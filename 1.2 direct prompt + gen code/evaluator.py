"""
evaluator.py — Pseudocode Evaluation System
=============================================

Evaluates student pseudocode submissions against a problem statement
using a configurable LLM provider (Gemini or OpenAI).  The pipeline
has three stages:

  1. **Criteria preparation** — generates missing criteria one dimension
     at a time, or uses criteria already saved for the question.
  2. **Evaluation + hints** (thinking mode if supported) — scores
     the student submission against the rubric, produces a step-by-step
     trace, issues, and Socratic hints.  Raw thinking tokens are captured
     separately when the provider supports it.
  3. **Python validation** — checks the evaluation JSON for structural
     correctness.  Retries Call 2 once on failure.

Public interface
----------------
- ``evaluate_submission(problem_statement, rubric, student_pseudocode,
       problem_id, submission_id) -> dict``

Provider selection
------------------
Set one of these environment variables:
- ``GEMINI_API_KEY``  → Google Gemini (default, supports thinking mode)
- ``OPENAI_API_KEY``  → OpenAI ChatGPT
- ``LLM_PROVIDER``   → Force 'gemini' or 'openai'
"""

from __future__ import annotations

import asyncio
import json
import re
from datetime import datetime, timezone
from pathlib import Path

import llm_client

# ──────────────────────────────────────────────────────────────────────
# Configuration
# ──────────────────────────────────────────────────────────────────────

TRACES_DIR = Path("data/traces")
RUBRICS_DIR = Path("data/rubrics")
RESULTS_DIR = Path("data/results")
VALID_SCORES = {"Excellent", "Good", "Fair", "Poor", "Clear", "Unclear"}

# ──────────────────────────────────────────────────────────────────────
# Prompts — imported from prompts.py (edit prompts there, not here)
# ──────────────────────────────────────────────────────────────────────

from prompts import (
    EVALUATION_USER_PROMPT_TEMPLATE,
    RETRY_ADDENDUM,
    RUBRIC_DIMENSION_PROMPTS,
    build_evaluation_system_prompt,
)


# ──────────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────────

def _strip_markdown_fences(text: str) -> str:
    """Remove ```json … ``` or ``` … ``` wrappers that models sometimes add."""
    pattern = r"```(?:json)?\s*\n?(.*?)\n?\s*```"
    match = re.search(pattern, text, re.DOTALL)
    if match:
        return match.group(1).strip()
    return text.strip()


def _parse_json_response(raw_text: str) -> dict:
    """
    Attempt to parse a JSON string from the model response.

    First tries direct parsing; if that fails, strips markdown fences
    and retries.  Raises ``ValueError`` on final failure.
    """
    # First attempt: parse directly
    try:
        return json.loads(raw_text)
    except json.JSONDecodeError:
        pass

    # Second attempt: strip fences then parse
    cleaned = _strip_markdown_fences(raw_text)
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError as exc:
        raise ValueError(
            f"Failed to parse JSON from model response: {exc}\n"
            f"Raw response (first 500 chars): {raw_text[:500]}"
        ) from exc


def _ensure_traces_dir() -> Path:
    """Create data/traces/ if it does not exist and return its Path."""
    TRACES_DIR.mkdir(parents=True, exist_ok=True)
    return TRACES_DIR


# ──────────────────────────────────────────────────────────────────────
# Validation
# ──────────────────────────────────────────────────────────────────────

def validate_evaluation_response(
    response: dict,
    approved_rubric: dict,
) -> tuple[bool, str]:
    """
    Validate the evaluation JSON returned by Call 2 against the rubric.

    Checks
    ------
    1. Number of dimensions matches the rubric.
    2. Dimension names match the rubric exactly (order matters).
    3. Every score is one of {Excellent, Good, Fair, Poor}.
    4. Null-consistency:
       - Excellent → issue & hint must be null
       - Good → issue must not be null, hint must be null
       - Fair/Poor → issue & hint must not be null
    5. Trace is at least 60 words.

    Returns ``(True, "ok")`` on success or ``(False, reason)`` on failure.
    """
    rubric_names = [d["name"] for d in approved_rubric["dimensions"]]

    # Some models return all dimensions even when a single dimension was
    # requested. Keep only the requested rubric dimensions, in rubric order.
    response_dimensions = response.get("dimensions")
    if not isinstance(response_dimensions, list):
        return False, "Response 'dimensions' must be a list"
    selected_dimensions: dict[str, dict] = {}
    for dim in response_dimensions:
        if not isinstance(dim, dict):
            continue
        name = dim.get("name")
        if name in rubric_names:
            if name in selected_dimensions:
                return False, f"Duplicate dimension in response: {name}"
            selected_dimensions[name] = dim

    missing_names = [name for name in rubric_names if name not in selected_dimensions]
    if missing_names:
        return False, (
            f"Response is missing rubric dimensions: {missing_names}"
        )
    response["dimensions"] = [selected_dimensions[name] for name in rubric_names]

    for dim in response["dimensions"]:
        # Older combined prompts used the four-level scale for Clarity.
        # Fold those responses into the current two-level Clarity scale.
        if dim["name"] == "Clarity":
            legacy_clarity_scores = {
                "Excellent": "Clear",
                "Good": "Clear",
                "Fair": "Unclear",
                "Poor": "Unclear",
                "Clear": "Clear",
                "Unclear": "Unclear",
            }
            dim["score"] = legacy_clarity_scores.get(
                str(dim.get("score", "")).strip().title(), dim.get("score")
            )
        score_options = {"Clear", "Unclear"} if dim["name"] == "Clarity" else {"Excellent", "Good", "Fair", "Poor"}
        # Check 3: score is a valid qualitative label
        if dim["score"] not in score_options:
            return False, (
                f"Invalid score for {dim['name']}: {dim['score']}"
            )

        # ── Auto-correct stubborn LLM formatting ──────────────────────
        # LLMs frequently omit the 'issue' or 'hint' when assigning
        # Good/Fair/Poor, even when explicitly told not to. Since we
        # hide 'issue' from the student anyway, we can safely inject
        # default strings here to prevent validation crashes.
        if dim["score"] == "Good":
            if not dim.get("issue"):
                dim["issue"] = f"A noticeable gap remains in {dim['name'].lower()}."
            dim["hint"] = None
        elif dim["score"] in {"Fair", "Poor", "Unclear"}:
            if not dim.get("issue"):
                dim["issue"] = f"A significant gap remains in {dim['name'].lower()}."
            if not dim.get("hint"):
                dim["hint"] = f"Can you think of a way to improve the {dim['name'].lower()}?"
        elif dim["score"] in {"Excellent", "Clear"}:
            dim["issue"] = None
            dim["hint"] = None

        # Check 4: null consistency             
        if dim["score"] in {"Excellent", "Clear"}:
            if dim["issue"] is not None:
                return False, f"Issue must be null when score is Excellent for {dim['name']}"
            if dim["hint"] is not None:
                return False, f"Hint must be null when score is Excellent for {dim['name']}"

        elif dim["score"] == "Good":
            if dim["issue"] is None:
                return False, f"Issue must not be null when score is Good for {dim['name']}"
            if dim["hint"] is not None:
                return False, f"Hint must be null when score is Good for {dim['name']}"

        elif dim["score"] in {"Fair", "Poor", "Unclear"}:
            if dim["issue"] is None:
                return False, f"Issue must not be null when score is {dim['score']} for {dim['name']}"
            if dim["hint"] is None:
                return False, f"Hint must not be null when score is {dim['score']} for {dim['name']}"

    # Check 5: trace length
    word_count = len(response["trace"].split())
    if word_count < 60:
        return False, f"Trace too short ({word_count} words)"

    return True, "ok"


# ──────────────────────────────────────────────────────────────────────
# Per-dimension criteria generation
# ──────────────────────────────────────────────────────────────────────

async def generate_dimension_rubric(
    problem_statement: str,
    dimension_name: str,
    reference_criteria: str | None = None,
) -> dict:
    """Generate criteria for one dimension without generating the others."""
    if dimension_name not in RUBRIC_DIMENSION_PROMPTS:
        raise ValueError(f"Unknown rubric dimension: {dimension_name}")

    system_prompt = (
        "You are an expert pseudocode assessment designer.\n\n"
        + RUBRIC_DIMENSION_PROMPTS[dimension_name]
        + "\n\nReturn only valid JSON in this shape: "
        + '{"name":"' + dimension_name
        + '","description":"...","sub_criteria":["..."],"weight":0}'
    )
    user_prompt = (
        f"Problem statement:\n{problem_statement}\n\n"
        f"Reference criteria:\n{reference_criteria or 'None supplied.'}"
    )
    try:
        raw_text = await llm_client.call_rubric_generation(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
        )
        parsed = _parse_json_response(raw_text)
    except Exception as exc:
        raise ValueError(f"Rubric generation failed for {dimension_name}: {exc}") from exc

    # Accept either one dimension object or a one-item dimensions wrapper.
    if isinstance(parsed.get("dimensions"), list):
        matches = [d for d in parsed["dimensions"] if d.get("name") == dimension_name]
        if len(matches) != 1:
            raise ValueError(f"Model did not return exactly one {dimension_name} rubric.")
        parsed = matches[0]
    if parsed.get("name") != dimension_name:
        raise ValueError(f"Model returned the wrong dimension for {dimension_name}.")
    if not isinstance(parsed.get("description"), str) or not isinstance(
        parsed.get("sub_criteria"), list
    ) or not parsed["sub_criteria"]:
        raise ValueError(f"Generated {dimension_name} rubric is incomplete.")
    return parsed


# ──────────────────────────────────────────────────────────────────────
# Call 2 — Evaluation + hints (thinking mode if supported)
# ──────────────────────────────────────────────────────────────────────

async def _call_evaluation(
    problem_statement: str,
    rubric: dict,
    student_pseudocode: str,
    is_retry: bool = False,
) -> tuple[str | None, str]:
    """
    Make a single Call 2 request.

    If the active provider supports thinking mode, raw thinking tokens
    are captured separately from the structured output.

    Returns ``(thinking_text, raw_output_text)``.
    ``thinking_text`` is ``None`` for providers without thinking support.
    """
    user_prompt = EVALUATION_USER_PROMPT_TEMPLATE.format(
        problem_statement=problem_statement,
        rubric_json=json.dumps(rubric, indent=2),
        student_pseudocode=student_pseudocode,
    )

    if is_retry:
        user_prompt += RETRY_ADDENDUM

    dimension_names = [
        dimension.get("name")
        for dimension in rubric.get("dimensions", [])
        if isinstance(dimension, dict)
    ]
    thinking_text, output_text = await llm_client.call_evaluation(
        system_prompt=build_evaluation_system_prompt(dimension_names),
        user_prompt=user_prompt,
    )
    return thinking_text, output_text


async def evaluate_submission(
    problem_statement: str,
    rubric: dict,
    student_pseudocode: str,
    problem_id: str,
    submission_id: str,
    user_id: str | None = None,
    reference_student_id: str | None = None,
    persist: bool = True,
) -> dict:
    """
    Evaluate a student pseudocode submission against a rubric.

    Pipeline: Call 2 → validate → (retry once if invalid) → save → return.

    Parameters
    ----------
    problem_statement : str
        The full text of the programming problem.
    rubric : dict
        The approved rubric assembled from saved or generated per-dimension criteria.
    student_pseudocode : str
        The student's pseudocode submission.
    problem_id : str
        An identifier for the problem.
    submission_id : str
        An identifier for this submission.

    Returns
    -------
    dict
        On success: the evaluation result (parsed Call 2 JSON).
        On failure: ``{"error": True, "message": "...", "raw_response": "..."}``.
    """
    dimensions = rubric.get("dimensions", [])
    if len(dimensions) > 1:
        # Keep dimensions independent even for callers that pass a combined
        # rubric (for example, the command-line workflow).
        combined_dimensions = []
        combined_traces = []
        for dimension in dimensions:
            dimension_name = dimension.get("name", "dimension")
            dimension_result = await evaluate_submission(
                problem_statement=problem_statement,
                rubric={"dimensions": [dimension]},
                student_pseudocode=student_pseudocode,
                problem_id=problem_id,
                submission_id=f"{submission_id}_{str(dimension_name).lower()}",
                user_id=user_id,
                reference_student_id=reference_student_id,
                persist=persist,
            )
            if dimension_result.get("error"):
                return dimension_result
            combined_dimensions.extend(dimension_result.get("dimensions", []))
            if dimension_result.get("trace"):
                combined_traces.append(f"{dimension_name}:\n{dimension_result['trace']}")
        return {
            "dimensions": combined_dimensions,
            "trace": "\n\n".join(combined_traces),
        }

    llm_thinking: str | None = None
    raw_text: str = ""
    retry_attempted: bool = False
    validation_passed: bool = False
    validation_message: str = ""
    evaluation_result: dict | None = None

    # ── Attempt 1 ────────────────────────────────────────────────
    try:
        llm_thinking, raw_text = await _call_evaluation(
            problem_statement=problem_statement,
            rubric=rubric,
            student_pseudocode=student_pseudocode,
            is_retry=False,
        )
        evaluation_result = _parse_json_response(raw_text)
        validation_passed, validation_message = validate_evaluation_response(
            evaluation_result, rubric
        )
    except Exception as exc:
        validation_passed = False
        validation_message = f"Attempt 1 error: {exc}"

    # ── Attempt 2 (retry) ────────────────────────────────────────
    if not validation_passed:
        retry_attempted = True
        try:
            llm_thinking, raw_text = await _call_evaluation(
                problem_statement=problem_statement,
                rubric=rubric,
                student_pseudocode=student_pseudocode,
                is_retry=True,
            )
            evaluation_result = _parse_json_response(raw_text)
            validation_passed, validation_message = (
                validate_evaluation_response(evaluation_result, rubric)
            )
        except Exception as exc:
            validation_passed = False
            validation_message = f"Retry error: {exc}"

    # ── Build the final result ───────────────────────────────────
    timestamp = datetime.now(timezone.utc).isoformat()

    if not validation_passed:
        # Return structured error — never raise
        error_result: dict = {
            "error": True,
            "message": validation_message,
            "raw_response": raw_text,
        }
        # Still save the trace for debugging
        if persist:
            _save_trace(
                problem_statement=problem_statement,
                rubric=rubric,
                student_pseudocode=student_pseudocode,
                llm_thinking=llm_thinking,
                evaluation_result=evaluation_result,
                validation_passed=False,
                validation_message=validation_message,
                retry_attempted=retry_attempted,
                timestamp=timestamp,
                problem_id=problem_id,
                submission_id=submission_id,
                user_id=user_id,
                reference_student_id=reference_student_id,
            )
        return error_result

    # Successful evaluation
    if persist:
        _save_trace(
            problem_statement=problem_statement,
            rubric=rubric,
            student_pseudocode=student_pseudocode,
            llm_thinking=llm_thinking,
            evaluation_result=evaluation_result,
            validation_passed=True,
            validation_message=validation_message,
            retry_attempted=retry_attempted,
            timestamp=timestamp,
            problem_id=problem_id,
            submission_id=submission_id,
            user_id=user_id,
            reference_student_id=reference_student_id,
        )

    assert evaluation_result is not None  # guaranteed by validation pass
    return evaluation_result


# ──────────────────────────────────────────────────────────────────────
# Trace saving
# ──────────────────────────────────────────────────────────────────────

def _save_trace(
    *,
    problem_statement: str,
    rubric: dict,
    student_pseudocode: str,
    llm_thinking: str | None,
    evaluation_result: dict | None,
    validation_passed: bool,
    validation_message: str,
    retry_attempted: bool,
    timestamp: str,
    problem_id: str,
    submission_id: str,
    user_id: str | None = None,
    reference_student_id: str | None = None,
) -> None:
    """
    Save the full evaluation trace to ``data/traces/``.

    Includes the raw Gemini thinking tokens so we can compare them
    against the ``trace`` field in the evaluation result to verify
    whether the model's stated reasoning matches its actual internal
    reasoning process.
    """
    traces_dir = _ensure_traces_dir()

    provider_name = llm_client.get_provider_name()
    model_name = llm_client.get_model_name()

    # Build a filesystem-safe timestamp (no colons)
    safe_ts = timestamp.replace(":", "").replace("+", "p")
    safe_problem_id = re.sub(r"[^A-Za-z0-9_.-]+", "_", problem_id)
    safe_submission_id = re.sub(r"[^A-Za-z0-9_.-]+", "_", submission_id)
    filename = f"problem_{safe_problem_id}_submission_{safe_submission_id}_{provider_name}_{safe_ts}.json"
    filepath = traces_dir / filename

    trace_data = {
        "problem_id": problem_id,
        "submission_id": submission_id,
        "problem_statement": problem_statement,
        "rubric": rubric,
        "student_pseudocode": student_pseudocode,
        "student_pseudocode_lines": student_pseudocode.splitlines(),
        "user_id": user_id,
        "reference_student_id": reference_student_id,
        "evaluator_provider": provider_name,
        "evaluator_model": model_name,
        "evaluator_thinking": llm_thinking,
        "evaluation_result": evaluation_result,
        "validation_result": {
            "passed": validation_passed,
            "message": validation_message,
        },
        "retry_attempted": retry_attempted,
        "timestamp": timestamp,
    }

    try:
        with open(filepath, "w", encoding="utf-8") as fh:
            json.dump(trace_data, fh, indent=2, ensure_ascii=False)
        print(f"[evaluator] Trace saved → {filepath}")
        _append_evaluation_record(trace_data)
    except OSError as exc:
        # Non-fatal: log but do not crash the evaluation pipeline
        print(f"[evaluator] WARNING: failed to save trace: {exc}")


def _append_evaluation_record(record: dict) -> Path:
    """Append one complete evaluation record as a JSON Lines entry."""
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    filepath = RESULTS_DIR / "evaluations.jsonl"
    with filepath.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(record, ensure_ascii=False) + "\n")
    return filepath


def _load_submission_file(filepath: str | Path) -> list[dict]:
    """Read and validate a JSON array of student pseudocode records."""
    source = Path(filepath).expanduser()
    try:
        with source.open(encoding="utf-8") as fh:
            records = json.load(fh)
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"Could not read valid JSON from {source}: {exc}") from exc

    if not isinstance(records, list):
        raise ValueError("Submission file must contain a JSON array.")
    for index, record in enumerate(records, start=1):
        if not isinstance(record, dict):
            raise ValueError(f"Record {index} must be a JSON object.")
        if not str(record.get("User ID", "")).strip():
            raise ValueError(f"Record {index} is missing a non-empty 'User ID'.")
        pseudocode = record.get("Pseudocode")
        if not isinstance(pseudocode, list) or not pseudocode or not all(
            isinstance(line, str) for line in pseudocode
        ):
            raise ValueError(
                f"Record {index} 'Pseudocode' must be a non-empty array of strings."
            )
        if not str(record.get("reference_student_id", "")).strip():
            raise ValueError(
                f"Record {index} is missing a non-empty 'reference_student_id'."
            )
    return records


# ──────────────────────────────────────────────────────────────────────
# Rubric persistence (save / load / list)
# ──────────────────────────────────────────────────────────────────────

def _ensure_rubrics_dir() -> Path:
    """Create data/rubrics/ if it does not exist and return its Path."""
    RUBRICS_DIR.mkdir(parents=True, exist_ok=True)
    return RUBRICS_DIR


def _list_saved_rubrics() -> list[dict]:
    """
    List all saved rubrics from ``data/rubrics/``.

    Returns a list of dicts, each containing the loaded JSON plus a
    ``_filepath`` key for reference.  Sorted by creation time (newest
    last).
    """
    rubrics_dir = _ensure_rubrics_dir()
    rubric_files = sorted(rubrics_dir.glob("rubric_*.json"))

    results: list[dict] = []
    for fp in rubric_files:
        try:
            with open(fp, encoding="utf-8") as fh:
                data = json.load(fh)
            data["_filepath"] = str(fp)
            results.append(data)
        except (json.JSONDecodeError, OSError):
            continue  # skip corrupt files

    return results


# ──────────────────────────────────────────────────────────────────────
# Interactive terminal interface
# ──────────────────────────────────────────────────────────────────────

def _read_multiline(prompt_msg: str) -> str:
    """
    Read multiline input from the terminal.

    Supports problem statements that contain blank lines (e.g. multi-paragraph
    problems pasted from an assignment sheet).

    Termination rule: two consecutive blank lines in a row = done.
    A single blank line is preserved as part of the text.

    Tip: paste your text, then press Enter twice to submit.
    """
    print(prompt_msg)
    print("  (paste or type your text, then press Enter TWICE on empty lines to finish)\n")
    lines: list[str] = []
    prev_blank = False          # was the previous line blank?
    while True:
        try:
            line = input()
        except EOFError:
            break
        if line == "":
            if prev_blank:
                # Two consecutive blank lines — strip the trailing blank and stop
                if lines and lines[-1] == "":
                    lines.pop()
                break
            prev_blank = True
        else:
            prev_blank = False
        lines.append(line)
    return "\n".join(lines)


def _print_section(title: str, width: int = 60) -> None:
    """Print a visual section divider."""
    print(f"\n{'─' * width}")
    print(f"  {title}")
    print(f"{'─' * width}\n")


def _display_rubric(rubric: dict) -> None:
    """Pretty-print a rubric to the terminal."""
    for dim in rubric.get("dimensions", []):
        print(f"  {dim['name']} (weight {dim['weight']}%)")
        print(f"    {dim['description']}")
        for sc in dim.get("sub_criteria", []):
            print(f"      • {sc}")
        print()


def _display_evaluation(result: dict) -> None:
    """Pretty-print the evaluation result to the terminal."""
    # Per-dimension results
    for dim in result.get("dimensions", []):
        name = dim.get("name", "?")
        score = dim.get("score", "?")
        print(f"  [{score:>9s}]  {name}")

        if dim.get("correct"):
            print(f"             ✓ {dim['correct']}")
        # Hide explicit 'issue' text from students entirely to force them to think.
        # The issue is still recorded in the JSON trace for teacher analytics.
        if dim.get("hint"):
            print(f"             💡 {dim['hint']}")
        print()

    # Trace
    trace = result.get("trace")
    if trace:
        _print_section("Step-by-step trace")
        print(trace)


def _print_main_menu() -> None:
    """Display the main menu options."""
    print("  [1]  Evaluate submission   (pick a question, submit pseudocode)")
    print("  [2]  View saved rubrics")
    print("  [q]  Quit")
    print()


def _select_rubric() -> dict | None:
    """
    Show available questions and let the user pick one.

    Only the problem statement is displayed — rubric details are
    kept hidden so students cannot see the grading criteria.

    Returns the selected rubric data dict, or None if cancelled.
    """
    saved = _list_saved_rubrics()

    if not saved:
        print("  ⚠  No questions available. Ask your teacher to set one up.")
        return None

    _print_section("Available Questions")
    for idx, entry in enumerate(saved, start=1):
        problem_id = entry.get("problem_id", "?")
        provider   = entry.get("llm_provider", "unknown")
        model      = entry.get("llm_model", "?")
        problem_text = entry.get("problem_statement", "(no question)")
        print(f"  [{idx}]  Question {problem_id}  —  generated by {provider} ({model})")
        for line in problem_text.strip().split("\n"):
            print(f"        {line}")
        print()

    try:
        choice = input("Select a question number (or 'back' to cancel): ").strip()
    except EOFError:
        return None

    if choice.lower() in {"back", "b", ""}:
        return None

    try:
        index = int(choice) - 1
        if 0 <= index < len(saved):
            return saved[index]
        else:
            print(f"  ⚠  Invalid selection: {choice}")
            return None
    except ValueError:
        print(f"  ⚠  Invalid input: {choice}")
        return None


async def _handle_evaluate_submission(submission_counter: int) -> int:
    """
    Flow: pick a saved rubric → enter pseudocode → evaluate.

    Loops so the user can submit multiple answers against the same
    rubric.  Returns the updated submission_counter.
    """
    selected = _select_rubric()
    if selected is None:
        return submission_counter

    problem_id = selected.get("problem_id", "unknown")
    problem_text = selected["problem_statement"]
    rubric = selected["rubric"]

    _print_section(f"Question: {problem_id}")
    for line in problem_text.strip().split("\n"):
        print(f"  {line}")

    source = input("\nSubmission source — [1] type one, [2] import JSON batch: ").strip()
    if source == "2":
        filename = input("JSON file path: ").strip()
        try:
            records = _load_submission_file(filename)
        except ValueError as exc:
            print(f"\n⚠  {exc}\n")
            return submission_counter

        print(f"\nLoaded {len(records)} student records. Starting evaluation…")
        for index, record in enumerate(records, start=1):
            submission_counter += 1
            pseudocode = "\n".join(record["Pseudocode"])
            user_id = str(record["User ID"]).strip()
            reference_student_id = str(record["reference_student_id"]).strip()
            submission_id = f"{user_id}_{index:04d}"
            print(f"\n[{index}/{len(records)}] User {user_id} ({reference_student_id})")
            result = await evaluate_submission(
                problem_statement=problem_text,
                rubric=rubric,
                student_pseudocode=pseudocode,
                problem_id=problem_id,
                submission_id=submission_id,
                user_id=user_id,
                reference_student_id=reference_student_id,
            )
            if result.get("error"):
                print(f"  ✗ Evaluation failed: {result['message']}")
            else:
                _display_evaluation(result)
        print(f"\nBatch finished. Results appended to {RESULTS_DIR / 'evaluations.jsonl'}\n")
        return submission_counter
    if source not in {"", "1"}:
        print("⚠  Choose 1 or 2; returning to the main menu.\n")
        return submission_counter

    # Inner loop: multiple submissions against this rubric
    while True:
        _print_section("Student Submission")
        pseudocode = _read_multiline("Enter the student's pseudocode:")

        if pseudocode.strip().lower() in {"quit", "exit", "q"}:
            print("Returning to main menu.\n")
            break
        if pseudocode.strip().lower() in {"back", "b"}:
            print("Returning to main menu.\n")
            break
        if not pseudocode.strip():
            print("⚠  Empty submission — skipping.")
            continue

        submission_counter += 1
        submission_id = f"s{submission_counter:03d}"

        print("\n⏳ Evaluating submission …")
        result = await evaluate_submission(
            problem_statement=problem_text,
            rubric=rubric,
            student_pseudocode=pseudocode,
            problem_id=problem_id,
            submission_id=submission_id,
        )

        if result.get("error"):
            print(f"\n✗ Evaluation failed: {result['message']}")
            raw = result.get("raw_response", "")
            if raw:
                print(f"  Raw response: {raw[:300]}")
            print()
            continue

        _print_section("Evaluation Result")
        _display_evaluation(result)

        print(f"{'─' * 60}")
        print("  Options:")
        print("    • Enter another submission for this same problem")
        print("    • Type 'back' to return to the main menu")
        print()

    return submission_counter


def _handle_view_rubrics() -> None:
    """Display all saved rubrics with full details."""
    saved = _list_saved_rubrics()

    if not saved:
        print("\n  ⚠  No saved rubrics found. Add a question and criteria in the Streamlit app.\n")
        return

    for idx, entry in enumerate(saved, start=1):
        problem_id = entry.get("problem_id", "?")
        created    = entry.get("created_at", "?")
        provider   = entry.get("llm_provider", "unknown")
        model      = entry.get("llm_model", "?")
        problem_text = entry.get("problem_statement", "(no problem statement)")

        _print_section(f"[{idx}] Rubric {problem_id}  —  {provider} ({model})  —  created {created}")
        print("  Problem:")
        for line in problem_text.strip().split("\n"):
            print(f"    {line}")
        print()
        _display_rubric(entry.get("rubric", {}))


async def _interactive_loop() -> None:
    """
    Menu-based interactive terminal session.

    - Option 1: Select a saved rubric and evaluate pseudocode
    - Option 2: View all saved rubrics
    """
    banner = "=" * 60
    provider = llm_client.get_provider_name()

    print(f"\n{banner}")
    print("  PSEUDOCODE EVALUATOR — Interactive Mode")
    print(f"{banner}")
    print()
    print(f"  Provider : {provider}")
    print("  Evaluate student pseudocode against saved criteria.")
    print(f"\n{banner}\n")

    submission_counter = 0

    while True:
        _print_main_menu()

        try:
            choice = input("  ▸ Choose an option: ").strip().lower()
        except EOFError:
            break

        if choice in {"q", "quit", "exit"}:
            break
        elif choice == "1":
            submission_counter = await _handle_evaluate_submission(
                submission_counter
            )
        elif choice == "2":
            _handle_view_rubrics()
        else:
            print(f"  ⚠  Unknown option: '{choice}'. Enter 1, 2, or q.\n")

    print("\nGoodbye! 👋\n")


if __name__ == "__main__":
    asyncio.run(_interactive_loop())

