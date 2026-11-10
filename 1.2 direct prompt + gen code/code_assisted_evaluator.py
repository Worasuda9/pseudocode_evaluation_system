"""Isolated evaluator for pseudocode with its generated Python translation."""

from __future__ import annotations

import json
import re

import llm_client
from prompts2 import DIMENSIONS, build_code_assisted_system_prompt, build_code_assisted_user_prompt


_SCORES = {
    "Correctness": {"Excellent", "Good", "Fair", "Poor"},
    "Completeness": {"Excellent", "Good", "Fair", "Poor"},
    "Clarity": {"Clear", "Unclear"},
}


def _parse_response(raw: str) -> dict:
    text = raw.strip()
    fenced = re.fullmatch(r"```(?:json)?\s*(.*?)\s*```", text, re.DOTALL | re.IGNORECASE)
    if fenced:
        text = fenced.group(1).strip()
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        start, end = text.find("{"), text.rfind("}")
        if start < 0 or end < start:
            raise ValueError("The model did not return a JSON object.")
        parsed = json.loads(text[start : end + 1])
    if not isinstance(parsed, dict):
        raise ValueError("The model response must be a JSON object.")
    return parsed


def _validate(response: dict, dimension: str) -> dict:
    trace = response.get("trace")
    if not isinstance(trace, str) or len(trace.split()) < 60:
        raise ValueError("The trace must contain at least 60 words.")
    rows = response.get("dimensions")
    if not isinstance(rows, list):
        raise ValueError("Expected a dimensions list.")
    matches = [row for row in rows if isinstance(row, dict) and row.get("name") == dimension]
    if len(matches) != 1:
        raise ValueError(f"Expected exactly one {dimension} dimension result.")
    row = matches[0]
    # Match the direct evaluator's compatibility with older four-level clarity
    # responses while retaining the current two-level clarity scale.
    if dimension == "Clarity":
        row["score"] = {
            "Excellent": "Clear",
            "Good": "Clear",
            "Fair": "Unclear",
            "Poor": "Unclear",
        }.get(str(row.get("score", "")).strip().title(), row.get("score"))
    if row.get("score") not in _SCORES[dimension]:
        raise ValueError(f"Invalid {dimension} score: {row.get('score')!r}.")
    for key in ("correct", "issue", "hint"):
        if row.get(key) is not None and not isinstance(row.get(key), str):
            raise ValueError(f"{key} must be a string or null.")
    # Match the direct evaluator's normalization instead of spending a retry
    # on fixable issue/hint null inconsistencies.
    if row["score"] in {"Excellent", "Clear"}:
        row["issue"] = None
        row["hint"] = None
    elif row["score"] == "Good":
        if not row.get("issue"):
            row["issue"] = f"A noticeable gap remains in {dimension.lower()}."
        row["hint"] = None
    else:
        if not row.get("issue"):
            row["issue"] = f"A significant gap remains in {dimension.lower()}."
        if not row.get("hint"):
            row["hint"] = f"Can you think of a way to improve the {dimension.lower()}?"
    return {"dimensions": [row], "trace": trace}


async def _evaluate_dimension(
    problem_statement: str,
    dimension: str,
    pseudocode: str,
    generated_code: str,
    criteria: str,
) -> dict:
    system_prompt = build_code_assisted_system_prompt(dimension)
    retry_note = ""
    last_error = None
    for attempt in range(2):
        user_prompt = build_code_assisted_user_prompt(
            problem_statement=problem_statement,
            dimension=dimension,
            pseudocode=pseudocode,
            generated_code=generated_code,
            criteria=criteria,
            retry_note=retry_note,
        )
        try:
            _, raw = await llm_client.call_evaluation(system_prompt, user_prompt)
            return _validate(_parse_response(raw), dimension)
        except Exception as exc:
            last_error = exc
            retry_note = f"Your previous response was invalid: {exc}. Return corrected JSON matching the schema."
    raise ValueError(f"{dimension} evaluation failed after one retry: {last_error}")


async def evaluate_submission(
    problem_statement: str,
    pseudocode: str,
    generated_code: str,
    criteria: dict[str, str] | None = None,
) -> dict:
    """Evaluate each dimension in its own request; no old evaluator state is used."""
    criteria = criteria or {}
    dimensions = []
    traces = []
    for name in DIMENSIONS:
        result = await _evaluate_dimension(
            problem_statement=problem_statement,
            dimension=name,
            pseudocode=pseudocode,
            generated_code=generated_code,
            criteria=criteria.get(name, ""),
        )
        dimensions.extend(result["dimensions"])
        traces.append(f"{name}:\n{result['trace']}")
    return {"dimensions": dimensions, "trace": "\n\n".join(traces)}
