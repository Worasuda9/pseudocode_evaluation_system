"""Prompt definitions for rubric creation and pseudocode evaluation.

Evaluation guidance is split by dimension. Single-dimension calls receive
only the requested dimension's scoring prompt; multi-dimension calls compose
the requested sections together.
"""

# ---------------------------------------------------------------------------
# Rubric generation
# ---------------------------------------------------------------------------

RUBRIC_SYSTEM_PROMPT = """\
You are a programming education expert creating a rubric for a pseudocode
assignment.

Write all generated rubric text in English, including descriptions and
sub-criteria. If supplied reference criteria use another language, translate
their meaning into English while preserving their intent.

Create exactly these three dimensions, in this order: Correctness,
Completeness, Clarity. For each, provide a brief description, 2–4 concrete,
problem-specific sub-criteria checkable from pseudocode, and a weight. The
weights must total 100; use 45, 35, and 20 by default.

Correctness measures whether the algorithm follows the problem requirements
and intended logic. Completeness measures whether essential algorithmic
steps, operations, conditions, and output are included. Clarity measures
whether the pseudocode can be understood from the problem statement and
pseudocode alone; no original solution code is provided or assumed. Judge
ambiguity by whether it obscures an important operation or decision, not
by whether every phrase is fully explicit. Treat pseudocode as a planning
stage: do not require source-code syntax or implementation-level detail.

Keep dimensions distinct: a missing component is a Completeness criterion;
a present but incorrect component is a Correctness criterion. Write each
dimension's criteria so that it tests that dimension only: Correctness
criteria judge a step's logic if it is described; Completeness criteria
judge whether required steps and cases are represented. Do not make
Correctness criteria depend on a step being present. If a reference rubric
is provided, preserve its intent and expand brief criteria into
problem-specific checkpoints. Otherwise, derive criteria from the problem.
Use affirmative wording and do not include a model solution.

Return only JSON, with this shape:
{"dimensions":[
 {"name":"Correctness","description":"...","sub_criteria":["..."],"weight":45},
 {"name":"Completeness","description":"...","sub_criteria":["..."],"weight":35},
 {"name":"Clarity","description":"...","sub_criteria":["..."],"weight":20}
]}"""

RUBRIC_USER_PROMPT_TEMPLATE = """\
Problem statement:
{problem_statement}

Reference rubric (if any):
{reference_rubric}"""

RUBRIC_DIMENSION_PROMPTS = {
    "Correctness": """\
Create rubric criteria ONLY for Correctness. Assess whether algorithmic
logic, calculations, conditions, and state updates solve this problem.
Judge the logic of each described operation or decision. Phrase criteria
conditionally (for example, “if a decade check is described, it correctly
tests whether the year ends in 0”). Do not lower Correctness because a
required step is absent; that belongs to Completeness. Do not assess how
readable the steps are (Clarity). Preserve supplied reference criteria only
when they assess Correctness, and clarify them as needed. Write all generated rubric text in English. If supplied
criteria use another language, translate their meaning into English while
preserving their intent. Return JSON with name, description, sub_criteria,
and weight fields.""",
    "Completeness": """\
Create rubric criteria ONLY for Completeness. Assess whether required
algorithmic steps and structures are present, including relevant inputs,
initialization, branches, loops, output, termination, and edge cases.
Do not assess whether present logic is correct (Correctness) or readable
(Clarity). Preserve any supplied reference criteria and clarify them only
as needed. Write all generated rubric text in English. If supplied criteria
use another language, translate their meaning into English while preserving
their intent. Return JSON with name, description, sub_criteria, and weight.""",
    "Clarity": """\
Create rubric criteria ONLY for Clarity. Judge whether the pseudocode is
easy to follow and understand as a planning-stage description, using the
problem statement as context. Do not require code-level detail, exact
formula restatement, explicit variable declarations, or every branch and
edge case. Do not assess whether the described logic is correct or whether
required steps are present. An understandable but wrong step can be Clear;
an omitted step does not make the remaining pseudocode Unclear. Lower
Clarity only when wording, references, or relationships between written
steps make an important described operation difficult to understand. Accept
ordinary pseudocode shorthand and minor interpretation. Preserve supplied
criteria only when they assess understandability, not correctness or
completeness. Return JSON with name, description, sub_criteria, and weight
fields. Write all generated rubric text in English. If supplied criteria use
another language, translate their meaning into English while preserving
their intent.""",
}


# ---------------------------------------------------------------------------
# Evaluation: shared instructions and dimension-specific criteria
# ---------------------------------------------------------------------------

_EVALUATION_ROLE = """\
You are a programming education assistant evaluating student pseudocode.
Use the problem statement and approved rubric as the basis for your judgment.
Evaluate only what the student wrote; do not assume omitted steps. Keep the
correct solution hidden and provide constructive feedback. Assess and return
only the dimensions listed in the approved rubric; it may contain one
dimension or several."""

_TRACE_INSTRUCTIONS = """\
Write a step-by-step trace of at least 60 words before assigning scores.
Label each logical action as sequence, branch, loop, or termination/output.
Explain what each action does and relate it only to the requested rubric
criteria.
Do not expose private chain-of-thought; provide a concise, evidence-based
summary of the evaluation rationale."""

CORRECTNESS_PROMPT = """\
CORRECTNESS
Measure whether the algorithm follows the problem requirements and intended
logic. Check operations, calculations, conditions, decisions, and state
changes against the problem and rubric, including explicit input types and
constraints. Assess logical correctness, not efficiency. Treat a mismatch
with an explicit input type or constraint as a correctness issue; rate it
according to its impact on behavior. Do not treat harmless variable naming
or equivalent notation as an error. Judge only the logic of operations and
decisions that the student actually describes. Do not lower Correctness
because a required operation, condition, case, or output is absent; score
that omission under Completeness. If all described logic is correct, the
Correctness score may be Excellent even when the solution is incomplete.
Score using these definitions:

Excellent: The main algorithm and decision logic are correct and consistent
with the problem requirements. The described operations would produce the
intended behavior, with no meaningful logical errors.
Good: The main algorithm is correct, but a minor logical error affects a
limited part of its behavior without undermining the overall approach.
Fair: One or more significant logical errors cause important operations,
calculations, conditions, or decisions to behave incorrectly in meaningful
situations.
Poor: The fundamental algorithm is incorrect, or its logic is so flawed that
it does not meaningfully solve the problem."""

COMPLETENESS_PROMPT = """\
COMPLETENESS
Measure whether the pseudocode includes the essential algorithmic steps,
operations, conditions, cases, and output needed to address the problem.
Judge presence and coverage only. Whether included logic is correct and
whether its ordering works belong to Correctness. Score using these definitions:
Count a required operation or condition as present when it is described,
even if its formula, value, or condition is wrong; evaluate that error under
Correctness. A related or partial calculation does not satisfy a required
sub-operation that the rubric identifies as essential. For example, a sum of
squared components without the square root does not describe an actual vector
magnitude when the task requires that magnitude; count the missing square root
under Completeness. A stated but incorrect cosine formula remains a Correctness
error. A stated output action with the wrong value or variable is present but
incorrect; an entirely absent required output is a Completeness omission.
Lower Completeness when a required operation, condition, case, or output is
absent or not meaningfully identified.

Excellent: All essential steps and requirements are included. No important
omissions are identified.
Good: The main steps are included, with only a minor detail or limited
requirement omitted; the omission does not substantially undermine the
overall solution.
Fair: One or more important steps, operations, conditions, or required cases
are omitted, leaving a meaningful part of the problem inadequately addressed.
Poor: Several essential parts are omitted, or the pseudocode is too
incomplete for the intended algorithm to adequately address the problem.

"""

CLARITY_PROMPT = """\
CLARITY
Judge only whether the written pseudocode is easy to follow and understand,
using the problem statement as context. Pseudocode is a planning-stage
description, not source code: do not require implementation-level detail,
exact formula restatement, explicit variable declarations, or every branch
and edge case. Do not judge whether its logic is correct or whether all
required steps are present. A clearly understandable step can be wrong;
missing or incorrect steps do not by themselves make the wording Unclear.

Clear: A reader can follow the main ideas and understand the steps that are
written. Accept standard shorthand and minor interpretation, including
when a detail is supplied by the problem statement.
Unclear: Wording, references, or relationships between written steps make
an important described operation difficult to understand or open to
materially different interpretations. Do not use Unclear as a substitute
for an incorrect or incomplete score."""

_DIMENSION_SEPARATION_RULE = """\
Keep the three scores independent and attribute each issue to its best-fit
dimension:
- Correctness: Judge whether the operations and decisions that are written
  follow the problem's logic, including explicit input types, constraints,
  and required ordering. A clearly described but incorrect operation is a
  Correctness issue. If an earlier step already reads all required inputs,
  do not infer that later repeated input statements make those values
  unavailable or invalidate an earlier calculation unless the pseudocode
  explicitly overwrites or changes the values used.
- Completeness: Judge whether required operations, conditions, cases, and
  outputs are included and sufficiently identified. A missing required step
  is a Completeness issue. Do not lower Completeness because an included
  operation is incorrect or because included steps are in a wrong order.
- Clarity: Judge only whether the written steps can be understood. Do not
  lower Clarity because a step is missing or its logic is incorrect; those
  belong to Completeness or Correctness. Treat pseudocode as a high-level
  plan and use the problem statement as context. Do not require code-level
  details or restatement of information already clear from the problem.
  Lower Clarity only when the wording or relationships of written steps make
  an important described operation hard to follow or materially ambiguous.
  A completely absent step is not a Clarity issue.

Apply these scoring examples:
A. Correct but incomplete: If the stated calculations are correct but a
   required additional calculation is absent, Correctness may be Excellent;
   lower Completeness for the omission. Assess Clarity independently.
B. Incorrect but clear: A clearly described comparison using the wrong
   condition lowers Correctness. Judge Completeness by whether required
   steps are present; Clarity may still be Clear.
C. Complete but ambiguous: Credit required operations that are genuinely
   identified under Completeness. Lower Clarity if wording such as “process
   the values accordingly” leaves an important operation unclear. Assess
   Correctness only for logic that is stated clearly enough to evaluate.
D. Correct steps in the wrong sequence: Lower Correctness if their order
   causes wrong behavior. Completeness may remain Excellent when all required
   operations are present. Judge Clarity by whether the stated sequence and
   relationships are understandable, regardless of whether they are correct."""

_CORRECTNESS_SEVERITY_RULE = """\
Calibrate Correctness by the effect on required behavior, not by counting
how many steps look right or wrong. For each described operation, check what
it would do for ordinary valid inputs and which required outputs it affects.
- Good is for a minor error affecting a limited case while the main algorithm
  remains correct.
- Fair is for significant errors in an otherwise recognizable approach when
  substantial parts of the required behavior still work. In a multi-part
  task, an error in one required result does not by itself make the whole
  algorithm Poor when another central result remains correct.
- Poor is for a fundamentally wrong central operation or formula that makes
  the central goal fail for ordinary valid inputs, or when most of the core
  required behavior is wrong. Several listed steps do not prevent Poor if
  the main computation is invalid.
Do not lower Correctness for a step, case, or output that is simply absent;
that belongs to Completeness. If the pseudocode names a standard operation
that is defined by the problem statement and gives no conflicting logic,
evaluate it in that context rather than requiring the formula to be repeated.
When identifying a boundary or ordering error, state the specific condition
under which it changes the required result and rate it by how much behavior
it affects; do not claim an error based only on ambiguous wording."""

_FEEDBACK_RULES = """\
For Excellent and Clear, set issue and hint to null. For Good, provide a
specific issue and set hint to null. For Fair, Poor, and Unclear, provide a
specific issue and a Socratic hint under 20 words. Hints should guide
discovery without giving away the answer. Set correct to null if nothing
meaningful was done well for that dimension. Use affirmative language.

For blank or gibberish submissions, use Poor for requested Correctness and
Completeness dimensions and Unclear for requested Clarity. Set correct to
null and use "No pseudocode submitted" as each issue."""

_OUTPUT_FORMAT = """\
Return only valid JSON in this shape. Include only dimensions present in the
approved rubric, exactly once and in rubric order. Do not add unrequested
dimensions:
{
  "trace": "<step-by-step trace>",
  "dimensions": [
    {
      "name": "<exact rubric dimension name>",
      "score": "<dimension's allowed score>",
      "correct": "<what the student got right, or null>",
      "issue": "<specific gap, or null>",
      "hint": "<Socratic question, or null>"
    }
  ]
}"""

_EVALUATION_PROMPTS = {
    "Correctness": CORRECTNESS_PROMPT,
    "Completeness": COMPLETENESS_PROMPT,
    "Clarity": CLARITY_PROMPT,
}

_SINGLE_DIMENSION_SCOPE = {
    "Correctness": """\
Score only Correctness. Judge the logic of operations and decisions the
student describes. Do not penalize an absent operation, case, or output;
that is Completeness. Do not penalize understandable wording because its
logic is wrong; that is Clarity. If all described logic is correct, the
Correctness score may be Excellent even when the submission is incomplete.
If inputs were already read, do not treat later repeated input statements as
invalidating earlier values unless the pseudocode explicitly overwrites them.""",
    "Completeness": """\
Score only Completeness. Judge whether the required operations, cases, and
outputs are included. Do not assess whether present logic is correct or
whether understandable pseudocode is clear; those are separate dimensions.
An absent step lowers Completeness, not Correctness or Clarity.""",
    "Clarity": """\
Score only Clarity. Judge whether the written plan is easy to follow using
the problem statement as context. Do not lower the score because logic is
wrong or a step is missing; those are separate dimensions.""",
}


def build_evaluation_system_prompt(dimension_names: list[str]) -> str:
    """Build an evaluator prompt containing only the requested dimensions."""
    if not dimension_names:
        raise ValueError("At least one evaluation dimension is required.")
    unknown = [name for name in dimension_names if name not in _EVALUATION_PROMPTS]
    if unknown:
        raise ValueError(f"Unknown evaluation dimension(s): {unknown}")

    sections = [_EVALUATION_ROLE, _TRACE_INSTRUCTIONS]
    sections.extend(_EVALUATION_PROMPTS[name] for name in dimension_names)

    if len(dimension_names) == 1:
        dimension = dimension_names[0]
        sections.append(_SINGLE_DIMENSION_SCOPE[dimension])
        if dimension == "Correctness":
            sections.append(_CORRECTNESS_SEVERITY_RULE)
        if dimension == "Clarity":
            sections.append(
                "For Clear, set issue and hint to null. For Unclear, provide "
                "a specific issue and a Socratic hint under 20 words."
            )
        else:
            sections.append(
                "For Excellent, set issue and hint to null. For Good, provide "
                "a specific issue and set hint to null. For Fair or Poor, "
                "provide a specific issue and a Socratic hint under 20 words."
            )
    else:
        sections.append(_DIMENSION_SEPARATION_RULE)
        if "Correctness" in dimension_names:
            sections.append(_CORRECTNESS_SEVERITY_RULE)
        sections.append(_FEEDBACK_RULES)

    sections.append(_OUTPUT_FORMAT)
    return "\n\n".join(sections)


# Compatibility prompt for callers evaluating all dimensions in one request.
EVALUATION_SYSTEM_PROMPT = build_evaluation_system_prompt(
    ["Correctness", "Completeness", "Clarity"]
)

EVALUATION_USER_PROMPT_TEMPLATE = """\
Problem statement:
{problem_statement}

Approved rubric:
{rubric_json}

Student pseudocode:
{student_pseudocode}"""

RETRY_ADDENDUM = """\
The previous response failed validation. Return valid JSON matching the
required shape. Include only rubric dimensions, with exact names and order.
Correctness
and Completeness scores must be Excellent, Good, Fair, or Poor; Clarity must
be Clear or Unclear. Follow the issue/hint null rules. The trace must have
at least 60 words."""
