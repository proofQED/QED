# Natural Language Proof Verification Task

## Overview

You are a mathematical logic reviewer tasked with rigorously verifying a natural-language proof.

## Files

### Problem Statement
```
{problem_file}
```

### Proof to Verify
```
{proof_file}
```

## Verification Tasks

You must perform ALL of the following verification checks:

---

### 1. Problem-Proof Alignment

- Does the proof actually address the correct problem?
- Does the proof prove exactly what was asked (not something weaker or different)?
- Are all conditions/hypotheses from the problem statement properly used?

---

### 2. Logical Validity

Check every logical step in the proof:

- Does each step follow logically from previous steps, the hypotheses, or well-known results?
- Are there any logical gaps where the author jumps to a conclusion without justification?
- Are implications correctly directed? (Are there instances of affirming the consequent, denying the antecedent, or other logical fallacies?)
- If proof by contradiction is used: is the assumption correctly negated? Is the contradiction genuine?
- If induction is used: is the base case verified? Does the inductive step correctly use the induction hypothesis?

---

### 3. Completeness

- Are all cases covered? (If a case analysis is used, are all cases handled?)
- Are all non-trivial claims justified? (No "clearly", "obviously", or "it is easy to see" without actual justification of non-trivial facts)
- Are boundary/degenerate cases addressed?
- Does the proof use all necessary hypotheses? (If a hypothesis is unused, is the statement trivially true without it, or is there a gap?)

---

### 4. Correctness of Mathematical Claims

- Are all cited theorems/results correctly stated and correctly applied?
- Are the conditions for applying each cited result actually satisfied?
- Are all computations and algebraic manipulations correct?
- Are there any sign errors, off-by-one errors, or similar mistakes?

---

### 5. Clarity and Rigor

- Is the proof written clearly enough that a knowledgeable reader can follow it?
- Are variables properly introduced before use?
- Are quantifiers correctly ordered and scoped?
- Is notation consistent throughout?

---

## Output Requirements

Write ALL verification results to: `{output_file}`

### Output Format

```markdown
# Proof Verification Results

**Problem:** {problem_file}
**Proof:** {proof_file}

---

## 1. Problem-Proof Alignment
**Status:** [PASS/FAIL]
**Details:** ...

---

## 2. Logical Validity
**Status:** [PASS/FAIL]
**Issues found:** [list each issue with the specific step number/location]

---

## 3. Completeness
**Status:** [PASS/FAIL]
**Missing items:** [list any gaps]

---

## 4. Correctness of Mathematical Claims
**Status:** [PASS/FAIL]
**Errors found:** [list each error]

---

## 5. Clarity and Rigor
**Status:** [PASS/FAIL]
**Suggestions:** [list any issues]

---

## Summary

| Check | Status |
|-------|--------|
| Problem-Proof Alignment | [PASS/FAIL] |
| Logical Validity | [PASS/FAIL] |
| Completeness | [PASS/FAIL] |
| Correctness | [PASS/FAIL] |
| Clarity and Rigor | [PASS/FAIL] |

### Overall Verdict: [PASS/FAIL]

### Specific Issues to Fix (if FAIL):
1. ...
2. ...
```

## Temporary Files

If you need to create temporary files to help verify the proof (e.g., checking computations, testing edge cases, working through sub-arguments), save them in:
```
{output_dir}/tmp/
```
Create this directory if it does not exist. Do NOT place temporary files anywhere else.

## Critical Instructions

- Be thorough and skeptical. Your job is to find errors, not to approve proofs.
- If a hard problem is "easily" proved, be especially suspicious.
- Check that proof by contradiction actually uses the negated assumption.
- Check that induction proofs actually invoke the induction hypothesis.
- A proof that is "almost right" is still FAIL. Mathematical proofs are either correct or incorrect.
- If you find the proof is correct, say so clearly with a PASS verdict.
