# Human Annotation Guidelines for Student Pseudocode Evaluation

**Study:** LLM-Based Pseudocode Evaluation System  
**Annotation dimensions:** Correctness, Completeness, and Clarity  
**Annotators:** Two independent human annotators

## 1. Purpose

The purpose of this annotation task is to evaluate the quality of student pseudocode before code is written. Three dimensions are evaluated independently:

- **Correctness:** Whether the algorithm described in the pseudocode follows the programming problem's requirements.
- **Completeness:** Whether the pseudocode includes the essential steps and requirements needed to solve the problem.
- **Clarity:** Whether another person can understand the intended algorithm from the pseudocode.

Correctness and Completeness use a four-level rating scale. Clarity uses a two-category rating: **Clear** or **Unclear**.

The annotation results will be used to examine agreement between two human annotators and to investigate the relationship between human judgments of pseudocode quality and the instructor's existing programming scores.

## 2. Materials Provided to Annotators

For each submission, annotators will receive:

1. The programming problem statement, including its requirements and expected behavior.
2. The student's pseudocode.
3. The annotation guidelines and the relevant problem-specific criteria.

Annotators will not receive the original student code, the instructor's existing score, or the other annotator's ratings during the annotation process.

The programming problem provides the reference for deciding what the algorithm should do. The student's pseudocode is the only evidence used to determine what the student has communicated.

## 3. Rating Scales

### 3.1 Correctness and Completeness

Correctness and Completeness are each rated on a four-level ordinal scale:

| Score | Label | General meaning |
|---:|---|---|
| 4 | Excellent | The pseudocode meets the dimension's requirements with no meaningful issues. |
| 3 | Good | The pseudocode meets most requirements, with minor issues. |
| 2 | Fair | The pseudocode has notable problems that reduce its quality in this dimension. |
| 1 | Poor | The pseudocode has major problems and does not adequately meet the dimension's requirements. |

A higher score represents better quality. The difference between adjacent scores is not assumed to be equal.

### 3.2 Clarity

Clarity uses only two categories. The previous Excellent and Fair levels are not used for Clarity.

| Score | Label | Category | General meaning |
|---:|---|---|---|
| 3 | Good | Clear | The main algorithm can be understood. Some wording, references, or transitions may require minor interpretation, but the reader can follow the intended logic without making substantial assumptions. |
| 1 | Poor | Unclear | Important parts are vague or ambiguous, or the pseudocode is difficult to interpret. The reader must make substantial assumptions and cannot reliably determine the intended algorithm. |

**Important:** For Clarity, annotators must choose either **Clear** or **Unclear**. Do not assign Excellent (4) or Fair (2) for this dimension.

## 4. Dimension 1: Correctness

### Definition

Correctness measures whether the algorithm described in the pseudocode follows the requirements and intended logic of the programming problem.

### Primary reference

Use the instructor's criteria for the corresponding programming problem as the primary reference for identifying the required algorithmic operations and correct logic.

However, annotators must judge the pseudocode itself. They must not use the instructor's score as a substitute for evaluating the described algorithm.

### Scoring criteria

**Excellent (4)**

The main algorithm and its decision logic are correct and consistent with the problem requirements. The described operations would produce the intended behavior, with no meaningful logical errors.

**Good (3)**

The main algorithm is correct, but a minor logical error affects a limited part of its behavior without undermining the overall approach.

**Fair (2)**

The pseudocode contains one or more significant logical errors. Important operations, calculations, conditions, or decisions would produce incorrect behavior in meaningful situations.

**Poor (1)**

The fundamental algorithm is incorrect, or the described logic is so flawed that it does not meaningfully solve the problem.

### What to examine

- Whether the main algorithm follows the problem requirements.
- Whether calculations and operations are logically correct.
- Whether conditions and branches express the intended decisions.
- Whether the order of operations preserves the required logic.
- Whether the stated algorithm would produce the expected behavior in the situations it describes.

### Rules

1. Evaluate the algorithm as written. Do not silently correct, simplify, or optimize it.
2. Do not penalize unconventional wording or formatting if the intended logic is understandable.
3. If a necessary step is missing, consider Completeness separately.
4. If a condition is clearly described but logically wrong, penalize Correctness rather than Clarity.
5. Use the instructor's problem-specific criteria to determine what constitutes correct behavior for each problem.

## 5. Dimension 2: Completeness

### Definition

Completeness measures whether the pseudocode includes the essential algorithmic steps, operations, conditions, and output needed to address the programming problem.

Correctness concerns whether the stated logic is right. Completeness concerns whether the necessary parts of the solution are present.

### Primary reference

Use the instructor's criteria to identify the required components for each programming problem. Any criterion that specifies a required operation, processing step, condition, or output should also inform the Completeness assessment.

If the instructor's criteria do not explicitly identify all essential steps, use the problem statement to determine what is required.

A problem-specific completeness checklist should be prepared from the problem statement and instructor's criteria before annotation begins.

### Scoring criteria

**Excellent (4)**

The pseudocode includes all essential steps and requirements needed to solve the problem. No important omissions are identified.

**Good (3)**

The pseudocode includes the main steps but omits a minor detail or limited requirement that does not substantially undermine the overall solution.

**Fair (2)**

The pseudocode omits one or more important steps, operations, conditions, or required cases. As a result, a meaningful part of the problem is not adequately addressed.

**Poor (1)**

The pseudocode omits several essential parts of the solution or is so incomplete that the intended algorithm cannot adequately address the problem.

### What to examine

- Whether the essential input and data-processing steps are included when required.
- Whether the major operations identified in the problem-specific checklist are present.
- Whether necessary calculations, decisions, and processing stages are described.
- Whether required repetition and stopping conditions are included.
- Whether important branches or cases specified by the problem are addressed.
- Whether the expected result is produced or its production is described.

### Rules

1. Do not require every implementation detail, temporary variable, or programming-language statement.
2. Do not require a fixed format, numbered steps, or a particular algorithm if the essential requirements are covered.
3. Do not automatically penalize an omitted edge case unless it is required by the problem or necessary to satisfy its stated requirements.
4. If a required operation is completely absent, assess the omission under Completeness.
5. If the operation is present but logically incorrect, assess Correctness as well.
6. Do not assume that an unstated step is present simply because it would be easy for an experienced programmer to add it.

## 6. Dimension 3: Clarity

### Definition

Clarity measures how easily a reader can understand the algorithm from the student's pseudocode without substantial guessing or resolving ambiguity.

Clarity focuses on how the algorithm is communicated, rather than whether the algorithm is correct or whether all required steps are present.

### Scoring criteria

** Clear**

The main algorithm is understandable. Some wording, references, or transitions may require minor interpretation, but the reader can follow the intended operations, conditions, and relationships between steps without making substantial assumptions.

**Unclear**

Important parts are vague, ambiguous, or difficult to interpret. The reader must make substantial assumptions to understand important operations or decisions, or cannot reliably determine the intended algorithm.

### What to examine

- Whether the intended operations are expressed understandably.
- Whether conditions explain what is being checked.
- Whether the relationships between steps are understandable.
- Whether references to values, variables, or previously described items are sufficiently clear.
- Whether the flow of decisions, repetition, and termination can be followed.
- Whether important statements allow multiple substantially different interpretations.

### Rules

1. Do not require pseudocode to resemble Python or another programming language.
2. Do not require numbered steps, bullet points, or a fixed input-process-output structure.
3. Natural language, short notes, fragments, and unconventional formatting are acceptable if the algorithm remains understandable.
4. Do not penalize a clearly expressed but incorrect algorithm under Clarity.
5. Do not automatically penalize an omitted step under Clarity; assess the omission under Completeness.
6. If the student uses vague wording that prevents the reader from determining what an operation does, assign **Unclear**.
7. Judge clarity based on the information available in the pseudocode, not on what the student may have intended.
8. Use **Clear** when the intended algorithm can be followed despite minor wording or transition issues. Use **Unclear** when understanding important parts requires substantial assumptions.

## 7. Distinguishing the Three Dimensions

The dimensions must be evaluated independently. A submission may receive different ratings because each dimension measures a different aspect of quality.

### Example A: Correct but incomplete

The pseudocode correctly describes how to calculate a total, but it omits an additional calculation required by the problem.

- **Correctness:** Potentially Excellent (4), if the stated calculation is correct.
- **Completeness:** Lower, because a required operation is missing.
- **Clarity:** Rated independently as Clear or Unclear.

### Example B: Incorrect but clear

The pseudocode clearly explains a comparison, but the comparison uses the wrong condition for the problem.

- **Correctness:** Lower, because the logic is incorrect.
- **Completeness:** Assessed based on whether the required steps are present.
- **Clarity:** Clear, because the intended comparison is understandable.

### Example C: Complete but ambiguous

The pseudocode mentions all required operations but uses a vague statement such as "process the values accordingly" without explaining the required processing.

- **Completeness:** May be high if the essential operations are genuinely identified.
- **Clarity:** Unclear if the reader cannot determine what the processing means.
- **Correctness:** Assess only to the extent that the stated logic can be evaluated.

### Example D: Correct individual steps in the wrong sequence

The pseudocode includes all required operations, but their order would cause the algorithm to produce an incorrect result.

- **Correctness:** Lower if the sequence causes incorrect behavior.
- **Completeness:** Potentially high because the necessary operations are present.
- **Clarity:** Assess whether the sequence and relationships between steps are understandable. The rating may be Clear even if the sequence is incorrect.

**General rule:** Do not penalize the same issue in multiple dimensions automatically. Lower multiple ratings only when the submission independently demonstrates problems in each dimension.

## 8. Annotation Procedure

For each submission, annotators should follow these steps:

1. Read the programming problem and identify its requirements.
2. Read the student's pseudocode in full.
3. Review the problem-specific criteria for Correctness and the corresponding completeness checklist.
4. Assign a Correctness score (1–4).
5. Assign a Completeness score (1–4).
6. Assign a Clarity category: Clear or Unclear.
7. Provide a short justification for each rating, citing the relevant content of the pseudocode.
8. Submit all ratings independently without discussing the submission with the other annotator.

Annotators should assess only what the pseudocode communicates. They must not assume missing steps are intended or use the original student code to fill in gaps.
