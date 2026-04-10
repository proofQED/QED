#!/usr/bin/env python3
"""
Smoke test: validates prompt loading and agent connectivity
without running the full proof loop (which is expensive).
"""

import argparse
import asyncio
import json
import os
import sys
import tempfile

import yaml

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from pipeline import load_prompt, make_claude_options


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
    prompt_files = [
        "literature_survey.md", "proof_search.md",
        "proof_verify_structural.md", "proof_verify_detailed.md",
        "proof_verify_easy.md",
        "proof_select.md", "verdict_proof.md", "proof_effort_summary.md",
    ]
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
            error_file="/tmp/test_output/error_literature_survey.md",
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
            prev_round_human_help_dir="",
            skill_file=os.path.join(skill_dir, "super_math_skill.md"),
            scratch_pad_file="/tmp/test_output/verification/round_1/scratch_pad.md",
            error_file="/tmp/test_output/verification/round_1/error_proof_search.md",
        )
        check("proof_search.md renders OK", "test_problem.tex" in prompt)
        check("No unresolved placeholders", "{problem_file}" not in prompt, "Found unresolved {problem_file}")
    except Exception as e:
        check("proof_search.md renders OK", False, str(e))

    try:
        prompt = load_prompt(
            prompts_dir, "proof_verify_structural.md",
            problem_file="/tmp/test_problem.tex",
            proof_file="/tmp/test_proof.md",
            output_file="/tmp/test_verify_structural.md",
            output_dir="/tmp/test_output",
            error_file="/tmp/test_output/verification/round_1/error_proof_verify_structural.md",
        )
        check("proof_verify_structural.md renders OK", "test_problem.tex" in prompt)
        check("proof_verify_structural.md has Phase 1", "Phase 1" in prompt)
        check("proof_verify_structural.md has Phase 3", "Phase 3" in prompt)
        check("proof_verify_structural.md has no Phase 4 section",
              "### Phase 4" not in prompt and "## Phase 4" not in prompt)
    except Exception as e:
        check("proof_verify_structural.md renders OK", False, str(e))

    try:
        prompt = load_prompt(
            prompts_dir, "proof_verify_detailed.md",
            problem_file="/tmp/test_problem.tex",
            proof_file="/tmp/test_proof.md",
            structural_report_file="/tmp/test_structural_report.md",
            output_file="/tmp/test_verify_detailed.md",
            output_dir="/tmp/test_output",
            error_file="/tmp/test_output/verification/round_1/error_proof_verify_detailed.md",
        )
        check("proof_verify_detailed.md renders OK", "test_problem.tex" in prompt)
        check("proof_verify_detailed.md has Phase 4", "Phase 4" in prompt)
        check("proof_verify_detailed.md references structural report",
              "test_structural_report.md" in prompt)
    except Exception as e:
        check("proof_verify_detailed.md renders OK", False, str(e))

    try:
        prompt = load_prompt(
            prompts_dir, "proof_verify_easy.md",
            problem_file="/tmp/test_problem.tex",
            proof_file="/tmp/test_proof.md",
            output_file="/tmp/test_verify.md",
            output_dir="/tmp/test_output",
            error_file="/tmp/test_output/verification/round_1/error_proof_verify_easy.md",
        )
        check("proof_verify_easy.md renders OK", "test_problem.tex" in prompt)
    except Exception as e:
        check("proof_verify_easy.md renders OK", False, str(e))

    try:
        prompt = load_prompt(
            prompts_dir, "verdict_proof.md",
            verification_result_file="/tmp/test_verify.md",
        )
        check("verdict_proof.md renders OK", "test_verify.md" in prompt)
    except Exception as e:
        check("verdict_proof.md renders OK", False, str(e))

    try:
        prompt = load_prompt(
            prompts_dir, "proof_effort_summary.md",
            output_dir="/tmp/test_output",
            outcome="PASS",
            total_rounds=3,
            max_rounds=9,
            summary_file="/tmp/test_output/proof_effort_summary.md",
            error_file="/tmp/test_output/error_proof_effort_summary.md",
        )
        check("proof_effort_summary.md renders OK", "test_output" in prompt)
    except Exception as e:
        check("proof_effort_summary.md renders OK", False, str(e))

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
    # Test 5: Claude CLI connectivity (provider-aware)
    # -------------------------------------------------------
    import shutil
    import subprocess

    provider = claude_cfg.get("provider", "subscription")
    print(f"\n=== Test 5: Claude CLI connectivity (provider: {provider}) ===")

    # Detect if ~/.claude/settings.json injects provider vars that would
    # override the config.yaml provider selection.
    _PROVIDER_VARS = ("CLAUDE_CODE_USE_BEDROCK", "ANTHROPIC_API_KEY",
                      "AWS_PROFILE", "ANTHROPIC_MODEL")
    cli_settings_path = os.path.join(os.path.expanduser("~"), ".claude", "settings.json")
    cli_settings_conflict = False
    if os.path.exists(cli_settings_path):
        import json as _json_settings
        try:
            with open(cli_settings_path) as _sf:
                cli_settings = _json_settings.load(_sf)
            settings_env = cli_settings.get("env", {})
            if provider == "subscription" and settings_env.get("CLAUDE_CODE_USE_BEDROCK"):
                check("No settings.json provider conflict", False,
                      "~/.claude/settings.json has CLAUDE_CODE_USE_BEDROCK set — "
                      "the CLI will use Bedrock regardless of config.yaml provider='subscription'. "
                      "Either set provider to 'bedrock' in config.yaml, or remove "
                      "CLAUDE_CODE_USE_BEDROCK from ~/.claude/settings.json")
                cli_settings_conflict = True
            elif provider == "subscription" and settings_env.get("ANTHROPIC_API_KEY"):
                check("No settings.json provider conflict", False,
                      "~/.claude/settings.json has ANTHROPIC_API_KEY set — "
                      "the CLI will use API key auth regardless of config.yaml provider='subscription'. "
                      "Either set provider to 'api_key' in config.yaml, or remove "
                      "ANTHROPIC_API_KEY from ~/.claude/settings.json")
                cli_settings_conflict = True
            elif provider == "api_key" and settings_env.get("CLAUDE_CODE_USE_BEDROCK"):
                check("No settings.json provider conflict", False,
                      "~/.claude/settings.json has CLAUDE_CODE_USE_BEDROCK set — "
                      "the CLI may use Bedrock instead of your API key. "
                      "Remove CLAUDE_CODE_USE_BEDROCK from ~/.claude/settings.json")
                cli_settings_conflict = True
            else:
                check("No settings.json provider conflict", True)
        except Exception:
            pass  # Can't read settings — not a conflict we can detect

    cli_path = claude_cfg.get("cli_path", "claude")
    if shutil.which(cli_path) is not None:
        check(f"Claude CLI '{cli_path}' found", True)

        # Build options via make_claude_options so we get the right model
        # and env vars for the configured provider.
        try:
            opts = make_claude_options(claude_cfg, tempfile.mkdtemp())
        except ValueError as e:
            check(f"Claude config valid ({provider})", False, str(e))
            opts = None

        if opts is not None:
            check(f"Claude config valid ({provider})", True)

            # Strip vars that cause provider cross-contamination, then
            # add back only the ones for the configured provider.
            clean_env = {k: v for k, v in os.environ.items()
                         if k not in _PROVIDER_VARS}
            clean_env.update(opts["env"])

            model = opts["model"]
            try:
                result = subprocess.run(
                    [cli_path, "-p", "--output-format", "json",
                     "--model", model,
                     "Reply with exactly: SMOKE_TEST_OK"],
                    stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                    env=clean_env, text=True, timeout=60,
                )
                import json as _json
                try:
                    data = _json.loads(result.stdout)
                    text = data.get("result", "")
                except (ValueError, KeyError):
                    text = result.stdout
                check(f"Claude CLI responds ({provider}, model={model})",
                      len(text) > 0,
                      f"Empty response, stderr: {result.stderr[:200]}")
                check("Claude CLI response valid",
                      "SMOKE_TEST_OK" in text.upper() or "smoke" in text.lower(),
                      f"Got: {text[:100]}")
            except subprocess.TimeoutExpired:
                check(f"Claude CLI responds ({provider})", False, "Timed out after 60s")
            except Exception as e:
                check(f"Claude CLI connectivity ({provider})", False, str(e))
    else:
        check(f"Claude CLI '{cli_path}' found", False,
              "Install claude CLI: npm install -g @anthropic-ai/claude-code")

    # -------------------------------------------------------
    # Test 6: Config validation
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
            verification_reports_block="**Claude's proof verification(s):**\n- `/tmp/round_1/claude/verification_result.md`",
            proof_claude="/tmp/round_1/claude/proof.md",
            proof_codex="/tmp/round_1/codex/proof.md",
            proof_gemini="/tmp/round_1/gemini/proof.md",
            selection_file="/tmp/round_1/selection.md",
            error_file="/tmp/round_1/error_proof_select.md",
        )
        check("proof_select.md renders OK", "selection" in prompt.lower())
    except Exception as e:
        check("proof_select.md renders OK", False, str(e))

    # -------------------------------------------------------
    # Test 7b: Verification agents config validation
    # -------------------------------------------------------
    va_cfg = pipeline_cfg.get("verification_agents", {})
    if va_cfg.get("enabled", False):
        va_providers = va_cfg.get("providers", ["claude"])
        valid_names = {"claude", "codex", "gemini"}
        all_valid = all(p in valid_names for p in va_providers)
        check("verification_agents.providers valid",
              all_valid, f"Invalid providers: {va_providers}")
    else:
        check("verification_agents config present (disabled OK)",
              True, "verification_agents not enabled — Claude-only verification")

    # -------------------------------------------------------
    # Test 7c: Multi-model providers config validation
    # -------------------------------------------------------
    mm_cfg = pipeline_cfg.get("multi_model", {})
    check("multi_model config present", "enabled" in mm_cfg, "Missing pipeline.multi_model in config")
    if mm_cfg.get("enabled", False):
        mm_providers = mm_cfg.get("providers", ["claude", "codex", "gemini"])
        valid_names = {"claude", "codex", "gemini"}
        all_valid = all(p in valid_names for p in mm_providers)
        check("multi_model.providers valid",
              all_valid, f"Invalid providers: {mm_providers}")
    else:
        check("multi_model config present (disabled OK)",
              True, "multi_model not enabled — Claude-only proof search")

    # -------------------------------------------------------
    # Test 7d: Auxiliary agent providers config validation
    # -------------------------------------------------------
    print("\n=== Test 7d: Auxiliary agent providers ===")
    valid_providers = {"claude", "codex", "gemini"}
    auxiliary_agents = [
        ("literature_survey", "Literature Survey"),
        ("proof_select", "Proof Selection"),
        ("proof_summary", "Proof Summary"),
    ]
    for agent_key, agent_name in auxiliary_agents:
        agent_cfg = pipeline_cfg.get(agent_key, {})
        provider = agent_cfg.get("provider", "claude")
        is_valid = provider.lower() in valid_providers
        check(f"{agent_key}.provider valid ({provider})",
              is_valid, f"Invalid provider '{provider}' for {agent_name}")

    # -------------------------------------------------------
    # Test 8: Non-Claude provider connectivity (when needed)
    # -------------------------------------------------------

    # Collect all providers that need connectivity testing
    providers_to_test = set()

    # From multi_model (parallel proof search)
    if mm_cfg.get("enabled", False):
        mm_providers = mm_cfg.get("providers", ["claude", "codex", "gemini"])
        for p in mm_providers:
            if p != "claude":  # Claude is tested separately in Test 5
                providers_to_test.add(p)

    # From verification_agents
    va_cfg = pipeline_cfg.get("verification_agents", {})
    if va_cfg.get("enabled", False):
        for p in va_cfg.get("providers", []):
            if p != "claude":  # Claude is tested separately in Test 5
                providers_to_test.add(p)

    # From auxiliary agents
    for agent_key, _ in auxiliary_agents:
        agent_cfg = pipeline_cfg.get(agent_key, {})
        provider = agent_cfg.get("provider", "claude").lower()
        if provider != "claude":
            providers_to_test.add(provider)

    if providers_to_test:
        print(f"\n=== Test 8: Non-Claude provider connectivity (testing: {', '.join(sorted(providers_to_test))}) ===")
        import json as _json_test

        # --- Codex ---
        if "codex" in providers_to_test:
            codex_cfg = config.get("codex", {})
            codex_cli = codex_cfg.get("cli_path", "codex")
            if shutil.which(codex_cli) is not None:
                try:
                    codex_model = codex_cfg.get("model", "gpt-5.4")
                    codex_result = subprocess.run(
                        [codex_cli, "--search", "-m", codex_model,
                         "exec", "--json", "--dangerously-bypass-approvals-and-sandbox",
                         "Reply with exactly: SMOKE_TEST_OK"],
                        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                        text=True, timeout=120, cwd=tempfile.mkdtemp(),
                    )
                    check("Codex CLI exits cleanly", codex_result.returncode == 0,
                          f"Exit code {codex_result.returncode}, stderr: {codex_result.stderr[:200]}")
                    codex_resp = codex_result.stdout.strip()
                    check("Codex responds", len(codex_resp) > 0, "Empty response")
                    check("Codex response valid",
                          "smoke" in codex_resp.lower() or "ok" in codex_resp.lower() or len(codex_resp) > 5,
                          f"Got: {codex_resp[:100]}")
                except subprocess.TimeoutExpired:
                    check("Codex connectivity", False, "Timed out after 120s")
                except Exception as e:
                    check("Codex connectivity", False, str(e))
            else:
                check(f"Codex CLI '{codex_cli}' found", False,
                      "Install codex or disable codex in verification_agents.providers / multi_model")

        # --- Gemini ---
        if "gemini" in providers_to_test:
            gemini_cfg = config.get("gemini", {})
            gemini_cli = gemini_cfg.get("cli_path", "gemini")
            gemini_api_key = gemini_cfg.get("api_key", "")
            if shutil.which(gemini_cli) is not None:
                try:
                    gemini_model = gemini_cfg.get("model", "gemini-3-flash-preview")
                    gemini_approval_mode = gemini_cfg.get("approval_mode", "yolo")
                    gemini_thinking_level = gemini_cfg.get("thinking_level", "")
                    gemini_thinking_budget = gemini_cfg.get("thinking_budget")
                    gemini_env = os.environ.copy()
                    if gemini_api_key:
                        gemini_env["GEMINI_API_KEY"] = gemini_api_key

                    thinking_config = {}
                    if gemini_thinking_level:
                        thinking_config["thinkingLevel"] = gemini_thinking_level
                    if gemini_thinking_budget is not None:
                        thinking_config["thinkingBudget"] = gemini_thinking_budget

                    if thinking_config:
                        with tempfile.TemporaryDirectory(prefix="qed-gemini-home-") as gemini_home:
                            settings_dir = os.path.join(gemini_home, ".gemini")
                            os.makedirs(settings_dir, exist_ok=True)
                            settings_path = os.path.join(settings_dir, "settings.json")
                            settings = {
                                "modelConfigs": {
                                    "overrides": [
                                        {
                                            "match": {"model": gemini_model},
                                            "modelConfig": {
                                                "generateContentConfig": {
                                                    "thinkingConfig": thinking_config,
                                                }
                                            },
                                        }
                                    ]
                                }
                            }
                            with open(settings_path, "w", encoding="utf-8") as f:
                                json.dump(settings, f)
                            gemini_env["GEMINI_CLI_HOME"] = gemini_home
                            gemini_result = subprocess.run(
                                [gemini_cli, "-m", gemini_model, "--approval-mode", gemini_approval_mode, "-o", "json",
                                 "-p", "Reply with exactly: SMOKE_TEST_OK"],
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                text=True, timeout=120, env=gemini_env,
                            )
                    else:
                        gemini_result = subprocess.run(
                            [gemini_cli, "-m", gemini_model, "--approval-mode", gemini_approval_mode, "-o", "json",
                             "-p", "Reply with exactly: SMOKE_TEST_OK"],
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            text=True, timeout=120, env=gemini_env,
                        )
                    check("Gemini CLI exits cleanly", gemini_result.returncode == 0,
                          f"Exit code {gemini_result.returncode}, stderr: {gemini_result.stderr[:300]}")
                    # Parse JSON response
                    gemini_resp = ""
                    try:
                        gemini_data = _json_test.loads(gemini_result.stdout)
                        gemini_resp = gemini_data.get("response", "")
                    except (ValueError, KeyError):
                        gemini_resp = gemini_result.stdout.strip()
                    check("Gemini responds", len(gemini_resp) > 0,
                          f"Empty response, stdout: {gemini_result.stdout[:200]}")
                    check("Gemini response valid",
                          "smoke" in gemini_resp.lower() or "ok" in gemini_resp.lower() or len(gemini_resp) > 5,
                          f"Got: {gemini_resp[:100]}")
                except subprocess.TimeoutExpired:
                    check("Gemini connectivity", False, "Timed out after 120s")
                except Exception as e:
                    check("Gemini connectivity", False, str(e))
            else:
                check(f"Gemini CLI '{gemini_cli}' found", False,
                      "Install gemini or disable gemini in verification_agents.providers / multi_model")
    else:
        print("\n=== Test 8: Non-Claude provider connectivity [SKIPPED — no non-Claude providers enabled] ===")

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
