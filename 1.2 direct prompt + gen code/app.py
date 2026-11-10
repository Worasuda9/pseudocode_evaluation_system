"""Streamlit interface for managing questions, criteria, evaluations, and results."""

from __future__ import annotations

import asyncio
import csv
import hashlib
import io
import json
import os
import re
from pathlib import Path

import streamlit as st

import evaluator
import code_assisted_evaluator
import llm_client


DATA_DIR = Path("data")
QUESTION_FILE = DATA_DIR / "questions.json"
CRITERIA_FILE = DATA_DIR / "criteria.json"
RESULTS_FILE = DATA_DIR / "results" / "submissions.jsonl"
RESULTS_DIR = DATA_DIR / "results"
CODE_ASSISTED_LEGACY_RESULTS_FILE = RESULTS_DIR / "code_assisted_evaluations.jsonl"
DIMENSIONS = ("Correctness", "Completeness", "Clarity")
WEIGHTS = {"Correctness": 45, "Completeness": 35, "Clarity": 20}


def _read_json(path: Path, default):
    try:
        with path.open(encoding="utf-8") as fh:
            return json.load(fh)
    except FileNotFoundError:
        return default
    except json.JSONDecodeError as exc:
        st.error(f"Could not read {path}: {exc}")
        return default


def _write_json(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)


def _questions() -> list[dict]:
    data = _read_json(QUESTION_FILE, [])
    saved = data if isinstance(data, list) else []
    known = {str(q.get("question_number")) for q in saved if isinstance(q, dict)}
    # Make the existing Question/*.md collection available immediately.
    questions_dir = Path("Question")
    if questions_dir.is_dir():
        for path in sorted((*questions_dir.glob("*.md"), *questions_dir.glob("*.txt"))):
            if path.stem not in known:
                saved.append({"question_number": path.stem, "question_text": path.read_text(encoding="utf-8")})
                known.add(path.stem)
    return saved


def _criteria() -> dict:
    data = _read_json(CRITERIA_FILE, {})
    return data if isinstance(data, dict) else {}


def _question_navigator(label: str, options: list[str], key: str) -> str:
    """Show a question selector with previous/next buttons."""
    if key not in st.session_state or st.session_state[key] not in options:
        st.session_state[key] = options[0]

    current_index = options.index(st.session_state[key])
    previous, selector, following = st.columns([1, 5, 1])
    if previous.button("← Previous", key=f"{key}_previous", disabled=current_index == 0):
        st.session_state[key] = options[current_index - 1]
    if following.button(
        "Next →", key=f"{key}_next", disabled=current_index == len(options) - 1
    ):
        st.session_state[key] = options[current_index + 1]
    selector.selectbox(label, options, key=key)
    return st.session_state[key]


def _results() -> list[dict]:
    records = []
    result_files = sorted(RESULTS_DIR.glob("submission_*.jsonl")) if RESULTS_DIR.exists() else []
    # Continue showing records from the former all-questions file.
    if RESULTS_FILE.exists():
        result_files.append(RESULTS_FILE)
    for result_file in result_files:
        with result_file.open(encoding="utf-8") as fh:
            for line_number, line in enumerate(fh, 1):
                try:
                    record = json.loads(line)
                    if isinstance(record, dict):
                        records.append(record)
                except json.JSONDecodeError:
                    st.warning(f"Ignoring invalid saved result in {result_file} on line {line_number}.")

    # Include prior app runs saved in the previous detailed JSONL format.
    questions = _questions()
    text_to_number = {q.get("question_text", ""): q.get("question_number", "") for q in questions}
    hash_to_number = {
        "question_" + hashlib.sha256(q.get("question_text", "").encode("utf-8")).hexdigest()[:10]: q.get("question_number", "")
        for q in questions
    }
    legacy_path = DATA_DIR / "results" / "web_runs.jsonl"
    if legacy_path.exists():
        with legacy_path.open(encoding="utf-8") as fh:
            for line in fh:
                try:
                    old = json.loads(line)
                    result = old.get("evaluation_result")
                    if not isinstance(result, dict):
                        continue
                    records.append({
                        "user_id": old.get("user_id"),
                        "question_number": text_to_number.get(old.get("problem_statement", ""), "Unknown"),
                        "pseudocode": old.get("student_pseudocode", ""),
                        "evaluation_result": result,
                    })
                except (json.JSONDecodeError, AttributeError):
                    continue
    legacy_traces = DATA_DIR / "results" / "evaluations.jsonl"
    grouped = {}
    if legacy_traces.exists():
        with legacy_traces.open(encoding="utf-8") as fh:
            for line in fh:
                try:
                    old = json.loads(line)
                    result = old.get("evaluation_result")
                    if not isinstance(result, dict):
                        continue
                    problem_id = old.get("problem_id", "")
                    key = (old.get("user_id"), problem_id, old.get("student_pseudocode", ""))
                    group = grouped.setdefault(key, {
                        "user_id": old.get("user_id"),
                        "question_number": hash_to_number.get(problem_id, problem_id or "Unknown"),
                        "pseudocode": old.get("student_pseudocode", ""),
                        "evaluation_result": {"dimensions": [], "trace": ""},
                    })
                    group["evaluation_result"]["dimensions"].extend(result.get("dimensions", []))
                    if result.get("trace"):
                        group["evaluation_result"]["trace"] += result["trace"] + "\n\n"
                except (json.JSONDecodeError, AttributeError):
                    continue
    records.extend(grouped.values())

    # Keep every saved evaluation. Re-evaluating the same pseudocode for the
    # same user creates a new JSONL row and must remain visible as its own run.
    return records


def _append_result(record: dict) -> Path:
    # Keep the result row limited to the four requested fields.
    compact = {
        "user_id": record["user_id"],
        "question_number": record["question_number"],
        "pseudocode": record["pseudocode"],
        "evaluation_result": record["evaluation_result"],
    }
    safe_question_number = re.sub(
        r"[^A-Za-z0-9_.-]+", "_", str(compact["question_number"])
    ).strip("._-") or "unknown"
    output_file = RESULTS_DIR / f"submission_{safe_question_number}.jsonl"
    output_file.parent.mkdir(parents=True, exist_ok=True)
    with output_file.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(compact, ensure_ascii=False) + "\n")
    return output_file


def _last_five_scores_csv(records: list[dict]) -> tuple[str, int]:
    """Build a score-only CSV from the latest five evaluations per question."""
    grouped: dict[str, list[dict]] = {}
    for record in records:
        question_number = str(record.get("question_number", ""))
        grouped.setdefault(question_number, []).append(record)

    output = io.StringIO(newline="")
    writer = csv.writer(output)
    writer.writerow(["Question", "User ID", "Correctness", "Completeness", "Clarity"])
    row_count = 0

    for question_number in sorted(grouped):
        safe_question_number = re.sub(
            r"[^A-Za-z0-9_.-]+", "_", question_number
        ).strip("._-") or "unknown"
        question_file = RESULTS_DIR / f"submission_{safe_question_number}.jsonl"
        question_records = []
        if question_file.is_file():
            with question_file.open(encoding="utf-8") as fh:
                for line in fh:
                    try:
                        saved_record = json.loads(line)
                        if isinstance(saved_record, dict):
                            question_records.append(saved_record)
                    except json.JSONDecodeError:
                        continue
        # Per-question JSONL files preserve evaluation order. Use older
        # imported records only when no per-question history exists.
        recent = (question_records or grouped[question_number])[-5:]
        for record in recent:
            evaluation_result = record.get("evaluation_result")
            dimensions = (
                evaluation_result.get("dimensions", [])
                if isinstance(evaluation_result, dict)
                else []
            )
            if not isinstance(dimensions, list):
                dimensions = []
            scores = {
                dimension.get("name"): dimension.get("score", "")
                for dimension in dimensions
                if isinstance(dimension, dict)
            }
            writer.writerow([
                question_number.replace("_", "-"),
                record.get("user_id", ""),
                scores.get("Correctness", ""),
                scores.get("Completeness", ""),
                scores.get("Clarity", ""),
            ])
            row_count += 1

    return output.getvalue(), row_count


def _read_uploaded_text(uploaded_file) -> str:
    try:
        return uploaded_file.getvalue().decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise ValueError("Please upload a UTF-8 text file.") from exc


def _load_student_batch(uploaded_file) -> list[dict]:
    try:
        records = json.loads(_read_uploaded_text(uploaded_file))
    except (ValueError, json.JSONDecodeError) as exc:
        raise ValueError(f"Could not parse pseudocode JSON: {exc}") from exc
    return _validate_student_batch(records)


def _validate_student_batch(records: object) -> list[dict]:
    if not isinstance(records, list):
        raise ValueError("The pseudocode file must contain a JSON array.")
    for index, record in enumerate(records, 1):
        if not isinstance(record, dict):
            raise ValueError(f"Record {index} must be an object.")
        if not str(record.get("User ID", "")).strip():
            raise ValueError(f"Record {index} is missing 'User ID'.")
        lines = record.get("Pseudocode")
        if not isinstance(lines, list) or not lines or not all(isinstance(x, str) for x in lines):
            raise ValueError(f"Record {index} needs 'Pseudocode' as a non-empty array of strings.")
    return records


def _load_student_batch_path(path: Path) -> list[dict]:
    try:
        records = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"Could not read pseudocode dataset {path}: {exc}") from exc
    return _validate_student_batch(records)


def _canonical_question_file_stem(path: Path, suffix: str) -> str:
    stem = path.stem.lower().replace("-", "_")
    if stem.startswith("itds120_"):
        stem = stem[len("itds120_"):]
    if stem.endswith(suffix):
        stem = stem[:-len(suffix)]
    return stem.strip("_")


def _find_pseudocode_dataset(question_number: str) -> Path | None:
    target = question_number.lower().replace("-", "_")
    pseudocode_dir = Path("Pseudocode")
    if not pseudocode_dir.is_dir():
        return None
    return next((
        path for path in sorted(pseudocode_dir.glob("*.json"))
        if _canonical_question_file_stem(path, "_pseudocode") == target
    ), None)


def _find_code_assisted_files(question_number: str) -> tuple[Path | None, Path | None]:
    target = question_number.lower().replace("-", "_")
    pseudocode_dir = Path("Pseudocode")
    code_dir = Path("Code from Pseudocode")
    pseudocode_file = next((
        path for path in sorted(pseudocode_dir.glob("*.json"))
        if _canonical_question_file_stem(path, "_pseudocode") == target
    ), None)
    code_file = next((
        path for path in sorted(code_dir.glob("*.json"))
        if _canonical_question_file_stem(path, "_gencode") == target
    ), None)
    return pseudocode_file, code_file


def _load_code_assisted_pair(question_number: str) -> tuple[list[dict], Path, Path, int, int]:
    pseudocode_file, code_file = _find_code_assisted_files(question_number)
    if not pseudocode_file or not code_file:
        raise ValueError(
            "Could not find both matching files in Pseudocode/ and Code from Pseudocode/."
        )
    try:
        pseudocode_rows = json.loads(pseudocode_file.read_text(encoding="utf-8"))
        code_rows = json.loads(code_file.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"Could not read the question's JSON files: {exc}") from exc
    if not isinstance(pseudocode_rows, list) or not isinstance(code_rows, list):
        raise ValueError("Both files must contain JSON arrays.")

    def index_rows(rows: list, content_key: str, path: Path) -> dict[str, dict]:
        indexed = {}
        for row_number, row in enumerate(rows, 1):
            if not isinstance(row, dict):
                raise ValueError(f"{path.name}: record {row_number} must be an object.")
            user_id = str(row.get("User ID", "")).strip()
            content = row.get(content_key)
            if not user_id:
                raise ValueError(f"{path.name}: record {row_number} is missing User ID.")
            if not isinstance(content, list) or not content or not all(isinstance(item, str) for item in content):
                raise ValueError(f"{path.name}: {user_id} needs {content_key} as a non-empty string array.")
            key = user_id.zfill(3) if user_id.isdigit() else user_id.casefold()
            if key in indexed:
                raise ValueError(f"{path.name}: duplicate User ID {user_id}.")
            indexed[key] = {"user_id": user_id, "text": "\n".join(content)}
        return indexed

    pseudocode_by_user = index_rows(pseudocode_rows, "Pseudocode", pseudocode_file)
    code_by_user = index_rows(code_rows, "Pseudocode to Code", code_file)
    matched = []
    # Keep pseudocode source order so the all-questions mode can take the
    # final five rows as the latest five submissions in that batch.
    for key in pseudocode_by_user:
        if key not in code_by_user:
            continue
        pseudocode = pseudocode_by_user[key]
        generated_code = code_by_user[key]
        matched.append({
            "user_id": pseudocode["user_id"],
            "pseudocode": pseudocode["text"],
            "generated_code": generated_code["text"],
        })
    if not matched:
        raise ValueError("The files contain no matching User IDs.")
    return (
        matched,
        pseudocode_file,
        code_file,
        len(pseudocode_by_user.keys() - code_by_user.keys()),
        len(code_by_user.keys() - pseudocode_by_user.keys()),
    )


def _append_code_assisted_result(record: dict) -> Path:
    safe_question_number = re.sub(
        r"[^A-Za-z0-9_.-]+", "_", str(record["question_number"])
    ).strip("._-") or "unknown"
    result_file = RESULTS_DIR / f"code_assisted_submission_{safe_question_number}.jsonl"
    result_file.parent.mkdir(parents=True, exist_ok=True)
    with result_file.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(record, ensure_ascii=False) + "\n")
    return result_file


def _code_assisted_results(question_number: str | None = None) -> list[dict]:
    """Read legacy combined results followed by the newer per-question files."""
    records = []
    result_files = []
    if CODE_ASSISTED_LEGACY_RESULTS_FILE.is_file():
        result_files.append(CODE_ASSISTED_LEGACY_RESULTS_FILE)
    result_files.extend(sorted(RESULTS_DIR.glob("code_assisted_submission_*.jsonl")))
    for result_file in result_files:
        with result_file.open(encoding="utf-8") as fh:
            for line_number, line in enumerate(fh, 1):
                try:
                    record = json.loads(line)
                    if not isinstance(record, dict):
                        continue
                    if question_number is None or record.get("question_number") == question_number:
                        records.append(record)
                except json.JSONDecodeError:
                    st.warning(f"Ignoring invalid code-assisted result in {result_file} on line {line_number}.")
    return records


def _code_assisted_scores_csv(
    records: list[dict], question_number: str | None = None
) -> tuple[str, int]:
    output = io.StringIO(newline="")
    writer = csv.writer(output)
    writer.writerow(["Question", "User ID", "Correctness", "Completeness", "Clarity"])
    grouped: dict[str, list[dict]] = {}
    for record in records:
        question = str(record.get("question_number", ""))
        if question_number is None or question == question_number:
            grouped.setdefault(question, []).append(record)

    row_count = 0
    for question in sorted(grouped):
        for record in grouped[question][-5:]:
            result = record.get("evaluation_result", {})
            dimensions = result.get("dimensions", []) if isinstance(result, dict) else []
            scores = {
                row.get("name"): row.get("score", "")
                for row in dimensions
                if isinstance(row, dict)
            }
            writer.writerow([
                question.replace("_", "-"),
                record.get("user_id", ""),
                scores.get("Correctness", ""),
                scores.get("Completeness", ""),
                scores.get("Clarity", ""),
            ])
            row_count += 1
    return output.getvalue(), row_count


def _render_code_assisted_evaluation() -> None:
    st.title("1.2 & 3.2 Generated code Eval")
    st.write(
        "Evaluates pseudocode with its generated Python code as secondary evidence. "
    )
    questions = _questions()
    if not questions:
        st.info("Save or add a question first on the Question page.")
        return
    by_number = {q["question_number"]: q for q in questions}
    all_questions_label = "All questions · latest 5 submissions per question"
    question_scope = st.selectbox(
        "Submission set",
        [all_questions_label, *by_number],
        key="code_assisted_question_scope",
    )
    all_questions = question_scope == all_questions_label
    submissions_to_evaluate = []

    if all_questions:
        missing_pairs = []
        for number, question in by_number.items():
            try:
                rows, _, _, _, _ = _load_code_assisted_pair(number)
                submissions_to_evaluate.extend(
                    {**row, "question_number": number, "question_text": question["question_text"]}
                    for row in rows[-5:]
                )
            except ValueError as exc:
                missing_pairs.append(f"{number}: {exc}")
        st.info(
            f"Loaded the last five matched rows from each question's files: "
            f"{len(submissions_to_evaluate)} submission(s) across {len(by_number)} question(s)."
        )
        if missing_pairs:
            with st.expander(f"Questions skipped ({len(missing_pairs)})"):
                for message in missing_pairs:
                    st.caption(message)
    else:
        question_number = question_scope
        question = by_number[question_number]
        with st.expander("Question text"):
            st.markdown(question["question_text"])
        try:
            rows, pseudocode_file, code_file, pseudocode_only, code_only = _load_code_assisted_pair(question_number)
            st.caption(
                f"{len(rows)} matched by User ID · {pseudocode_only} pseudocode-only row(s) and "
                f"{code_only} code-only row(s) excluded. Sources: `{pseudocode_file}` and `{code_file}`."
            )
            submissions_to_evaluate = [
                {**row, "question_number": question_number, "question_text": question["question_text"]}
                for row in rows
            ]
        except ValueError as exc:
            st.error(str(exc))

    if submissions_to_evaluate:
        display_names = [
            f"{row['question_number']} · User {row['user_id']}"
            for row in submissions_to_evaluate
        ]
        selection_key = f"code_assisted_submissions_{question_scope}"
        if all_questions and st.button(
            "Select all loaded submissions",
            key="select_all_code_assisted_latest",
        ):
            st.session_state[selection_key] = display_names
        selected_names = st.multiselect(
            "Select student submissions",
            options=display_names,
            default=[],
            key=selection_key,
            help="The all-questions option loads the last five matched rows per question. Select which ones to evaluate.",
        )
        selected_rows = [row for name, row in zip(display_names, submissions_to_evaluate) if name in selected_names]
        criteria_by_question = _criteria()
        for dimension in DIMENSIONS:
            if any(criteria_by_question.get(row["question_number"], {}).get(dimension, "").strip() for row in selected_rows):
                st.caption(f"Using saved {dimension} criteria where available.")

        if st.button(
            f"Evaluate {len(selected_rows)} selected submission(s)",
            type="primary",
            disabled=not selected_rows,
            key="run_code_assisted_evaluation",
        ):
            for submission in selected_rows:
                user_id = submission["user_id"]
                question_number = submission["question_number"]
                question_text = submission["question_text"]
                criteria = criteria_by_question.get(question_number, {})
                st.subheader(f"{question_number} · User {user_id}")
                try:
                    with st.spinner(f"Evaluating {question_number} · User {user_id}…"):
                        result = asyncio.run(code_assisted_evaluator.evaluate_submission(
                            problem_statement=question_text,
                            pseudocode=submission["pseudocode"],
                            generated_code=submission["generated_code"],
                            criteria={name: criteria.get(name, "") for name in DIMENSIONS},
                        ))
                    saved_record = {
                        "user_id": user_id,
                        "question_number": question_number,
                        "pseudocode": submission["pseudocode"].splitlines(),
                        "generated_code": submission["generated_code"].splitlines(),
                        "evaluation_result": result,
                    }
                    result_file = _append_code_assisted_result(saved_record)
                    _display_evaluation(result)
                    st.success(f"Saved separately to `{result_file}`.")
                except Exception as exc:
                    st.error(f"Evaluation failed for {user_id}: {exc}")

    st.subheader("Saved code-assisted results")
    saved_rows = _code_assisted_results() if all_questions else _code_assisted_results(question_scope)
    if not saved_rows:
        st.info("No code-assisted evaluations saved yet.")
        return
    score_csv, score_count = _code_assisted_scores_csv(
        saved_rows, None if all_questions else question_scope
    )
    safe_question_number = "all_questions" if all_questions else re.sub(
        r"[^A-Za-z0-9_.-]+", "_", question_scope
    ).strip("._-") or "unknown"
    st.download_button(
        "Download latest 5 scores per question (CSV)" if all_questions
        else "Download latest 5 scores for this question (CSV)",
        data=score_csv,
        file_name=f"code_assisted_scores_{safe_question_number}.csv",
        mime="text/csv",
        disabled=score_count == 0,
        key=f"download_code_assisted_{safe_question_number}",
    )
    for index, row in enumerate(reversed(saved_rows), 1):
        with st.expander(f"{row.get('question_number', '?')} · User {row.get('user_id', '?')} · run #{index}"):
            st.markdown("**Pseudocode**")
            st.code("\n".join(row.get("pseudocode", [])))
            st.markdown("**Generated Python code**")
            st.code("\n".join(row.get("generated_code", [])), language="python")
            _display_evaluation(row.get("evaluation_result", {}))


def _make_rubric(question_text: str, existing: dict[str, str], reference_criteria: str = "") -> dict:
    async def generate_missing() -> dict:
        dimensions = []
        for name in DIMENSIONS:
            criteria_text = existing.get(name, "").strip()
            if criteria_text:
                dimension = {
                    "name": name,
                    "description": f"Saved criteria for {name.lower()}.",
                    "sub_criteria": [criteria_text],
                    "weight": WEIGHTS[name],
                }
            else:
                dimension = await evaluator.generate_dimension_rubric(
                    question_text, name, reference_criteria=reference_criteria or None
                )
            dimensions.append(dimension)
        return {"dimensions": dimensions}

    return asyncio.run(generate_missing())


def _evaluate_submission(question: dict, rubric: dict, pseudocode: str,
                         user_id: str, index: int) -> dict:
    async def evaluate_dimensions() -> dict:
        scored_dimensions = []
        traces = []
        question_number = question["question_number"]
        safe_question_number = re.sub(r"[^A-Za-z0-9_.-]+", "_", question_number)
        safe_user_id = re.sub(r"[^A-Za-z0-9_.-]+", "_", user_id)
        for dimension in rubric["dimensions"]:
            result = await evaluator.evaluate_submission(
                problem_statement=question["question_text"],
                rubric={"dimensions": [dimension]},
                student_pseudocode=pseudocode,
                problem_id=safe_question_number,
                submission_id=f"{safe_user_id}_{index}_{dimension['name'].lower()}",
                user_id=user_id,
                persist=False,
            )
            if result.get("error"):
                raise RuntimeError(f"{dimension['name']}: {result.get('message', 'evaluation failed')}")
            scored_dimensions.extend(result.get("dimensions", []))
            if result.get("trace"):
                traces.append(f"{dimension['name']}:\n{result['trace']}")
        return {"dimensions": scored_dimensions, "trace": "\n\n".join(traces)}

    return asyncio.run(evaluate_dimensions())


def _display_evaluation(result: dict) -> None:
    dimensions = result.get("dimensions", [])
    columns = st.columns(max(1, len(dimensions)))
    for column, dimension in zip(columns, dimensions):
        with column:
            st.metric(dimension.get("name", "Dimension"), dimension.get("score", "—"))
            if dimension.get("correct"):
                st.markdown(f"**What worked**\n\n{dimension['correct']}")
            if dimension.get("issue"):
                st.markdown(f"**Area to consider**\n\n{dimension['issue']}")
            if dimension.get("hint"):
                st.info(dimension["hint"])
    with st.expander("Evaluation trace"):
        st.write(result.get("trace", "No trace returned."))


def _render_questions() -> None:
    st.title("Questions")
    st.write("Add a question once, then select it later from the Evaluation page.")
    upload = st.file_uploader("Upload a question (.md or .txt)", type=["md", "txt"], key="question_upload")
    if upload:
        content_hash = hashlib.sha256(upload.getvalue()).hexdigest()
        if st.session_state.get("question_upload_hash") != content_hash:
            try:
                st.session_state["question_editor"] = _read_uploaded_text(upload)
                st.session_state["question_upload_hash"] = content_hash
            except ValueError as exc:
                st.error(str(exc))
    questions = _questions()
    number = st.text_input("Question number", placeholder="e.g. cp1_q1")
    question_text = st.text_area("Question text", key="question_editor", height=250)
    if st.button("Save question", type="primary"):
        if not question_text.strip():
            st.error("Enter question text or upload a file first.")
        else:
            number = number.strip() or f"Q{len(questions) + 1:03d}"
            existing = next((q for q in questions if q.get("question_number") == number), None)
            if existing:
                existing["question_text"] = question_text.strip()
            else:
                questions.append({"question_number": number, "question_text": question_text.strip()})
            _write_json(QUESTION_FILE, questions)
            st.success(f"Saved question {number}.")
            st.rerun()

    st.subheader("Saved questions")
    if not questions:
        st.info("No questions saved yet.")
    for question in questions:
        with st.expander(question.get("question_number", "Question")):
            st.markdown(question.get("question_text", ""))


def _render_criteria() -> None:
    st.title("Criteria")
    questions = _questions()
    if not questions:
        st.info("Save a question before adding criteria.")
        return
    labels = {q["question_number"]: q for q in questions}
    question_number = _question_navigator("Question", list(labels), "criteria_question_number")
    saved = _criteria()
    saved_for_question = saved.get(question_number, {})
    st.write("Enter criteria separately. Leave a dimension empty to generate it during evaluation.")

    legacy_file = Path("Criteria") / f"criteria_{question_number}.txt"
    legacy_correctness = legacy_file.read_text(encoding="utf-8") if legacy_file.is_file() else ""
    reference_key = f"criteria_reference_{question_number}"
    if reference_key not in st.session_state:
        st.session_state[reference_key] = saved_for_question.get("_reference", "")
    reference_upload = st.file_uploader(
        "Upload shared/brief reference criteria (.txt or .md)",
        type=["txt", "md"], key=f"criteria_reference_upload_{question_number}",
    )
    if reference_upload:
        upload_hash = hashlib.sha256(reference_upload.getvalue()).hexdigest()
        if st.session_state.get(f"criteria_reference_hash_{question_number}") != upload_hash:
            try:
                st.session_state[reference_key] = _read_uploaded_text(reference_upload)
                st.session_state[f"criteria_reference_hash_{question_number}"] = upload_hash
            except ValueError as exc:
                st.error(str(exc))
    shared_reference = st.text_area(
        "Shared or brief reference criteria (optional)",
        key=reference_key, height=140,
        help="Used to guide generation for any dimension without its own saved criteria.",
    )

    typed_values = {}
    for dimension in DIMENSIONS:
        key = f"criteria_{question_number}_{dimension.lower()}"
        existing = saved_for_question.get(dimension, "")
        if dimension == "Correctness" and not existing.strip():
            existing = legacy_correctness
        if key not in st.session_state or (
            dimension == "Correctness" and not st.session_state[key].strip() and existing.strip()
        ):
            st.session_state[key] = existing
        with st.expander(dimension, expanded=True):
            uploaded = st.file_uploader(
                f"Upload {dimension} criteria (.txt or .md)",
                type=["txt", "md"],
                key=f"criteria_upload_{question_number}_{dimension.lower()}",
            )
            upload_key = f"criteria_file_loaded_{key}"
            upload_hash = hashlib.sha256(uploaded.getvalue()).hexdigest() if uploaded else None
            if uploaded and st.session_state.get(upload_key) != upload_hash:
                try:
                    st.session_state[key] = _read_uploaded_text(uploaded)
                    st.session_state[upload_key] = upload_hash
                except ValueError as exc:
                    st.error(str(exc))
            typed_values[dimension] = st.text_area(
                f"{dimension} criteria", key=key, height=150
            )

    if st.button("Save criteria", type="primary"):
        saved[question_number] = {name: value.strip() for name, value in typed_values.items()}
        saved[question_number]["_reference"] = shared_reference.strip()
        _write_json(CRITERIA_FILE, saved)
        st.success(f"Criteria saved for {question_number}.")
    st.subheader("Saved criteria")
    for qnum, dimensions in saved.items():
        if qnum in labels:
            with st.expander(qnum):
                if dimensions.get("_reference"):
                    st.markdown("**Shared reference criteria**")
                    st.write(dimensions["_reference"])
                for name in DIMENSIONS:
                    st.markdown(f"**{name}**")
                    st.write(dimensions.get(name, "No criteria saved; generated when evaluated."))
    legacy_dir = Path("Criteria")
    if legacy_dir.is_dir():
        for qnum in labels:
            legacy_file = legacy_dir / f"criteria_{qnum}.txt"
            if legacy_file.is_file() and not saved.get(qnum, {}).get("Correctness", "").strip():
                with st.expander(f"{qnum} · existing Correctness criteria file"):
                    st.caption("This file is used as the Correctness criteria.")
                    st.code(legacy_file.read_text(encoding="utf-8"))


def _render_evaluation() -> None:
    st.title("1.1 & 3.1 Direct prompt Eval")
    questions = _questions()
    if not questions:
        st.info("Save a question first on the Questions page.")
        return
    by_number = {q["question_number"]: q for q in questions}
    question_number = st.selectbox("Choose a question", list(by_number))
    question = by_number[question_number]
    with st.expander("Question text"):
        st.markdown(question["question_text"])

    saved_criteria = _criteria().get(question_number, {})
    reference_criteria = saved_criteria.get("_reference", "")
    legacy_file = Path("Criteria") / f"criteria_{question_number}.txt"
    if not saved_criteria.get("Correctness", "").strip() and legacy_file.is_file():
        saved_criteria = {**saved_criteria, "Correctness": legacy_file.read_text(encoding="utf-8")}
    missing = [name for name in DIMENSIONS if not saved_criteria.get(name, "").strip()]
    if missing:
        st.info("Criteria will be generated for: " + ", ".join(missing))
    else:
        st.success("Saved criteria found for all three dimensions.")

    dataset_file = _find_pseudocode_dataset(question_number)
    input_sources = (["Pseudocode folder"] if dataset_file else []) + ["Upload JSON", "Manual entry"]
    if not dataset_file:
        st.info(f"No matching pseudocode dataset was found in `Pseudocode/` for {question_number}.")
    input_source = st.radio(
        "Submission source",
        input_sources,
        horizontal=True,
        key=f"evaluation_source_{question_number}",
    )

    if input_source == "Pseudocode folder":
        try:
            submissions = [
                {"user_id": str(row["User ID"]).strip(), "pseudocode": "\n".join(row["Pseudocode"])}
                for row in _load_student_batch_path(dataset_file)
            ]
            st.success(f"Loaded {len(submissions)} submissions from `{dataset_file}`.")
        except ValueError as exc:
            submissions = []
            st.error(str(exc))
    elif input_source == "Upload JSON":
        upload = st.file_uploader(
            "Upload pseudocode JSON batch", type=["json"], key="evaluation_batch"
        )
        if upload:
            try:
                submissions = [
                    {"user_id": str(row["User ID"]).strip(), "pseudocode": "\n".join(row["Pseudocode"])}
                    for row in _load_student_batch(upload)
                ]
                st.success(f"Loaded {len(submissions)} submissions.")
            except ValueError as exc:
                submissions = []
                st.error(str(exc))
        else:
            submissions = []
    else:
        user_id = st.text_input("User ID", key="evaluation_user_id")
        pseudocode_text = st.text_area("Pseudocode", height=200, key="evaluation_pseudocode")
        submissions = ([{"user_id": user_id.strip(), "pseudocode": pseudocode_text.strip()}]
                       if pseudocode_text.strip() and user_id.strip() else [])
        if pseudocode_text.strip() and not user_id.strip():
            st.caption("Enter a User ID to enable evaluation.")

    if st.button("Evaluate", type="primary", disabled=not submissions):
        try:
            with st.spinner("Preparing missing criteria…"):
                rubric = _make_rubric(
                    question["question_text"], saved_criteria, reference_criteria
                )
            # Cache generated rubric criteria so future evaluations reuse them.
            saved = _criteria()
            updated_criteria = {
                dim["name"]: "\n".join(dim.get("sub_criteria", []))
                for dim in rubric["dimensions"]
            }
            updated_criteria["_reference"] = reference_criteria
            saved[question_number] = updated_criteria
            _write_json(CRITERIA_FILE, saved)
            st.success("Rubric ready. Evaluating each dimension independently.")
        except Exception as exc:
            st.error(f"Could not prepare criteria: {exc}")
            return

        for index, submission in enumerate(submissions, 1):
            user_id = submission["user_id"]
            pseudocode = submission["pseudocode"]
            st.subheader(f"User {user_id}")
            try:
                with st.spinner(f"Evaluating {user_id}…"):
                    result = _evaluate_submission(question, rubric, pseudocode, user_id, index)
                result_path = _append_result({
                    "user_id": user_id,
                    "question_number": question_number,
                    "pseudocode": pseudocode.splitlines(),
                    "evaluation_result": result,
                })
                _display_evaluation(result)
                st.success(f"Saved to Results: {result_path}")
            except Exception as exc:
                st.error(f"Evaluation failed for {user_id}: {exc}")


def _render_results() -> None:
    st.title("Results")
    records = _results()
    if not records:
        st.info("No evaluations saved yet.")
        return
    score_csv, score_row_count = _last_five_scores_csv(records)
    st.download_button(
        "Download latest 5 scores per question (CSV)",
        data=score_csv,
        file_name="latest_5_scores_per_question.csv",
        mime="text/csv",
        disabled=score_row_count == 0,
    )
    question_numbers = sorted({str(row.get("question_number", "")) for row in records})
    selected = _question_navigator(
        "Filter by question", ["All questions"] + question_numbers, "result_question_filter"
    )
    filtered = records if selected == "All questions" else [
        row for row in records if str(row.get("question_number")) == selected
    ]
    grouped: dict[str, list[dict]] = {}
    for record in filtered:
        grouped.setdefault(str(record.get("question_number", "")), []).append(record)
    visible = [record for question_records in grouped.values() for record in question_records[-5:]]
    st.caption(f"Showing {len(visible)} latest saved evaluation(s) (up to 5 per question).")
    for index, record in enumerate(reversed(visible), 1):
        label = f"{record.get('question_number', '?')} · User {record.get('user_id', '?')} · #{index}"
        with st.expander(label, expanded=(index == 1)):
            st.markdown("**Pseudocode**")
            pseudocode = record.get("pseudocode", "")
            st.code("\n".join(pseudocode) if isinstance(pseudocode, list) else str(pseudocode))
            _display_evaluation(record.get("evaluation_result", {}))
    st.download_button(
        "Download filtered results as JSON",
        data=json.dumps(visible, ensure_ascii=False, indent=2),
        file_name="pseudocode_results.json",
        mime="application/json",
    )


st.set_page_config(page_title="Pseudocode Evaluator", page_icon="🧩", layout="wide")
with st.sidebar:
    st.title("Pseudocode Evaluator")
    page = st.radio(
        "Pages",
        ("Question", "Criteria", "Direct prompt Eval", "Generated code Eval", "Result"),
        key="page",
    )
    with st.expander("LLM provider", expanded=False):
        configured = os.environ.get("LLM_PROVIDER", "").strip().lower()
        if configured in {"gemini", "openai"}:
            provider_default = configured
        elif os.environ.get("GEMINI_API_KEY"):
            provider_default = "gemini"
        elif os.environ.get("OPENAI_API_KEY"):
            provider_default = "openai"
        else:
            provider_default = "gemini"
        provider = st.selectbox("Provider", ("gemini", "openai"),
                                index=("gemini", "openai").index(provider_default))
        key_name = "GEMINI_API_KEY" if provider == "gemini" else "OPENAI_API_KEY"
        api_key = st.text_input("API key", value=os.environ.get(key_name, ""), type="password")
        if api_key.strip():
            os.environ[key_name] = api_key.strip()
            os.environ["LLM_PROVIDER"] = provider
            llm_client._cached_provider = None

if page == "Question":
    _render_questions()
elif page == "Criteria":
    _render_criteria()
elif page == "Direct prompt Eval":
    _render_evaluation()
elif page == "Generated code Eval":
    _render_code_assisted_evaluation()
else:
    _render_results()
