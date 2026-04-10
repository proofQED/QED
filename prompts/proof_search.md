# Natural Language Proof Search Task

> **Agentic task.** Read the input files first, then think, plan, and work — use bash, computational tools, or any available resources as needed. Write the output files using tool calls according to the instructions. All input/output file paths and format specifications are at the end of this prompt.

## Overview

You are a mathematical proof expert tasked with writing a complete, rigorous natural-language proof for a problem given in LaTeX.

## CRITICAL: Round-Based Workflow — Read Previous Round, Log for Next Round

This proof search runs in multiple rounds. This is round {round_num}.

### At the START of your round:
{previous_round_instructions}
- Use this information to pick up where the previous round left off and try **different** strategies.
- **Search online for related work.** At the beginning of every round, use web search to look for related theorems, techniques, papers, or forum discussions (e.g., Math StackExchange, MathOverflow, ArXiv, Wikipedia) that may be relevant to the problem. Base your search queries on the previous round's status log — focus on the approaches that failed, the specific steps that were hard, and the techniques that were attempted. This way your searches are targeted rather than generic. Even if a literature survey was done earlier, new queries informed by what was actually tried (and what went wrong) may surface results the initial survey missed. Spend a few minutes on this before diving into proof writing.
- **Check for human guidance.** Read any files in the following locations if they exist and are non-empty:
  1. **Global guidance:** `{human_help_dir}` — persistent hints that apply across all rounds.
  2. **Previous round's guidance:** `{prev_round_human_help_dir}` — round-specific feedback a human left after reviewing the previous round's results. This is especially valuable because it targets exactly what went wrong last round.

  A human may have left hints, suggestions, corrections, or opinions about the problem or about previous proof attempts. This input can be extremely valuable — a single human observation can unlock an approach you hadn't considered or point out a subtle error in your reasoning. Treat human guidance seriously, but still verify any claims independently.

## CRITICAL: Do NOT Shy Away from Difficulty

**This is the most important instruction in this entire prompt.**

You have a tendency to avoid the hard core of a problem. You hand-wave through the difficult steps, write "clearly" or "it is easy to see" when it is not clear or easy at all, or silently weaken the problem to something easier. **This is unacceptable.**

A proof is ONLY valuable if it tackles the hardest part head-on. The hard part is the whole point. Everything else is scaffolding.

**Common avoidance patterns you MUST NOT do:**

- ❌ Writing "clearly, X holds" or "it is straightforward to verify" for non-trivial claims. If it were clear, you wouldn't need to say it. **Prove it.**
- ❌ Skipping the key inequality, the critical estimate, or the hardest case with vague language. **Work through it step by step.**
- ❌ Replacing a hard problem with a weaker version and hoping no one notices. **Prove exactly what was asked.** The verification agent compares your problem statement against the original — any alteration is an automatic FAIL.
- ❌ Giving up after a few minutes of difficulty and writing a half-baked proof. **Push through.** Hard steps require hard work.
- ❌ Claiming a result "follows from standard techniques" without showing which techniques and how they apply. **Be explicit.**
- ❌ Writing a proof outline or sketch and calling it a proof. **A proof must be complete, with every step justified.**
- ❌ Deferring the hard work to "future rounds" when you can do it now. **Do the work NOW.**

**What you SHOULD do instead:**

- ✅ Identify the hardest step in the proof and spend MOST of your effort there.
- ✅ When you hit a wall, try harder before trying something else. Sit with the difficulty. Break the hard step into sub-steps. Use computational tools to explore.
- ✅ **When you are truly stuck and don't know what to do next, search online.** Use web search to look for the specific technique, lemma, or type of problem you are struggling with. Search Math StackExchange, MathOverflow, ArXiv, Wikipedia, or other mathematical resources. A targeted search like "bound for sum of divisors using convexity" or "induction on tree depth for graph coloring" can unlock an approach you hadn't considered. Do NOT spin your wheels in silence — actively seek external knowledge when you are blocked.
- ✅ If a step is hard to prove, that means it NEEDS a careful proof — not a hand-wave.
- ✅ Write out every epsilon, every bound, every case. Be painfully explicit.
- ✅ If you genuinely cannot prove a step after exhaustive effort, say so honestly in the proof status log — do NOT paper over it with vague language in the proof itself.

**Remember: the verification agent WILL catch hand-waving, and the round will be wasted. It is far better to write a proof that is incomplete but honest about its gaps than one that pretends to be complete but hides the hard parts behind "clearly" and "obviously". A failed round where you genuinely engaged with the difficulty teaches the next round something. A failed round where you dodged the difficulty teaches nothing.**


## CRITICAL: Do NOT Alter the Problem Statement

**You must prove EXACTLY the problem stated in `{problem_file}` — nothing more, nothing less.**

- Do NOT add extra assumptions or hypotheses that are not in the original problem.
- Do NOT weaken the conclusion (e.g. proving a bound of 2 when the problem asks for 1).
- Do NOT strengthen the hypotheses (e.g. assuming continuity when the problem only gives measurability).
- Do NOT change quantifiers (e.g. proving "there exists" when the problem says "for all").
- Do NOT restrict the domain (e.g. proving for positive integers when the problem says all integers).
- Do NOT prove a special case and present it as the general result.
- Do NOT prove the converse instead of the original statement.
- Do NOT silently rephrase the problem in a way that changes its mathematical meaning.

When you restate the problem in your proof file, **copy the mathematical content verbatim from `{problem_file}`**. You may reformat (e.g. LaTeX to readable math) but the mathematical meaning must be identical. The verification agent will compare your stated problem against the original word-by-word, and any discrepancy will cause an automatic FAIL.

## Your Task

Write (or refine) a complete mathematical proof and save it to `{proof_file}`.

### Requirements for the proof:

1. **Correctness**: The proof must be mathematically rigorous and logically valid.
2. **Completeness**: Every claim must be justified. No steps may be skipped without justification.
3. **Clarity**: The proof should be clear and well-organized. Use standard mathematical writing conventions.
4. **Self-contained**: The proof should be readable on its own (the reader has access to the problem statement).


## Use Computational Tools Freely

You have access to a shell and can run code. **Use computational tools when needed** to explore, verify, and support your proof work. Do not rely solely on mental calculation — write and run scripts whenever they can help. Save scripts and their output in `{output_dir}/tmp/`.

### ⚠️ Keep tool output concise

Printing large symbolic expressions, matrices, or long lists to stdout wastes your context window — every character of output becomes tokens you can never reclaim. Keep your context budget for reasoning, not for dumping raw SymPy output.

**Rules for computational tool use:**
- **Write large results to files, print only a summary.** Write expressions to files in `{output_dir}/tmp/` and print only a short message like "Written to file, N chars".
- **Print only what you need:** booleans (True/False), small numbers, short summaries, or whether something simplified to zero.
- **For SymPy:** if `len(str(expr)) > 500`, write to file instead of printing. You can always read the file back later if you need specific parts.
- **For loops/enumerations:** print only the final conclusion, not every iteration.
- **For plots:** save to file in `{output_dir}/tmp/`, don't try to display.


### When to reach for a tool:

- **Checking algebraic identities or simplifications** — Don't simplify by hand when SymPy can verify it instantly.
- **Testing conjectures on small cases** — Before proving something for all n, enumerate n = 1..20 computationally.
- **Verifying combinatorial or number-theoretic claims** — Use SageMath or brute-force Python to check formulas against direct computation.
- **Exploring when stuck** — Plot functions, compute tables of values, run experiments to build intuition about *why* a statement is true.
- **Sanity-checking finished proofs** — After completing a proof, numerically verify key claims as a safety net.
- **Solving auxiliary equations** — If the proof requires finding a specific value, root, or closed form, let SymPy/SageMath find it.
- **Matrix and linear algebra claims** — Verify rank, determinant, eigenvalue, or invertibility claims computationally.


**Don't be shy about using tools.** A 5-line Python script that confirms (or refutes) a key step is worth more than 20 minutes of manual algebra. If one tool doesn't work well for your problem, try another.

**If any tool or script you run takes longer than 3 minutes, stop it and try a different approach or skip that computation.**

## Important Notes

- If you are refining a previous draft, read the previous verification result to understand what was wrong.
- Focus on mathematical rigor. A proof that is "mostly right" is not a proof.

## CRITICAL: Strict Citation Format for External Results and Citations

This is a research proof task. Do not reprove well-known results unnecessarily. When using any standard theorem, lemma, proposition, corollary, definition, or other established result from the literature, cite it explicitly instead of reproving it, whenever appropriate.

Every external mathematical result used in the proof must be cited using exactly one citation block of the following form:

<cite>type=TYPE; label=LABEL; title=TITLE; authors=AUTHORS; source_url=URL; verifier_locator=EXACT_LOCATOR; statement_match=exact; statement=EXACT_STATEMENT_FROM_SOURCE; usage=EXACTLY_HOW_IT_IS_USED_HERE</cite>

No other citation format is allowed.

### Field meanings

- `TYPE`: theorem, lemma, proposition, corollary, definition, remark, section, chapter, or other
- `LABEL`: the exact theorem / lemma / proposition / corollary / definition number in the source, or `unlabeled`
- `TITLE`: the exact title of the cited paper or book
- `AUTHORS`: the author list
- `source_url`: a direct and stable link to the cited source, such as an arXiv page, DOI page, publisher page, project page, or stable online copy
- `verifier_locator`: a precise locator sufficient for an independent verifier to find the exact cited statement directly in the source without guessing
- `statement_match`: must always be `exact`
- `statement`: the exact mathematical statement from the cited source that is being imported
- `usage`: a precise sentence stating exactly how that cited statement is used in the current proof

### Exact-statement rule

A citation is valid only if the cited source contains the exact same mathematical statement being used in the proof.

- Do NOT cite a stronger theorem and specialize it.
- Do NOT cite an equivalent reformulation.
- Do NOT cite a related statement that is merely similar.
- Do NOT paraphrase the cited result loosely.
- Do NOT cite a source unless you have checked that the cited statement matches exactly.
- If the exact statement cannot be found in a source, then do not cite it as external support. Instead, prove it directly or record the gap honestly in the proof status log.

### Verifier-locator rule

The `verifier_locator` must be specific enough that a separate verifier, using only the citation block and the cited source, can locate the exact referenced statement directly and unambiguously.

Acceptable examples:
- `Theorem 2.4, p. 17`
- `Lemma 5.1, p. 43`
- `Chapter III, Proposition 9.3, pp. 121-122`
- `Section 4, displayed theorem beginning "Let X be a normal variety...", p. 28`

Unacceptable examples:
- `see Section 3`
- `see the introduction`
- `around page 20`
- `Hartshorne chapter III`
- `arXiv paper`

If the source has numbered results, include the exact result number.  
Include page number(s) whenever possible.  
If the source is an arXiv paper, include the exact arXiv URL and the exact theorem / lemma number and page number if available.  
If the source is a book, include a stable source URL whenever available, plus chapter / section / result number / page number.

### Source-link rule

Every citation must include `source_url`.

- For arXiv papers, use the exact arXiv URL.
- For books, use a stable accessible URL whenever available.
- If no stable public full-text URL is available for a book, use the best available stable source page, such as a DOI page or publisher page.
- Do not invent links.
- Do not omit the link field.

### Usage rule

The `usage` field must state exactly what role the cited statement plays in the present proof.

Good examples:
- `usage=Used exactly as stated to conclude that f is continuous on [a,b].`
- `usage=Used exactly as stated to deduce existence of a weakly convergent subsequence.`
- `usage=Used exactly as stated to identify the dimension of H^0(X,L).`

Bad examples:
- `usage=standard fact`
- `usage=used here`
- `usage=for the next step`

### One-citation-per-result rule

Each `<cite>...</cite>` block must correspond to exactly one imported result.  
If multiple external results are used, create separate citation blocks.

### No fake citations

- Do not invent theorem numbers, page numbers, titles, author names, or URLs.
- Do not cite a source you have not actually checked.
- Do not output a citation block unless all fields are filled with real information.
- If some source metadata truly cannot be found, do not guess. Either locate another source, prove the claim directly, or record the issue honestly in the proof status log.

### Proof-writing policy

The proof should focus on the genuinely problem-specific and nontrivial parts of the argument. Standard background results should be cited using the required `<cite>...</cite>` format rather than reproved. However, every new reduction, delicate step, or nonstandard argument must still be proved fully and explicitly.

## CRITICAL: Mark Key Original Steps

Every proof has steps that are original, nontrivial, and problem-specific — the steps where the real intellectual work happens. These are distinct from routine steps (setting up notation, applying standard definitions) and cited results (covered by `<cite>` tags).

**You must wrap every such step with `<key-original-step>` tags:**

```
<key-original-step>
[The complete, detailed argument for this nontrivial original step]
</key-original-step>
```

### What qualifies as a key original step

- A novel reduction or transformation specific to this problem
- A nontrivial estimate, bound, or inequality that requires real work
- A construction (of an object, counterexample, auxiliary function) designed for this proof
- An argument that combines known results in a non-obvious way
- Any step where the core difficulty of the problem is actually resolved

### What does NOT qualify

- Setting up notation or restating definitions
- Routine applications of standard techniques (e.g., triangle inequality, linearity)
- Steps that are fully justified by a `<cite>` tag
- Trivial case checks or bookkeeping steps

### Rules

- Every proof of a nontrivial problem must have at least one `<key-original-step>`.
- The content inside the tags must be **maximally detailed** — this is where you must show every sub-step, every bound, every case. No hand-waving allowed inside a `<key-original-step>`.
- Do not inflate: tagging a routine step as key-original does not make it so. The verification agent will independently assess which steps are nontrivial.
- Do not hide: if a step is nontrivial but you do not tag it, the verification agent will flag the mismatch.

## CRITICAL: Declare Subgoals (Proof Tree)

The proof must declare its logical architecture as a **tree of subgoals**. The root is the main problem ("main"). Every intermediate claim the proof needs to establish is a node in this tree. There are two types of subgoals:

- **`type: reduction`** — The prover's strategic decomposition: "to prove X, it suffices to prove Y." These represent proof strategy.
- **`type: condition`** — A hypothesis required by a cited result: "Theorem T requires condition C, so I must verify C." These represent obligations imposed by applied theorems.

Both types are nodes in the same tree, checked the same way: the claim must be proved, and the connection to its parent must be valid.

### Subgoal lifecycle: declare, then resolve

**Step 1: Declare** the subgoal with a full `<subgoal>` tag when it is first identified:

```
<subgoal>
id: [unique identifier, e.g. SG1, SG2, ...]
type: [reduction / condition]
parent: [what this subgoal helps prove — "main" for top-level, or another subgoal id]
claim: [precise mathematical statement]
justification: [for reduction: why proving this suffices for the parent]
              [for condition: which cited result requires this, and which hypothesis it is]
</subgoal>
```

**Step 2: Resolve** the subgoal later in the proof, at the point where it is actually established:

```
<subgoal-resolved id="SG1" by="[brief description of how — e.g. 'proved above', 'by Step 3', 'cited: DCT']" />
```

If a subgoal is proved immediately at the point of declaration, place the `<subgoal-resolved>` tag right after the argument. Every `<subgoal>` must have a corresponding `<subgoal-resolved>` by the end of the proof.

### Example 1: Reductions (proof strategy)

Suppose the main problem is: "Prove that every continuous function on [0,1] is uniformly continuous."

```
We prove uniform continuity by finding a uniform δ for any ε.

<subgoal>
id: SG1
type: reduction
parent: main
claim: For every ε > 0, there exists δ > 0 such that for all x, y ∈ [0,1], |x - y| < δ implies |f(x) - f(y)| < ε.
justification: This is the definition of uniform continuity — proving this claim directly proves the main result.
</subgoal>

To find such a uniform δ, we use compactness.

<subgoal>
id: SG2
type: reduction
parent: SG1
claim: The open cover {{B(x, δ_x/2) : x ∈ [0,1]}} of [0,1] has a finite subcover.
justification: By Heine-Borel, [0,1] is compact, so this finite subcover exists. The minimum δ over the finite subcover gives a uniform δ for SG1.
</subgoal>

[... proof that the finite subcover exists ...]

<subgoal-resolved id="SG2" by="proved above via Heine-Borel" />

Taking δ = min(δ_{{x_1}}, ..., δ_{{x_n}})/2, we get the uniform δ for SG1.

<subgoal-resolved id="SG1" by="follows from SG2 — the finite subcover gives a uniform δ" />
```

### Example 2: Conditions (obligations from cited results)

Suppose the proof applies the Dominated Convergence Theorem:

```
By the Dominated Convergence Theorem, we may exchange the limit and integral.
We must verify its hypotheses:

<subgoal>
id: SG5
type: condition
parent: SG3
claim: f_n → f pointwise almost everywhere on Ω
justification: Required hypothesis of the Dominated Convergence Theorem (cite label: DCT).
</subgoal>

<subgoal>
id: SG6
type: condition
parent: SG3
claim: There exists an integrable function g such that |f_n(x)| ≤ g(x) for all n and a.e. x ∈ Ω
justification: Domination hypothesis of the Dominated Convergence Theorem (cite label: DCT).
</subgoal>

Pointwise convergence was established in Step 3 above.
<subgoal-resolved id="SG5" by="proved in Step 3" />

The bound |f_n(x)| ≤ M from Step 2 gives g(x) = M · χ_Ω, which is integrable since Ω has finite measure.
<subgoal-resolved id="SG6" by="g = M · χ_Ω from Step 2, integrable by finite measure" />
```

### Rules

- Every proof with more than one logical stage must have at least one `<subgoal>`.
- **Every application of a cited result with conditions must have `type: condition` subgoals for each hypothesis.** Models routinely apply theorems without checking conditions — this is non-negotiable.
- The `claim` field must be a precise mathematical statement — not a vague description like "handle the boundary case."
- The `justification` field must explain:
  - For `type: reduction`: why proving this claim suffices for its parent. This is where silent goal-shifting gets caught.
  - For `type: condition`: which cited result requires this condition, and which specific hypothesis it corresponds to.
- The `parent` field creates a tree rooted at "main." The verifier checks that the tree is complete (every branch terminates) and every reduction/condition is valid.
- **Every `<subgoal>` must have a matching `<subgoal-resolved>` by the end of the proof.** A subgoal without a resolution marker is an unresolved gap.
- The `by` field in `<subgoal-resolved>` must point to a specific part of the proof — not vague like "this is obvious."
- For well-known results where conditions are trivially satisfied (e.g., continuity of a polynomial), a brief one-line subgoal and immediate resolution is fine. The point is to be explicit, not verbose for trivial cases.

---

## HERE ARE THE INPUT FILE PATHS:

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

**Read these files before starting your proof.** They contain critical intelligence gathered from a literature survey — similar problems, applicable theorems, and known results that may inform your approach.

### Mathematical Strategy Guide

A curated set of proof strategies and methodology principles is at:
```
{skill_file}
```

This covers proof orientation, core strategies (direct proof, induction, contradiction, decomposition), tactics for when you get stuck (counterexample search, contrapositive, viewpoint changes), self-checking discipline, and computational tool usage. **Read and internalize these principles before starting your proof.**

### Current Proof Draft

Your current proof draft is at:
```
{proof_file}
```

If this file is empty or only contains a placeholder, you are starting from scratch. Otherwise, you are refining a previous draft.

## HERE ARE THE OUTPUT FILE PATHS:

### Proof File

Save your complete proof to:
```
{proof_file}
```

Write the proof in Markdown format with the following structure:

```markdown
# Proof

## Problem Statement
(Copy the problem from {problem_file} verbatim. Do NOT paraphrase or alter it.)

## Proof
(Your complete proof here. Use LaTeX math notation where appropriate: $...$, $$...$$)

## Key Ideas
(Brief summary of the main proof strategy and key insights)
```

**No output files means the proof failed directly. Always remember to output the proof and the proof_status_log in the correct path.**

**Save progress incrementally.** Do NOT try to write the entire proof in one shot. As soon as you have a meaningful skeleton, a partial argument, or substantial progress on a key step, **write it to `{proof_file}` immediately** — then keep working to fill in gaps and refine. If you run out of context or hit an error late in a long attempt, all unsaved work is lost. A partial proof with honest gaps marked (e.g., "TODO: show this bound holds") is far more useful to the next round than nothing at all. Save a skeleton first, then iteratively strengthen it. Every time you complete a meaningful sub-argument, update the file.

### Proof Status Log

At the END of your round, **you MUST save a complete proof status log** to:
```
{proof_status_file}
```

Include:
- The approach(es) you tried
- For each failed approach: why it failed
- For the final approach: a brief summary of why it works
- Any remaining concerns or potential issues

Log **every approach you tried and why it failed or succeeded**. This file is the **primary way the next round learns what happened**. If you don't log your failed approaches, the next round will waste time repeating the same mistakes.

### Error Log

If you encounter any errors during this call — tool failures, runtime exceptions, file I/O issues, context window limits, or unexpected behavior — record them in:
```
{error_file}
```
**Always create this file.** If no errors occur, write an empty file. If errors occur, include the error message, what you were doing when it occurred, and any workaround you applied.

### Temporary Files

If you need to create temporary files (e.g., scratch work, exploratory computations, scripts), save them in:
```
{output_dir}/tmp/
```
Create this directory if it does not exist. Do NOT place temporary files anywhere else.
