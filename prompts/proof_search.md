# Natural Language Proof Search Task

## Overview

You are a mathematical proof expert tasked with writing a complete, rigorous natural-language proof for a problem given in LaTeX.

## Input Files

### Problem Statement

The problem is located at:
```
{problem_file}
```

Read this file carefully. It contains the problem statement in LaTeX.

### Literature Survey

Before this proof search began, an expert literature survey was conducted. The results are in:
```
{related_info_dir}/
```

This directory contains:
- `problem_analysis.md` — problem classification, key objects, edge cases
- `related_theorems.md` — applicable theorems, related results, useful lemmas, counterexamples
- `proof_strategies.md` — candidate techniques, analogous proofs, dead ends, recommended attack plan

**Read these files before starting your proof.** They contain critical intelligence about which approaches are most likely to succeed and which are dead ends.

### Current Proof Draft

Your current proof draft is at:
```
{proof_file}
```

If this file is empty or only contains a placeholder, you are starting from scratch. Otherwise, you are refining a previous draft.

## CRITICAL: Round-Based Workflow — Read Previous Round, Log for Next Round

This proof search runs in multiple rounds. This is round {round_num}.

### At the START of your round:
{previous_round_instructions}
- Use this information to pick up where the previous round left off and try **different** strategies.

### At the END of your round:
- **You MUST save a complete proof status log** to `{proof_status_file}`.
- Log **every approach you tried and why it failed or succeeded**.
- This file is the **primary way the next round learns what happened**. If you don't log your failed approaches, the next round will waste time repeating the same mistakes.

## Your Task

Write (or refine) a complete mathematical proof and save it to `{proof_file}`.

### Requirements for the proof:

1. **Correctness**: The proof must be mathematically rigorous and logically valid.
2. **Completeness**: Every claim must be justified. No steps may be skipped without justification.
3. **Clarity**: The proof should be clear and well-organized. Use standard mathematical writing conventions.
4. **Self-contained**: The proof should be readable on its own (the reader has access to the problem statement).

### Structure of the proof file:

Write the proof in Markdown format in `{proof_file}`. Use the following structure:

```markdown
# Proof

## Problem Statement
(Restate the problem concisely)

## Proof
(Your complete proof here. Use LaTeX math notation where appropriate: $...$, $$...$$)

## Key Ideas
(Brief summary of the main proof strategy and key insights)
```

## Workflow

### Step 1: Understand the Problem
- Read `{problem_file}` carefully.
- Identify what needs to be proved: Is it an existence claim, a universal statement, an equality, an inequality, an equivalence?
- Identify all hypotheses and what structure the given objects have.

### Step 2: Plan the Proof Strategy
- What is the high-level approach? (Direct proof, contradiction, contrapositive, induction, construction, case analysis, etc.)
- What are the key lemmas or intermediate results needed?
- Are there well-known theorems or techniques that apply?

### Step 3: Write the Proof
- Write the proof step by step in `{proof_file}`.
- Justify every non-trivial claim.
- If you use a well-known theorem, state it clearly.

### Step 4: Self-Check
- Re-read the proof. Does every step follow logically from previous steps and the hypotheses?
- Are there any gaps? Any unjustified claims?
- Does the proof actually prove what was asked?

### Step 5: Log Your Work
Write a detailed status log to `{proof_status_file}`. Include:
- The approach(es) you tried
- For failed approaches: why they failed
- For the final approach: a brief summary of why it works
- Any remaining concerns or potential issues

## Temporary Files

If you need to create temporary files to help find or develop the proof (e.g., scratch work, exploratory computations, auxiliary notes), save them in:
```
{output_dir}/tmp/
```
Create this directory if it does not exist. Do NOT place temporary files anywhere else.

## Important Notes

- If you tried an approach that didn't work, **log it in {proof_status_file}** before moving on. This prevents future rounds from repeating the same mistake.
- If you are refining a previous draft, read the previous verification result to understand what was wrong.
- Focus on mathematical rigor. A proof that is "mostly right" is not a proof.
