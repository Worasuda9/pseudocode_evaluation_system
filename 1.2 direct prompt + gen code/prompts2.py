"""Current evaluation prompts with generated-code evidence for the assisted path."""

from __future__ import annotations

import json

from prompts import (
    EVALUATION_USER_PROMPT_TEMPLATE,
    RETRY_ADDENDUM,
    build_evaluation_system_prompt,
)

DIMENSIONS = ("Correctness", "Completeness", "Clarity")

_CODE_EVIDENCE_RULE = """\
Code-assisted evidence rule:
The generated Python is a translation of the student's pseudocode, not an
official solution or independent reference. Grade the pseudocode itself.
Use the generated code only to interpret an explicitly described pseudocode
operation or identify a translation mismatch. Do not credit behavior present
only in the generated code, and do not penalize the pseudocode for a bug that
appears only in the generated translation. If the two differ, state the
difference when relevant and base the score on the pseudocode and problem.
The generated code must not supply a missing pseudocode step or resolve a
material ambiguity by itself."""


def build_code_assisted_system_prompt(dimension: str) -> str:
    """Reuse the current direct-evaluation rules for one dimension."""
    return f"{build_evaluation_system_prompt([dimension])}\n\n{_CODE_EVIDENCE_RULE}"


def build_code_assisted_user_prompt(
    problem_statement: str,
    dimension: str,
    pseudocode: str,
    generated_code: str,
    criteria: str = "",
    retry_note: str = "",
) -> str:
    """Use the direct pipeline's approved-rubric layout plus labeled code."""
    rubric = {
        "dimensions": [
            {
                "name": dimension,
                "sub_criteria": criteria.splitlines() if criteria.strip() else [
                    f"Apply the current {dimension} definition and the problem requirements."
                ],
            }
        ]
    }
    prompt = EVALUATION_USER_PROMPT_TEMPLATE.format(
        problem_statement=problem_statement,
        rubric_json=json.dumps(rubric, ensure_ascii=False, indent=2),
        student_pseudocode=pseudocode,
    )
    prompt += f"""

Generated Python translation (secondary evidence only):
```python
{generated_code}
```

Use this code only under the code-assisted evidence rule in the system
instructions. Grade the pseudocode, not the translation."""
    if retry_note:
        prompt += f"\n\n{RETRY_ADDENDUM}\n{retry_note}"
    return prompt
