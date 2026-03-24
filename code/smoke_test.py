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


async def run_smoke_test(config: dict, config_path: str | None = None) -> bool:
    """Run all smoke tests. Returns True if all passed, False if any failed.

    Can be called from pipeline.py at startup, or standalone via main().

    Args:
        config: Parsed config dict (from yaml.safe_load).
        config_path: Path to config.yaml (used to resolve project paths).
            If None, resolves from this file's location.
    """
    # Resolve project root: config.yaml lives at project_base/config.yaml
    if config_path:
        project_base = os.path.dirname(os.path.abspath(config_path))
    else:
        script_dir = os.path.dirname(os.path.abspath(__file__))
        project_base = os.path.dirname(script_dir)

    prompts_dir = os.path.join(project_base, "prompts")
    skill_dir = os.path.join(project_base, "skill")

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
    prompt_files = ["literature_survey.md", "proof_search.md", "proof_verify.md", "verdict_proof.md"]
    for pf in prompt_files:
        exists = os.path.exists(os.path.join(prompts_dir, pf))
        check(f"Prompt {pf} exists", exists)

    # -------------------------------------------------------
    # Test 2: Skill files exist
    # -------------------------------------------------------
    print("\n=== Test 2: Skill files ===")
    skill_files = ["super_math_skill.md"]
    for sf in skill_files:
        exists = os.path.exists(os.path.join(skill_dir, sf))
        check(f"Skill {sf} exists", exists)

    # -------------------------------------------------------
    # Test 3: Prompt loading with variable substitution
    # -------------------------------------------------------
    print("\n=== Test 3: Prompt loading ===")
    try:
        prompt = load_prompt(
            prompts_dir, "literature_survey.md",
            problem_file="/tmp/test_problem.tex",
            related_info_dir="/tmp/test_output/related_info",
            output_dir="/tmp/test_output",
        )
        check("literature_survey.md renders OK", "test_problem.tex" in prompt)
    except Exception as e:
        check("literature_survey.md renders OK", False, str(e))

    try:
        prompt = load_prompt(
            prompts_dir, "proof_search.md",
            problem_file="/tmp/test_problem.tex",
            proof_file="/tmp/test_proof.md",
            output_dir="/tmp/test_output",
            related_info_dir="/tmp/test_output/related_info",
            round_num=1,
            proof_status_file="/tmp/test_status.md",
            previous_round_instructions="- This is the first round.",
            human_help_dir="/tmp/human_help",
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
            decomposition_file="/tmp/test_decomp.md",
            output_file="/tmp/test_verify.md",
            output_dir="/tmp/test_output",
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
    math_skill_path = os.path.join(skill_dir, "super_math_skill.md")
    try:
        with open(math_skill_path) as f:
            math_skill = f.read()
        check("Math skill loads", len(math_skill) > 100)
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
    # Test 7: Multi-model prompt (proof_select.md)
    # -------------------------------------------------------
    print("\n=== Test 7: Selector prompt ===")
    select_prompt_path = os.path.join(prompts_dir, "proof_select.md")
    check("proof_select.md exists", os.path.exists(select_prompt_path))
    try:
        prompt = load_prompt(
            prompts_dir, "proof_select.md",
            problem_file="/tmp/test_problem.tex",
            verify_claude="/tmp/round_1/claude/verification_result.md",
            verify_codex="/tmp/round_1/codex/verification_result.md",
            verify_gemini="/tmp/round_1/gemini/verification_result.md",
            proof_claude="/tmp/round_1/claude/proof.md",
            proof_codex="/tmp/round_1/codex/proof.md",
            proof_gemini="/tmp/round_1/gemini/proof.md",
            selection_file="/tmp/round_1/selection.md",
        )
        check("proof_select.md renders OK", "selection" in prompt.lower())
    except Exception as e:
        check("proof_select.md renders OK", False, str(e))

    # -------------------------------------------------------
    # Test 8: Multi-model connectivity (when enabled)
    # -------------------------------------------------------
    mm_cfg = config.get("pipeline", {}).get("multi_model", {})
    check("multi_model config present", "enabled" in mm_cfg, "Missing pipeline.multi_model in config")

    import shutil
    if mm_cfg.get("enabled", False):
        print("\n=== Test 8: Multi-model connectivity (enabled in config) ===")
        from model_runner import run_codex_agent, run_gemini_agent

        # --- Codex ---
        codex_cfg = config.get("codex", {})
        codex_cli = codex_cfg.get("cli_path", "codex")
        if shutil.which(codex_cli) is not None:
            try:
                codex_resp = await run_codex_agent(
                    "Reply with exactly: SMOKE_TEST_OK",
                    tempfile.mkdtemp(), codex_cfg,
                )
                check("Codex responds", len(codex_resp) > 0, "Empty response")
                check("Codex response valid",
                      "smoke" in codex_resp.lower() or "ok" in codex_resp.lower() or len(codex_resp) > 5,
                      f"Got: {codex_resp[:100]}")
            except Exception as e:
                check("Codex connectivity", False, str(e))
        else:
            check(f"Codex CLI '{codex_cli}' found", False,
                  "Install codex or set multi_model.enabled to false")

        # --- Gemini ---
        gemini_cfg = config.get("gemini", {})
        gemini_cli = gemini_cfg.get("cli_path", "gemini")
        if shutil.which(gemini_cli) is not None:
            try:
                gemini_resp = await run_gemini_agent(
                    "Reply with exactly: SMOKE_TEST_OK",
                    tempfile.mkdtemp(), gemini_cfg,
                )
                check("Gemini responds", len(gemini_resp) > 0, "Empty response")
                check("Gemini response valid",
                      "smoke" in gemini_resp.lower() or "ok" in gemini_resp.lower() or len(gemini_resp) > 5,
                      f"Got: {gemini_resp[:100]}")
            except Exception as e:
                check("Gemini connectivity", False, str(e))
        else:
            check(f"Gemini CLI '{gemini_cli}' found", False,
                  "Install gemini or set multi_model.enabled to false")
    else:
        print("\n=== Test 8: Multi-model connectivity [SKIPPED — disabled in config] ===")

    # -------------------------------------------------------
    # Summary
    # -------------------------------------------------------
    print(f"\n{'=' * 60}")
    print(f"SMOKE TEST RESULTS: {passed} passed, {failed} failed")
    print(f"{'=' * 60}")

    return failed == 0


async def main():
    parser = argparse.ArgumentParser(description="Smoke test for the proof agent pipeline")
    parser.add_argument("--config", help="Path to config.yaml", default=None)
    args = parser.parse_args()

    script_dir = os.path.dirname(os.path.abspath(__file__))
    project_base = os.path.dirname(script_dir)
    config_path = args.config or os.path.join(project_base, "config.yaml")

    with open(config_path) as f:
        config = yaml.safe_load(f)

    ok = await run_smoke_test(config, config_path)
    if not ok:
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
