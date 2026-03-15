#!/usr/bin/env python3
"""
Smoke test: validates prompt loading and agent connectivity
without running the full proof loop (which is expensive).
"""

import argparse
import asyncio
import os
import sys
import tempfile

import yaml

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from pipeline import load_prompt, make_claude_options
from agent_framework.anthropic import ClaudeAgent


async def main():
    parser = argparse.ArgumentParser(description="Smoke test for the proof agent pipeline")
    parser.add_argument("--problem", help="Optional path to a problem.tex file for testing", default=None)
    args = parser.parse_args()

    script_dir = os.path.dirname(os.path.abspath(__file__))
    project_base = os.path.dirname(script_dir)  # proof_agent/
    config_path = os.path.join(project_base, "config.yaml")
    prompts_dir = os.path.join(project_base, "prompts")
    skill_dir = os.path.join(project_base, "skill")

    with open(config_path) as f:
        config = yaml.safe_load(f)

    claude_cfg = config.get("claude", {})

    passed = 0
    failed = 0

    def check(name: str, condition: bool, detail: str = ""):
        nonlocal passed, failed
        if condition:
            print(f"  PASS: {name}")
            passed += 1
        else:
            print(f"  FAIL: {name} -- {detail}")
            failed += 1

    # -------------------------------------------------------
    # Test 1: Prompt files exist
    # -------------------------------------------------------
    print("\n=== Test 1: Prompt files ===")
    prompt_files = ["proof_search.md", "proof_verify.md", "verdict_proof.md"]
    for pf in prompt_files:
        exists = os.path.exists(os.path.join(prompts_dir, pf))
        check(f"Prompt {pf} exists", exists)

    # -------------------------------------------------------
    # Test 2: Skill files exist
    # -------------------------------------------------------
    print("\n=== Test 2: Skill files ===")
    skill_files = ["proving_skill.md", "super_math_skill.md"]
    for sf in skill_files:
        exists = os.path.exists(os.path.join(skill_dir, sf))
        check(f"Skill {sf} exists", exists)

    # -------------------------------------------------------
    # Test 3: Prompt loading with variable substitution
    # -------------------------------------------------------
    print("\n=== Test 3: Prompt loading ===")
    try:
        prompt = load_prompt(
            prompts_dir, "proof_search.md",
            problem_file="/tmp/test_problem.tex",
            proof_file="/tmp/test_proof.md",
            output_dir="/tmp/test_output",
            round_num=1,
            proof_status_file="/tmp/test_status.md",
            previous_round_instructions="- This is the first round.",
        )
        check("proof_search.md renders OK", "test_problem.tex" in prompt)
        check("No unresolved placeholders", "{problem_file}" not in prompt, "Found unresolved {problem_file}")
    except Exception as e:
        check("proof_search.md renders OK", False, str(e))

    try:
        prompt = load_prompt(
            prompts_dir, "proof_verify.md",
            problem_file="/tmp/test_problem.tex",
            proof_file="/tmp/test_proof.md",
            output_file="/tmp/test_verify.md",
        )
        check("proof_verify.md renders OK", "test_problem.tex" in prompt)
    except Exception as e:
        check("proof_verify.md renders OK", False, str(e))

    try:
        prompt = load_prompt(
            prompts_dir, "verdict_proof.md",
            verification_result_file="/tmp/test_verify.md",
        )
        check("verdict_proof.md renders OK", "test_verify.md" in prompt)
    except Exception as e:
        check("verdict_proof.md renders OK", False, str(e))

    # -------------------------------------------------------
    # Test 4: Skill loading
    # -------------------------------------------------------
    print("\n=== Test 4: Skill loading ===")
    proving_skill_path = os.path.join(skill_dir, "proving_skill.md")
    math_skill_path = os.path.join(skill_dir, "super_math_skill.md")
    try:
        with open(proving_skill_path) as f:
            proving_skill = f.read()
        proving_skill = proving_skill.replace("{math_skill_file}", math_skill_path)
        check("Proving skill loads", len(proving_skill) > 100)
        check("Math skill path substituted", math_skill_path in proving_skill)
    except Exception as e:
        check("Skill loading", False, str(e))

    # -------------------------------------------------------
    # Test 5: ClaudeAgent connectivity
    # -------------------------------------------------------
    print("\n=== Test 5: ClaudeAgent connectivity ===")
    with tempfile.TemporaryDirectory() as tmpdir:
        claude_opts = make_claude_options(claude_cfg, tmpdir)
        try:
            async with ClaudeAgent(default_options=claude_opts) as agent:
                response = await agent.run("Reply with exactly: SMOKE_TEST_OK")
                text = response.text or ""
                check("Agent responds", len(text) > 0, "Empty response")
                check("Agent response contains expected text",
                      "SMOKE_TEST_OK" in text.upper() or "smoke" in text.lower(),
                      f"Got: {text[:100]}")
        except Exception as e:
            check("Agent connectivity", False, str(e))

    # -------------------------------------------------------
    # Test 6: Config loading
    # -------------------------------------------------------
    print("\n=== Test 6: Config validation ===")
    pipeline_cfg = config.get("pipeline", {})
    check("max_proof_iterations set", "max_proof_iterations" in pipeline_cfg,
          "Missing max_proof_iterations in pipeline config")
    check("claude config present", "claude" in config, "Missing claude config")

    # -------------------------------------------------------
    # Summary
    # -------------------------------------------------------
    print(f"\n{'=' * 60}")
    print(f"SMOKE TEST RESULTS: {passed} passed, {failed} failed")
    print(f"{'=' * 60}")

    if failed > 0:
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
