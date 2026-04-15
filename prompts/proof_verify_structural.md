# Structural Proof Verification Task (Phases 1–4)

> **Agentic task.** Read the input files first, then think, plan, and work — use bash, computational tools, or any available resources as needed. Write the output files using tool calls according to the instructions. All input/output file paths and format specifications are at the end of this prompt.

## Overview

You are a mathematical logic reviewer tasked with performing the **structural verification** of a natural-language proof. This covers four phases: Problem-Statement Integrity, Citation Verification, Subgoal Tree Structure, and Additional Verification Rules. These are the foundational checks — if the proof fails any of these, detailed step-by-step verification will not be attempted.

You must be absolutely strict. If you are uncertain if the proof proved certain claim, then it fail to do so. You should always be very conservative on every respect. All judgement should be based on evidence.

---

## Verification Method

The verification proceeds in four phases, ordered from cheapest/most-fatal to most-expensive. These are structural checks that validate the proof's foundations before any detailed step-by-step work.

### Phase 1: Problem-Statement Integrity

**This is the most critical check and must be done FIRST, before anything else.**

The proof search agent may — intentionally or accidentally — alter, weaken, or re-interpret the problem statement. You must catch this.

1. Read the **original** problem statement from `{problem_file}` verbatim.
2. Identify the claim the proof **actually proves** (look at what it states at the beginning and what it concludes).
3. Compare the two **word-by-word**. Flag ANY discrepancy, including but not limited to:
   - Changed quantifiers (e.g. "for all" → "there exists", or an added/dropped "for all")
   - Strengthened or weakened hypotheses (extra assumptions added, or conditions dropped)
   - Modified constants, bounds, or inequalities (e.g. strict vs. non-strict, changed exponents)
   - Restricted domain (e.g. proving for integers when the problem says reals)
   - Swapped conclusion and hypothesis (proving the converse instead of the original)
   - Subtle rephrasing that changes meaning (e.g. "at most" → "at least", "unique" dropped)
   - Proving a special case instead of the general statement
4. If the proof does not state the problem it is proving, that itself is a FAIL — the proof must clearly declare what it proves so the reader can verify alignment.

**If the problem the proof claims to solve differs from `{problem_file}` in ANY mathematically meaningful way, this check is FAIL — regardless of whether the proof of the altered statement is correct. Record the failure and continue to the remaining phases.**

### Phase 2: Citation Verification

**This phase is critical. Language models routinely hallucinate citations — inventing theorem numbers, attributing results to wrong sources, fabricating URLs, or citing statements that do not appear in the referenced source. You must catch every instance of this.**

Citation verdicts from this phase are recorded in the output and will be used by the detailed verification phase (Phase 5) — if a citation is FAIL, any step depending on it will also be FAIL.

#### 2a. Identify all citations

Scan the entire proof for `<cite>...</cite>` blocks. List every citation found.

#### 2b. Check citation format

Every citation must use exactly this format:

```
<cite>type=TYPE; label=LABEL; title=TITLE; authors=AUTHORS; source_url=URL; verifier_locator=EXACT_LOCATOR; statement_match=exact; statement=EXACT_STATEMENT_FROM_SOURCE; usage=EXACTLY_HOW_IT_IS_USED_HERE</cite>
```

For each citation, verify:
- All required fields are present: `type`, `label`, `title`, `authors`, `source_url`, `verifier_locator`, `statement_match`, `statement`, `usage`
- `statement_match` is set to `exact`
- `type` is one of: theorem, lemma, proposition, corollary, definition, remark, section, chapter, or other
- `source_url` is a real URL (not empty, not placeholder text)
- `verifier_locator` is specific enough to find the exact statement (e.g. "Theorem 2.4, p. 17" — NOT vague like "see Section 3" or "see the introduction")
- `usage` is a precise sentence explaining how the result is used (NOT vague like "standard fact" or "used here")

Flag any citation with missing or malformed fields as FAIL.

#### 2c. Verify faithfulness of each citation (THE MOST IMPORTANT PART)

**For EVERY citation, you must independently check whether the cited result is real and faithfully stated.** Do the following for each citation:

1. **Check the source URL.** Open/fetch the `source_url`. Does the URL actually work? Does it point to the claimed paper/book?
2. **Check the title and authors.** Does the source at that URL actually have the claimed title and authors? Models frequently invent or mix up titles and authors.
3. **Locate the exact statement.** Using the `verifier_locator` (e.g. "Theorem 2.4, p. 17"), find the exact cited result in the source. Does the source actually contain a result at that location?
4. **Compare the statement.** Does the `statement` field match what the source actually says? Check word-by-word. Models often:
   - Cite a theorem that exists but state it incorrectly
   - Cite the right theorem number from the wrong paper
   - Paraphrase a result in a way that subtly changes its meaning
   - Cite a weaker/stronger version than what the source actually states
   - Cite a result that simply does not exist in the given source
5. **Check usage correctness.** Is the cited result actually applicable in the way described in the `usage` field? Are the hypotheses of the cited theorem actually satisfied in the context where it is applied?

**Verdict for each citation:** PASS (source verified, statement matches, usage correct) / FAIL (any issue found) / UNABLE_TO_VERIFY (source cannot be accessed — note this is still a risk flag)

**If ANY citation is FAIL, this phase is FAIL.** Record exactly what is wrong for each failed citation.

### Phase 3: Subgoal Tree Structure

**Check the proof's logical architecture BEFORE checking individual steps.** If the subgoal tree is structurally broken, the proof fails regardless of how correct individual steps are.

The proof should declare its architecture as a tree of `<subgoal>` nodes rooted at "main," with `<subgoal-resolved>` markers where each subgoal is established. There are two types: `type: reduction` (proof strategy) and `type: condition` (hypothesis of a cited result).

1. **List all declared subgoals and resolutions.** Extract every `<subgoal>` block and every `<subgoal-resolved>` marker. Record each declaration's id, type, parent, claim, and justification. Record each resolution's id and `by` field.
2. **Check the tree structure.** The `parent` fields form a tree rooted at "main." Verify:
   - Every subgoal's `parent` refers to either "main" or another declared subgoal id.
   - The tree has no orphans (subgoals with nonexistent parents) and no cycles.
3. **Check each node's justification (reduction validity).** For every subgoal:
   - **`type: reduction`**: Does proving this subgoal's `claim` actually help prove the parent? Is the reduction logically sound? This is where you catch **silent goal-shifting** — if the reduction is invalid, the proof has a structural gap regardless of whether individual steps are correct.
   - **`type: condition`**: Does the cited result actually require this condition? Is the condition stated correctly (matching the cited result's exact hypothesis)? Cross-reference with citation verdicts from Phase 2.
4. **Cross-reference conditions with citations.** For every `<cite>` block in the proof, check: does the cited result have conditions/hypotheses? If so, are there corresponding `type: condition` subgoals for each hypothesis? **Missing condition subgoals are a FAIL** — the prover applied a result without checking its conditions. Also check results applied without formal `<cite>` tags (e.g., "by compactness," "by the implicit function theorem") — if the result has nontrivial conditions, flag missing condition subgoals.
5. **Check tree completeness.** Do the subgoals cover the entire proof's logical architecture? If the proof has multiple logical stages but only one subgoal (or none), the prover may be hiding the structure. Flag any major logical transition that lacks a corresponding subgoal.

**Note:** This phase checks whether the tree STRUCTURE is valid — whether the reductions are sound and the architecture is complete. It does NOT check whether individual subgoals are actually proved (that happens in the detailed verification stage).

**Phase 3 overall:** PASS if tree well-formed, all reductions valid, and no missing subgoals. FAIL otherwise.

### Phase 4: Additional Verification Rules

**This phase applies human-provided verification criteria on top of the standard phases above.** Read the following two files if they exist and are non-empty:

1. **Global verification rules:** `{additional_verify_rule_global_file}` — persistent rules that apply to ALL rounds.
2. **Previous round's verification rules:** `{additional_verify_rule_prev_round_file}` — round-specific rules left by a human after reviewing the previous round's results.

If both files are empty or do not exist, this phase is automatically **PASS** (no additional rules to check).

If either file contains rules:

1. **List every rule found.** Extract each distinct verification criterion from both files.
2. **Check the proof against each rule.** For every rule, determine whether the proof satisfies it. Treat each rule as a **hard requirement** — the proof must comply with every single one.
3. **Report per-rule verdicts.** For each rule: PASS (proof satisfies it) or FAIL (proof violates it), with a brief explanation.

**Phase 4 overall:** PASS if no additional rules exist, or if the proof satisfies ALL additional rules. FAIL if the proof violates any additional rule.

---

## Use Computational Tools to Verify Steps

You have access to a shell and can run code. **You should actively use computational tools to check citations** rather than relying only on manual inspection. Save scripts and their output in `{output_dir}/tmp/`.

### Keep tool output concise

Printing large expressions to stdout wastes your context window. Write large results to files in `{output_dir}/tmp/` and print only summaries or booleans. If `len(str(expr)) > 500`, write to file instead of printing.

### How to use tools for verification:

- **Fetch and verify cited sources** — Use web tools to open cited URLs and check that the referenced theorems actually exist and match the cited statement.
- **Check algebraic identities and simplifications** — Use SymPy (`pip install sympy`) to verify that claimed equalities, simplifications, and manipulations are correct.
- **Test claims on concrete cases** — Use Python/NumPy/SageMath to evaluate key formulas at specific values and confirm they match what the proof claims.

**However, if an algorithmic run used for verification is longer than 3 minutes, stop it and skip that computation.**

## Critical Instructions

- **Follow the four phases in order.** Do not skip ahead. Report all structural issues found across all four phases.
- Be thorough and skeptical. Your job is to find errors, not to approve proofs.
- **Citations are the #1 source of hallucinations. Check every single one.** Do not trust any citation without verification. Models invent theorem numbers, fabricate URLs, attribute results to wrong authors, and misstate theorems. Assume every citation is wrong until you verify it yourself.
- **Use computational tools to independently verify citations.** Don't just read the proof — test it.
- **Whenever you feel you verified something, save your partial progress to the file!**

---

## HERE ARE THE INPUT FILE PATHS:

### Problem Statement
```
{problem_file}
```

### Proof to Verify
```
{proof_file}
```

## HERE ARE THE OUTPUT FILE PATHS:

### Verification Results

Write ALL verification results to:
```
{output_file}
```

### Output Format

```markdown
# Structural Verification Results (Phases 1–4)

**Problem:** {problem_file}
**Proof:** {proof_file}
**Mode:** Structural verification (Phases 1–4)

**No output files means the proof failed directly. Always put the verification result in the correct path.**

---

## Phase 1: Problem-Statement Integrity

**Status:** [PASS/FAIL]
**Original problem (from {problem_file}):** [quote verbatim]
**Problem as stated/implied in proof:** [quote what the proof claims to prove]
**Discrepancies:** [list every difference, or "None — exact match"]

---

## Phase 2: Citation Verification

**Citations found:** [N total]

### Citation 1: [label from cite block]
**Source:** [title, authors]
**URL check:** [URL works / URL broken / URL points to wrong source]
**Statement check:** [matches source exactly / does not match / source does not contain this result]
**Usage check:** [correctly applied / incorrectly applied — explain]
**Verdict:** [PASS / FAIL / UNABLE_TO_VERIFY]
**Issues:** [describe any problems, or "None"]

### Citation 2: [label]
...

[Continue for ALL citations]

**Citation Summary:**
| # | Label | Source verified | Statement matches | Usage correct | Verdict |
|---|-------|---------------|-------------------|---------------|---------|
| 1 | [label] | [yes/no/inaccessible] | [yes/no] | [yes/no] | [PASS/FAIL/UNABLE_TO_VERIFY] |
| ... | ... | ... | ... | ... | ... |

**Citations passed:** X / N
**Citations failed:** Y / N
**Citations unverifiable:** Z / N

**Phase 2 overall:** [PASS / FAIL]

---

## Phase 3: Subgoal Tree Structure

**Subgoals declared:** [N total — M reductions, K conditions]

| ID | Type | Parent | Claim (short) | Justification valid |
|----|------|--------|---------------|---------------------|
| SG1 | reduction | main | [brief] | [yes/no] |
| SG2 | condition | SG1 | [brief] | [yes/no] |
| ... | ... | ... | ... | ... |

**Tree well-formed:** [YES / NO — no orphans, no cycles, all parents valid]
**Invalid reductions:** [list any subgoals where the reduction is logically unsound, or "None"]
**Missing reduction subgoals:** [list any major proof transitions without corresponding subgoal, or "None"]
**Missing condition subgoals:** [list any cited results with unchecked conditions, or "None"]

**Phase 3 overall:** [PASS / FAIL — FAIL if tree malformed, invalid reductions, or missing subgoals]

---

## Phase 4: Additional Verification Rules

**Global rules file:** `{additional_verify_rule_global_file}`
**Per-round rules file:** `{additional_verify_rule_prev_round_file}`
**Rules found:** [N total, or "None — no additional rules provided"]

### Rule 1: [rule description]
**Source:** [global / per-round]
**Verdict:** [PASS / FAIL]
**Explanation:** [how the proof satisfies or violates this rule]

### Rule 2: [rule description]
...

[Continue for ALL rules]

**Phase 4 overall:** [PASS / FAIL / PASS (no rules)]

---

## Summary

| Check | Status |
|-------|--------|
| Phase 1: Problem-Statement Integrity | [PASS/FAIL] |
| Phase 2: Citation Verification | [PASS/FAIL] |
| Phase 3: Subgoal Tree Structure | [PASS/FAIL] |
| Phase 4: Additional Verification Rules | [PASS/FAIL] |

### Overall Verdict: [PASS/FAIL]

### Failed Items (if any):
1. [what is wrong]
2. [what is wrong]
...

### Specific Issues to Fix (if FAIL):
1. ...
2. ...
```

### Error Log

If you encounter any errors during this call — tool failures, runtime exceptions, file I/O issues, context window limits, or unexpected behavior — record them in:
```
{error_file}
```
**Always create this file.** If no errors occur, write an empty file. If errors occur, include the error message, what you were doing when it occurred, and any workaround you applied.

### Temporary Files

If you need to create temporary files (e.g., verification scripts, computational checks), save them in:
```
{output_dir}/tmp/
```
Create this directory if it does not exist. Do NOT place temporary files anywhere else.
