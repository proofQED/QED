# Verdict Task: Proof Verification

> **Agentic task.** Read the input file(s) first, then evaluate. The input file path(s) are at the end of this prompt.

## Decision Criteria

Reply with ONLY the single word **'DONE'** if ALL of the following criteria are satisfied:

1. **Problem-Proof Alignment:** The proof addresses the correct problem and proves exactly what was asked
2. **Logical Validity:** Every logical step is valid with no gaps
3. **Completeness:** All cases are covered, all claims are justified (uncertain means failed)
4. **Correctness:** All mathematical claims, computations, and cited results are correct
5. **Overall Verdict in the verification file is PASS**

Be strict and very conservative!

Reply with ONLY the single word **'CONTINUE'** otherwise.

## Important

- **If any tool or script you run takes longer than 3 minutes, stop it and try a different approach or skip that computation.**
- Your response must be exactly one word: either `DONE` or `CONTINUE`
- Do not include any explanation or additional text
- If the verification result file is empty or missing, reply `CONTINUE`
- If any single criterion fails, reply `CONTINUE`

---

## HERE ARE THE INPUT FILE PATH(S):

Read ALL verification result file(s) listed below. If there are multiple files,
each is an independent verification of the same proof by a different agent.
**If ANY verification report has Overall Verdict = FAIL, reply CONTINUE.**

{verification_result_file}
