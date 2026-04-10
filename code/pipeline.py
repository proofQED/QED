#!/usr/bin/env python3
"""
Proof Agent pipeline: takes a problem.tex (LaTeX) and produces a natural-language proof.

Three-stage pipeline:
  Stage 0 — Literature Survey agent: deep-dives into the problem context and related results
  Stage 1 — Proof Search Loop (iterative, up to max_proof_iterations rounds):
    1a. Proof Search agent    — writes/refines the proof (informed by the survey)
    1b. Verification agent    — directly verifies the proof for correctness
    1c. Verdict agent         — decides DONE or CONTINUE
  Stage 2 — Summary agent: reads all generated files and writes proof_effort_summary.md

Supports resuming interrupted runs: detects prior progress on disk, skips
completed stages, deletes incomplete rounds, and restores proof.md from backups.
"""

import asyncio
import argparse
import json
import os
import shutil
import subprocess
import sys
from datetime import datetime

import yaml


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def load_prompt(prompts_dir: str, name: str, **kwargs) -> str:
    """Load a prompt template and fill placeholders."""
    path = os.path.join(prompts_dir, name)
    with open(path) as f:
        template = f.read()
    return template.format(**kwargs)


def make_claude_options(claude_cfg: dict, working_dir: str) -> dict:
    """Build options dict for the Claude CLI subprocess runner.

    Supports three providers:
      - "subscription": Claude Pro/Max subscription (no keys, shorthand model names)
      - "bedrock": AWS Bedrock (requires AWS credentials)
      - "api_key": Anthropic API key (requires ANTHROPIC_API_KEY)
    """
    provider = claude_cfg.get("provider", "subscription")
    env = {}

    if provider == "subscription":
        sub_cfg = claude_cfg.get("subscription", {})
        model = sub_cfg.get("model", "opus")
    elif provider == "api_key":
        api_cfg = claude_cfg.get("api_key", {})
        model = api_cfg.get("model", "claude-opus-4-6-20250609")
        key = api_cfg.get("key", "")
        if not key:
            raise ValueError("config.yaml: claude.api_key.key is empty. Set your Anthropic API key.")
        env["ANTHROPIC_API_KEY"] = key
    elif provider == "bedrock":
        bedrock_cfg = claude_cfg.get("bedrock", {})
        model = bedrock_cfg.get("model", "us.anthropic.claude-opus-4-6-v1[1m]")
        env["CLAUDE_CODE_USE_BEDROCK"] = "1"
        env["AWS_PROFILE"] = bedrock_cfg.get("aws_profile", "default")
    else:
        raise ValueError(f"config.yaml: unknown claude.provider '{provider}'. Use 'subscription', 'bedrock', or 'api_key'.")

    return {
        "cli_path": claude_cfg.get("cli_path", "claude"),
        "model": model,
        "cwd": working_dir,
        "env": env,
    }


def check_prerequisites():
    """Check that required tools are available."""
    missing = []
    for cmd in ["claude", "python3"]:
        if shutil.which(cmd) is None:
            missing.append(cmd)
    if missing:
        print(f"ERROR: Missing required tools: {', '.join(missing)}")
        print("Please install them before running the pipeline.")
        sys.exit(1)
    try:
        import yaml as _y  # noqa: F401
    except ImportError:
        missing.append("pyyaml (pip install pyyaml)")
    if missing:
        print(f"ERROR: Missing Python packages: {', '.join(missing)}")
        sys.exit(1)


def check_multi_model_providers(config: dict) -> list[str]:
    """Check which multi-model providers are available for proof search.

    Reads config["pipeline"]["multi_model"]["providers"]. If not specified,
    defaults to all available providers (claude + any installed codex/gemini).
    When providers are explicitly specified, exits with error if any CLI is missing.

    Returns a list of available providers.
    """
    mm_cfg = config.get("pipeline", {}).get("multi_model", {})
    requested = mm_cfg.get("providers", None)

    # If providers not specified, use all available (backward compatible)
    if requested is None:
        providers = ["claude"]
        for name, cfg_key in [("codex", "codex"), ("gemini", "gemini")]:
            cli = config.get(cfg_key, {}).get("cli_path", name)
            if shutil.which(cli) is not None:
                providers.append(name)
            else:
                print(f"  WARNING: '{cli}' CLI not found — {name} will be excluded from multi-model runs")
        return providers

    # Filter requested providers to those that are available
    available = []
    missing = []
    for name in requested:
        if name == "claude":
            available.append("claude")
            continue
        cli = config.get(name, {}).get("cli_path", name)
        if shutil.which(cli) is not None:
            available.append(name)
        else:
            missing.append(name)
    if missing:
        print(f"  ERROR: CLI not found for configured proof search providers: {', '.join(missing)}")
        print(f"         Install the missing CLI(s) or remove them from multi_model.providers in config.yaml")
        sys.exit(1)
    if not available:
        print(f"  ERROR: No proof search providers configured in multi_model.providers")
        sys.exit(1)
    return available


def check_verification_providers(config: dict) -> list[str]:
    """Return available verification providers from config.

    Reads config["pipeline"]["verification_agents"]. When disabled or absent,
    returns ["claude"]. Otherwise filters the requested list to CLIs that are
    actually installed. Exits with error if any configured provider's CLI is missing.
    """
    va_cfg = config.get("pipeline", {}).get("verification_agents", {})
    if not va_cfg.get("enabled", False):
        return ["claude"]
    requested = va_cfg.get("providers", ["claude"])
    available = []
    missing = []
    for name in requested:
        if name == "claude":
            available.append("claude")
            continue
        cli = config.get(name, {}).get("cli_path", name)
        if shutil.which(cli) is not None:
            available.append(name)
        else:
            missing.append(name)
    if missing:
        print(f"  ERROR: CLI not found for configured verification providers: {', '.join(missing)}")
        print(f"         Install the missing CLI(s) or remove them from verification_agents.providers in config.yaml")
        sys.exit(1)
    if not available:
        print(f"  ERROR: No verification providers configured in verification_agents.providers")
        sys.exit(1)
    return available


def _verification_filename(verifier: str, multi_verifier: bool) -> str:
    """Return the verification result filename for *verifier*.

    When *multi_verifier* is True the verifier name is appended as a suffix
    (e.g. ``verification_result_codex.md``). When False the plain name
    ``verification_result.md`` is returned for backward compatibility.
    """
    if multi_verifier:
        return f"verification_result_{verifier}.md"
    return "verification_result.md"


def _file_nonempty(path: str) -> bool:
    """Return True if *path* exists and has non-whitespace content."""
    if not os.path.exists(path):
        return False
    with open(path) as f:
        return bool(f.read().strip())


def _find_verification_files(directory: str) -> list[str]:
    """Find all verification result files in *directory*.

    Returns the single-file name if it exists, otherwise all multi-verifier
    files matching ``verification_result_<provider>.md``.
    """
    single = os.path.join(directory, "verification_result.md")
    if _file_nonempty(single):
        return [single]
    files = []
    if os.path.isdir(directory):
        for name in sorted(os.listdir(directory)):
            if name.startswith("verification_result_") and name.endswith(".md"):
                path = os.path.join(directory, name)
                if _file_nonempty(path):
                    files.append(path)
    return files


def _check_expected_files(
    expected: list[tuple[str, str]],
    logger,
    step_name: str,
) -> None:
    """Verify that all expected output files exist after an agent call.

    Args:
        expected: List of (filepath, description) tuples.
        logger: PipelineLogger instance for logging.
        step_name: Name of the pipeline step (for error messages).

    Raises:
        RuntimeError: If any expected file is missing.
    """
    missing = []
    for path, desc in expected:
        if not os.path.exists(path):
            missing.append((path, desc))
    if missing:
        lines = [f"  - {desc}: {path}" for path, desc in missing]
        msg = f"FATAL — {step_name}: expected output file(s) missing:\n" + "\n".join(lines)
        logger.log(msg)
        logger.append_history(f"{step_name}: FATAL — {len(missing)} expected file(s) missing")
        raise RuntimeError(msg)


def _fallback_save_response(
    response: str,
    primary_files: list[str],
    error_files: list[str],
    logger=None,
    step_name: str = "",
) -> None:
    """Save agent response to missing primary output files as fallback.

    If the agent wrote the file via tool use, this is a no-op.
    If the agent didn't, the text response is saved so work isn't lost.
    When fallback triggers, error log files record that the pipeline
    (not the agent) saved the output.
    """
    fallback_used = []
    for path in primary_files:
        if not os.path.exists(path) and response.strip():
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w") as f:
                f.write(response)
            fallback_used.append(path)
            if logger:
                logger.log(f"  Fallback: saved response to {path}")
    # Write or append fallback notice to error files
    if fallback_used:
        notice = (f"\n\n# Pipeline Fallback Notice\n\n"
                  f"**Step:** {step_name}\n\n"
                  f"The agent did not write the following expected output file(s) "
                  f"via tool use. The pipeline saved the agent's text response "
                  f"as a fallback:\n\n")
        for fb in fallback_used:
            notice += f"- `{fb}`\n"
        notice += f"\nThe content may not be properly formatted for this file.\n"
        for path in error_files:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "a") as f:
                f.write(notice)
    else:
        for path in error_files:
            if not os.path.exists(path):
                os.makedirs(os.path.dirname(path), exist_ok=True)
                with open(path, "w") as f:
                    f.write("")


def _parse_verdict_from_file(path: str) -> str:
    """Parse the Overall Verdict from a verification_result.md file.

    Looks for a line containing 'Overall Verdict' with PASS or FAIL.
    Returns 'PASS', 'FAIL', or 'UNKNOWN'.
    """
    with open(path) as f:
        for line in f:
            if "overall verdict" in line.lower():
                upper = line.upper()
                if "PASS" in upper:
                    return "PASS"
                if "FAIL" in upper:
                    return "FAIL"
    return "UNKNOWN"


def _parse_difficulty(output_dir: str) -> str:
    """Parse the difficulty classification from difficulty_evaluation.md.

    Looks for a line like '## Classification: Easy' (or Medium / Hard).
    Returns 'easy', 'medium', 'hard', or 'unknown'.
    """
    path = os.path.join(output_dir, "related_info", "difficulty_evaluation.md")
    if not os.path.exists(path):
        return "unknown"
    with open(path) as f:
        for line in f:
            if "classification" in line.lower():
                upper = line.upper()
                if "EASY" in upper:
                    return "easy"
                if "MEDIUM" in upper:
                    return "medium"
                if "HARD" in upper:
                    return "hard"
    return "unknown"


def _is_parallel_round(round_dir: str) -> bool:
    """Return True if round_dir contains per-model subdirectories (parallel mode)."""
    return any(
        os.path.isdir(os.path.join(round_dir, m))
        for m in ("claude", "codex", "gemini")
    )


def _parallel_round_complete(round_dir: str) -> bool:
    """Return True if a parallel round has a selection.md (fully complete)."""
    return _file_nonempty(os.path.join(round_dir, "selection.md"))


def detect_resume_state(
    output_dir: str,
    verification_providers: list[str] | None = None,
    proof_providers: list[str] | None = None,
) -> tuple[bool, int, str]:
    """Scan the output directory for progress from a previous run.

    Returns (skip_survey, start_round, resume_from_step):
      - skip_survey: True if the literature survey is already complete.
      - start_round: the round number to start (or resume) the proof loop from.
        1 means no prior rounds exist.
      - resume_from_step: which step to resume from within start_round.

        For **single-model** rounds:
          "proof_search"            — start the round from scratch
          "verification"            — proof search done, resume from structural verification
          "detailed_verification"   — structural done, resume from detailed verification

        For **parallel** rounds:
          "proof_search"                    — start the round from scratch
          "parallel_verification"           — proof searches done, resume from structural verification
          "parallel_detailed_verification"  — structural done, resume from detailed verification
          "parallel_selection"              — all verifications done, resume from selector

    Args:
        output_dir: Path to the output directory.
        verification_providers: List of verifier names (e.g., ["claude", "codex", "gemini"]).
            Used to check that ALL expected verification files exist.
        proof_providers: List of proof search providers (e.g., ["claude", "codex", "gemini"]).
            Used to check parallel round completeness.

    Side effects:
      - Deletes the last round directory if proof search did NOT complete
        (no proof_status.md), and restores proof.md from backup.
    """
    if verification_providers is None:
        verification_providers = ["claude"]
    if proof_providers is None:
        proof_providers = ["claude"]
    # --- Check literature survey completeness ---
    related_info_dir = os.path.join(output_dir, "related_info")
    survey_files = [
        "difficulty_evaluation.md",
        "problem_analysis.md",
        "related_theorems.md",
    ]
    skip_survey = all(
        _file_nonempty(os.path.join(related_info_dir, f)) for f in survey_files
    )

    # --- Scan round directories ---
    verify_dir = os.path.join(output_dir, "verification")
    if not os.path.isdir(verify_dir):
        return skip_survey, 1, "proof_search"

    # Collect round numbers that have a directory
    round_nums: list[int] = []
    for name in os.listdir(verify_dir):
        if name.startswith("round_"):
            try:
                round_nums.append(int(name.split("_", 1)[1]))
            except ValueError:
                continue
    if not round_nums:
        return skip_survey, 1, "proof_search"

    round_nums.sort()
    last = round_nums[-1]
    last_dir = os.path.join(verify_dir, f"round_{last}")

    # ====== Parallel (hard mode) round ======
    if _is_parallel_round(last_dir):
        if _parallel_round_complete(last_dir):
            return skip_survey, last + 1, "proof_search"

        # Helper: check if a directory has ALL expected verification files
        def _has_all_verifications_in(vdir: str) -> bool:
            """Check if vdir has verification results from ALL configured verifiers."""
            if not os.path.isdir(vdir):
                return False
            if len(verification_providers) == 1:
                return _file_nonempty(os.path.join(vdir, "verification_result.md"))
            for v in verification_providers:
                vf = os.path.join(vdir, f"verification_result_{v}.md")
                if not _file_nonempty(vf):
                    return False
            return True

        def _has_all_verifications(mdir: str) -> bool:
            """Check if mdir has verification results (new or old layout)."""
            # New layout: check verification_file/detailed/ first
            if _has_all_verifications_in(os.path.join(mdir, "verification_file", "detailed")):
                return True
            # Old layout: check mdir directly (backward compat)
            return _has_all_verifications_in(mdir)

        def _has_structural_verifications(mdir: str) -> bool:
            """Check if mdir has structural verification results."""
            return _has_all_verifications_in(os.path.join(mdir, "verification_file", "structural"))

        # Check per-model completion to find resume point
        models_with_proof = []
        models_with_detailed = []
        models_with_structural = []
        models_with_any_verify = []
        for m in proof_providers:
            mdir = os.path.join(last_dir, m)
            if not os.path.isdir(mdir):
                continue
            if _file_nonempty(os.path.join(mdir, "proof_status.md")):
                models_with_proof.append(m)
            if _has_all_verifications(mdir):
                models_with_detailed.append(m)
            elif _has_structural_verifications(mdir):
                models_with_structural.append(m)
            elif (_find_verification_files(os.path.join(mdir, "verification_file", "structural"))
                  or _find_verification_files(os.path.join(mdir, "verification_file", "detailed"))
                  or _find_verification_files(mdir)):
                models_with_any_verify.append(m)

        # Check if ALL proof providers have ALL detailed verifications
        all_detailed_complete = (
            len(models_with_detailed) == len(models_with_proof)
            and len(models_with_proof) > 0
            and len(models_with_structural) == 0
            and len(models_with_any_verify) == 0
        )

        if all_detailed_complete:
            print(f"  Round {last}: verifications complete, selection incomplete — will resume from selection")
            return skip_survey, last, "parallel_selection"

        # Check if ALL proof providers have structural verifications (but not detailed)
        all_structural_complete = (
            len(models_with_structural) + len(models_with_detailed) == len(models_with_proof)
            and len(models_with_proof) > 0
            and len(models_with_structural) > 0  # at least one still needs detailed
        )

        if all_structural_complete:
            print(f"  Round {last}: structural complete, detailed incomplete — will resume from detailed verification")
            return skip_survey, last, "parallel_detailed_verification"

        if models_with_any_verify or models_with_structural:
            print(f"  Round {last}: partial verifications found — will resume from verification")
            return skip_survey, last, "parallel_verification"

        if models_with_proof:
            print(f"  Round {last}: proof search complete, verification incomplete — will resume from verification")
            return skip_survey, last, "parallel_verification"

        # No proof search completed — delete and restart
        proof_file = os.path.join(output_dir, "proof.md")
        backup = os.path.join(last_dir, "proof_before_round.md")
        if os.path.exists(backup):
            shutil.copy2(backup, proof_file)
            print(f"  Restored proof.md from round {last} backup")
        shutil.rmtree(last_dir)
        print(f"  Deleted incomplete parallel round_{last}")
        return skip_survey, last, "proof_search"

    # ====== Single-model (easy/medium) round ======
    status_ok = _file_nonempty(os.path.join(last_dir, "proof_status.md"))

    # Check for verification files in new structure, then old structure
    structural_dir = os.path.join(last_dir, "verification_file", "structural")
    detailed_dir = os.path.join(last_dir, "verification_file", "detailed")
    has_detailed = bool(_find_verification_files(detailed_dir))
    has_structural = bool(_find_verification_files(structural_dir))
    has_legacy = bool(_find_verification_files(last_dir))  # backward compat
    verify_ok = has_detailed or has_legacy  # round complete if detailed or legacy exists

    if status_ok and verify_ok:
        # Last round is fully complete — resume from the next one.
        return skip_survey, last + 1, "proof_search"

    if status_ok and has_structural and not has_detailed:
        # Structural done but detailed not started — resume from detailed verification
        print(f"  Round {last}: structural verification complete, detailed incomplete — will resume from detailed verification")
        return skip_survey, last, "detailed_verification"

    if status_ok and not verify_ok:
        print(f"  Round {last}: proof search complete, verification incomplete — will resume from verification")
        return skip_survey, last, "verification"

    # Proof search didn't complete — delete the round and restore proof.md.
    proof_file = os.path.join(output_dir, "proof.md")
    backup = os.path.join(last_dir, "proof_before_round.md")
    if os.path.exists(backup):
        shutil.copy2(backup, proof_file)
        print(f"  Restored proof.md from round {last} backup")

    shutil.rmtree(last_dir)
    print(f"  Deleted incomplete round_{last}")

    # Redo this round from scratch.
    return skip_survey, last, "proof_search"


# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

class PipelineLogger:
    """Persistent logging to AUTO_RUN_STATUS.md, .history, and AUTO_RUN_LOG.txt."""

    def __init__(self, log_dir: str, phase: str):
        os.makedirs(log_dir, exist_ok=True)
        self.log_dir = log_dir
        self.phase = phase
        self.status_file = os.path.join(log_dir, "AUTO_RUN_STATUS.md")
        self.history_file = os.path.join(log_dir, "AUTO_RUN_STATUS.md.history")
        self.log_file = os.path.join(log_dir, "AUTO_RUN_LOG.txt")
        self.start_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        self.pid = os.getpid()

        # Append to history (not truncate) so resumed runs preserve prior history
        self.append_history(f"{phase} started")

    def update_status(self, iteration: int, max_iter: int, step: str, state: str, details: str):
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        history = ""
        if os.path.exists(self.history_file):
            with open(self.history_file) as f:
                history = f.read()
        with open(self.status_file, "w") as f:
            f.write(f"# {self.phase} - Auto Status\n\n")
            f.write("| Field | Value |\n|-------|-------|\n")
            f.write(f"| **Status** | {state} |\n")
            f.write(f"| **Current Iteration** | {iteration} / {max_iter} |\n")
            f.write(f"| **Current Step** | {step} |\n")
            f.write(f"| **Started At** | {self.start_time} |\n")
            f.write(f"| **Last Updated** | {now} |\n")
            f.write(f"| **PID** | {self.pid} |\n\n")
            f.write(f"## Current Activity\n{details}\n\n")
            f.write(f"## Progress History\n{history}\n")

    def append_history(self, msg: str):
        now = datetime.now().strftime("%H:%M:%S")
        with open(self.history_file, "a") as f:
            f.write(f"- [{now}] {msg}\n")

    def log(self, msg: str):
        print(msg)
        with open(self.log_file, "a") as f:
            f.write(msg + "\n")

    def finalize(self, iteration: int, max_iter: int, exit_state: str, details: str):
        self.update_status(iteration, max_iter, exit_state, exit_state, details)
        self.append_history(f"Process ended: {exit_state}")


# ---------------------------------------------------------------------------
# Token usage tracking
# ---------------------------------------------------------------------------

class TokenTracker:
    """Accumulates token usage across all agent calls and persists to disk
    after every update so the user can check TOKEN_USAGE.md at any time.

    Supports multi-provider tracking: each call can specify a provider
    (claude/codex/gemini) and model name. Per-provider subtotals are shown
    in TOKEN_USAGE.md when more than one provider is used.
    """

    def __init__(self, output_dir: str, model: str):
        self.output_dir = output_dir
        self.model = model  # default model label (backward compat)
        self.calls: list[dict] = []
        self.total_input = 0
        self.total_output = 0
        self.total_elapsed = 0.0
        self.per_provider: dict[str, dict] = {}  # provider → {input, output, calls, model}
        self.md_path = os.path.join(output_dir, "TOKEN_USAGE.md")
        self.json_path = os.path.join(output_dir, "token_usage.json")
        self.start_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    def record(self, call_name: str, input_tokens: int, output_tokens: int,
               elapsed: float, provider: str = "claude", model: str = ""):
        self.total_input += input_tokens
        self.total_output += output_tokens
        self.total_elapsed += elapsed

        # Per-provider tracking
        if provider not in self.per_provider:
            self.per_provider[provider] = {
                "input": 0, "output": 0, "calls": 0,
                "model": model or self.model,
            }
        self.per_provider[provider]["input"] += input_tokens
        self.per_provider[provider]["output"] += output_tokens
        self.per_provider[provider]["calls"] += 1

        self.calls.append({
            "call": len(self.calls) + 1,
            "name": call_name,
            "provider": provider,
            "model": model or self.model,
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "elapsed_s": round(elapsed, 1),
            "cumul_input": self.total_input,
            "cumul_output": self.total_output,
            "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        })
        self._save()

    def _save(self):
        """Write both TOKEN_USAGE.md and token_usage.json."""
        # --- Markdown ---
        lines = [
            "# Token Usage\n",
            f"**Primary Model:** `{self.model}`  ",
            f"**Started:** {self.start_time}  ",
            f"**Last updated:** {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}  \n",
            "## Summary\n",
            "| Metric | Value |",
            "|--------|-------|",
            f"| Total input tokens | {self.total_input:,} |",
            f"| Total output tokens | {self.total_output:,} |",
            f"| Total tokens | {self.total_input + self.total_output:,} |",
            f"| Total elapsed | {self.total_elapsed:.0f}s |",
            f"| Agent calls | {len(self.calls)} |\n",
        ]

        # Per-provider summary (only shown when multiple providers used)
        if len(self.per_provider) > 1:
            lines.append("## Per-Provider Summary\n")
            lines.append("| Provider | Model | Input | Output | Total | Calls |")
            lines.append("|----------|-------|------:|-------:|------:|------:|")
            for prov, stats in sorted(self.per_provider.items()):
                total = stats['input'] + stats['output']
                lines.append(
                    f"| {prov} | {stats['model']} "
                    f"| {stats['input']:,} | {stats['output']:,} "
                    f"| {total:,} | {stats['calls']} |"
                )
            lines.append("")

        lines.append("## Per-Call Breakdown\n")
        if len(self.per_provider) > 1:
            lines.append("| # | Agent | Provider | Input | Output | Time | Cumul In | Cumul Out |")
            lines.append("|---|-------|----------|------:|-------:|-----:|---------:|----------:|")
        else:
            lines.append("| # | Agent | Input | Output | Time | Cumul In | Cumul Out |")
            lines.append("|---|-------|------:|-------:|-----:|---------:|----------:|")

        for c in self.calls:
            if len(self.per_provider) > 1:
                lines.append(
                    f"| {c['call']} | {c['name']} | {c.get('provider', 'claude')} "
                    f"| {c['input_tokens']:,} | {c['output_tokens']:,} "
                    f"| {c['elapsed_s']}s "
                    f"| {c['cumul_input']:,} | {c['cumul_output']:,} |"
                )
            else:
                lines.append(
                    f"| {c['call']} | {c['name']} "
                    f"| {c['input_tokens']:,} | {c['output_tokens']:,} "
                    f"| {c['elapsed_s']}s "
                    f"| {c['cumul_input']:,} | {c['cumul_output']:,} |"
                )
        lines.append("")

        with open(self.md_path, "w") as f:
            f.write("\n".join(lines))

        # --- JSON ---
        data = {
            "model": self.model,
            "started": self.start_time,
            "last_updated": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "total_input_tokens": self.total_input,
            "total_output_tokens": self.total_output,
            "total_tokens": self.total_input + self.total_output,
            "total_elapsed_s": round(self.total_elapsed, 1),
            "per_provider": self.per_provider,
            "calls": self.calls,
        }
        with open(self.json_path, "w") as f:
            json.dump(data, f, indent=2)


# ---------------------------------------------------------------------------
# Agent runners
# ---------------------------------------------------------------------------

async def run_agent(
    claude_opts: dict,
    prompt: str,
    logger: PipelineLogger | None = None,
    tools: list | None = None,
    instructions: str | None = None,
    tracker: TokenTracker | None = None,
    call_name: str = "",
) -> str:
    """Run a Claude CLI call via subprocess and return the response text.

    Uses ``claude -p --output-format json`` to get structured output with
    token usage. The agent runs in the working directory specified by
    ``claude_opts["cwd"]`` and has full tool access (file read/write, bash).
    """
    cli_path = claude_opts.get("cli_path", "claude")
    model = claude_opts.get("model", "opus")
    cwd = claude_opts.get("cwd", ".")
    extra_env = claude_opts.get("env", {})

    cmd = [
        cli_path,
        "-p",
        "--output-format", "json",
        "--dangerously-skip-permissions",
        "--model", model,
    ]
    if instructions:
        cmd += ["--append-system-prompt", instructions]
    cmd.append(prompt)

    start_time = datetime.now()
    if logger:
        logger.log(f"[Claude] Starting {call_name} (model={model})")

    # Build environment with provider-specific vars (bedrock, api_key)
    env = None
    if extra_env:
        env = os.environ.copy()
        env.update(extra_env)

    def _call():
        return subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            cwd=cwd,
            env=env,
        )

    try:
        result = await asyncio.get_event_loop().run_in_executor(None, _call)
    except Exception as exc:
        elapsed = (datetime.now() - start_time).total_seconds()
        if logger:
            logger.log(f"[Claude] EXCEPTION: {type(exc).__name__}: {exc}")
        if tracker:
            tracker.record(call_name or "agent", 0, 0, elapsed)
        return ""

    elapsed = (datetime.now() - start_time).total_seconds()

    # Log stderr if present (contains error messages from CLI)
    if result.stderr and result.stderr.strip() and logger:
        logger.log(f"[Claude] stderr:\n{result.stderr.strip()}")

    # Parse JSON output
    response = ""
    input_tokens = 0
    output_tokens = 0

    try:
        data = json.loads(result.stdout)
        response = data.get("result", "")

        for _, model_stats in data.get("modelUsage", {}).items():
            input_tokens += model_stats.get("inputTokens", 0)
            output_tokens += model_stats.get("outputTokens", 0)
    except (json.JSONDecodeError, ValueError) as exc:
        if logger:
            logger.log(f"[Claude] JSON parse error: {exc}")
            if result.stdout.strip():
                logger.log(f"[Claude] Raw stdout (first 1000 chars): {result.stdout.strip()[:1000]}")
        response = result.stdout.strip()

    if result.returncode != 0 and logger:
        logger.log(f"[Claude] Non-zero exit code: {result.returncode}")

    # Log the response (truncated for readability)
    if logger and response:
        preview = response[:500] + ("..." if len(response) > 500 else "")
        for line in preview.splitlines():
            if line.strip():
                logger.log(line)

    if tracker:
        tracker.record(call_name or "agent", input_tokens, output_tokens, elapsed)

    if logger:
        logger.log(f"[Claude] Completed {call_name} in {elapsed:.0f}s "
                   f"({input_tokens} in / {output_tokens} out)")

    return response


async def run_agent_for_verdict(
    claude_opts: dict,
    prompt: str,
    logger: PipelineLogger | None = None,
    tools: list | None = None,
    tracker: TokenTracker | None = None,
    call_name: str = "",
) -> str:
    """Run agent and extract DONE/CONTINUE verdict from response."""
    text = await run_agent(claude_opts, prompt, logger, tools=tools,
                           tracker=tracker, call_name=call_name)
    for line in reversed(text.strip().splitlines()):
        stripped = line.strip().upper()
        if stripped == "DONE":
            return "DONE"
        if stripped == "CONTINUE":
            return "CONTINUE"
    for line in reversed(text.strip().splitlines()):
        stripped = line.strip().upper()
        if "DONE" in stripped:
            return "DONE"
        if "CONTINUE" in stripped:
            return "CONTINUE"
    return "CONTINUE"


async def run_auxiliary_agent(
    provider: str,
    prompt: str,
    working_dir: str,
    config: dict,
    claude_opts: dict,
    logger: PipelineLogger | None = None,
    tracker: TokenTracker | None = None,
    call_name: str = "",
    instructions: str | None = None,
) -> str:
    """Run an auxiliary agent (literature survey, selection, summary).

    Dispatches to the configured provider (claude, codex, or gemini).
    Falls back to Claude if the provider is unrecognized.
    """
    from model_runner import run_model

    provider = provider.lower().strip()
    if provider not in ("claude", "codex", "gemini"):
        if logger:
            logger.log(f"[Auxiliary] Unknown provider '{provider}', falling back to claude")
        provider = "claude"

    if logger:
        logger.log(f"[Auxiliary] Running {call_name} with provider={provider}")

    return await run_model(
        provider=provider,
        prompt=prompt,
        working_dir=working_dir,
        config=config,
        claude_opts=claude_opts,
        logger=logger,
        tracker=tracker,
        call_name=call_name,
        instructions=instructions,
    )


async def _run_multi_verification(
    *,
    verifiers: list[str],
    prompt_template: str,
    prompt_kwargs: dict,
    base_dir: str,
    prompts_dir: str,
    config: dict,
    claude_opts: dict,
    logger,
    tracker,
    call_name_prefix: str,
    round_num: int,
) -> list[str]:
    """Run verification across multiple providers in parallel.

    For each verifier in *verifiers*, loads *prompt_template*, sets the
    output file to a per-verifier filename inside *base_dir*, and dispatches
    the call via ``run_model()`` (Claude/Codex/Gemini).

    When only one verifier is requested the output filename is the backward-
    compatible ``verification_result.md``; with multiple verifiers the files
    are named ``verification_result_<provider>.md``.

    Returns a list of verification result file paths that were created.
    If some verifiers fail but at least one succeeds, returns only the
    successful results. Raises RuntimeError only if ALL verifiers fail.
    """
    import asyncio as _asyncio
    from model_runner import run_model, ModelRunnerError

    multi = len(verifiers) > 1
    result_files: list[str] = []
    failed_verifiers: list[tuple[str, str]] = []  # (verifier, error_msg)

    async def _verify_with(verifier: str) -> tuple[str, str | None, str | None]:
        """Returns (verifier, result_path, error_msg). error_msg is None on success."""
        vf_name = _verification_filename(verifier, multi)
        vf_path = os.path.join(base_dir, vf_name)

        # Build per-verifier error file name
        base_err = prompt_template.replace("proof_verify", "error_proof_verify")
        if multi:
            err_name = base_err.replace(".md", f"_{verifier}.md")
        else:
            err_name = base_err
        err_path = os.path.join(base_dir, err_name)

        kwargs = dict(prompt_kwargs)
        kwargs["output_file"] = vf_path
        kwargs["error_file"] = err_path

        verify_prompt = load_prompt(prompts_dir, prompt_template, **kwargs)
        verify_prompt += (
            f"\n\nThis is round {round_num}. "
            f"Write results to {vf_path}."
        )

        cn = f"{call_name_prefix} [{verifier}]" if multi else call_name_prefix

        try:
            response = await run_model(
                verifier, verify_prompt, kwargs.get("output_dir", base_dir), config,
                claude_opts=claude_opts, logger=logger, tracker=tracker,
                call_name=cn,
            )
            _fallback_save_response(response, [vf_path], [err_path],
                                    logger, step_name=cn)
            # Check if output file exists
            if not os.path.exists(vf_path):
                error_msg = f"Output file not created: {vf_path}"
                logger.log(f"  {verifier} verification failed: {error_msg}")
                # Write error to error file
                os.makedirs(os.path.dirname(err_path), exist_ok=True)
                with open(err_path, "a") as f:
                    f.write(f"\n\n# Verification Failed\n\n{error_msg}\n")
                return (verifier, None, error_msg)
            return (verifier, vf_path, None)
        except ModelRunnerError as e:
            # Detailed error from model runner - log full details
            error_msg = str(e)
            logger.log(f"  {verifier} verification FAILED: {e.error_type}")
            logger.log(f"    Message: {e.args[0]}")
            if e.exit_code is not None:
                logger.log(f"    Exit code: {e.exit_code}")
            if e.stderr:
                stderr_preview = e.stderr[:300] + ("..." if len(e.stderr) > 300 else "")
                logger.log(f"    Stderr: {stderr_preview}")
            # Write full error details to error file
            os.makedirs(os.path.dirname(err_path), exist_ok=True)
            with open(err_path, "w") as f:
                f.write(e.full_details())
            return (verifier, None, error_msg)
        except Exception as e:
            # Unexpected error - log and save
            error_msg = f"{type(e).__name__}: {e}"
            logger.log(f"  {verifier} verification failed (unexpected): {error_msg}")
            # Write error to error file
            os.makedirs(os.path.dirname(err_path), exist_ok=True)
            with open(err_path, "a") as f:
                f.write(f"\n\n# Verification Failed (Unexpected Error)\n\n")
                f.write(f"**Exception:** {type(e).__name__}\n")
                f.write(f"**Message:** {e}\n")
            return (verifier, None, error_msg)

    results = await _asyncio.gather(*[_verify_with(v) for v in verifiers])

    for verifier, result_path, error_msg in results:
        if result_path:
            result_files.append(result_path)
        else:
            failed_verifiers.append((verifier, error_msg or "Unknown error"))

    # Log summary of failed verifiers
    if failed_verifiers:
        failed_names = [v for v, _ in failed_verifiers]
        logger.log(f"  WARNING: {len(failed_verifiers)} verifier(s) failed: {', '.join(failed_names)}")
        for v, err in failed_verifiers:
            logger.append_history(f"{call_name_prefix} [{v}]: FAILED — {err[:100]}")

    # Only fail if ALL verifiers failed
    if not result_files:
        failed_details = "\n".join(f"  - {v}: {err}" for v, err in failed_verifiers)
        msg = f"FATAL — {call_name_prefix}: ALL verifiers failed:\n{failed_details}"
        logger.log(msg)
        raise RuntimeError(msg)

    return result_files


# ---------------------------------------------------------------------------
# Literature survey
# ---------------------------------------------------------------------------

async def run_literature_survey(
    output_dir: str,
    problem_file: str,
    claude_opts: dict,
    prompts_dir: str,
    config: dict,
    math_skill: str = "",
    tracker: TokenTracker | None = None,
) -> str:
    """Run the literature survey agent before proof search.
    Returns the path to the related_info directory.
    """
    related_info_dir = os.path.join(output_dir, "related_info")
    os.makedirs(related_info_dir, exist_ok=True)
    log_dir = os.path.join(output_dir, "literature_survey_log")

    logger = PipelineLogger(log_dir, "Literature Survey")
    logger.update_status(1, 1, "Literature Survey", "RUNNING", "Running literature survey agent...")

    # Get provider from config (default to claude)
    provider = config.get("pipeline", {}).get("literature_survey", {}).get("provider", "claude")

    survey_prompt = load_prompt(
        prompts_dir, "literature_survey.md",
        problem_file=problem_file,
        related_info_dir=related_info_dir,
        output_dir=output_dir,
        error_file=os.path.join(related_info_dir, "error_literature_survey.md"),
    )

    response = await run_auxiliary_agent(
        provider=provider,
        prompt=survey_prompt,
        working_dir=claude_opts.get("cwd", output_dir),
        config=config,
        claude_opts=claude_opts,
        logger=logger,
        tracker=tracker,
        call_name="Literature Survey",
        instructions=math_skill or None,
    )
    _fallback_save_response(response, [
        os.path.join(related_info_dir, "difficulty_evaluation.md"),
        os.path.join(related_info_dir, "problem_analysis.md"),
        os.path.join(related_info_dir, "related_theorems.md"),
    ], [os.path.join(related_info_dir, "error_literature_survey.md")],
        logger, step_name="Literature Survey")

    _check_expected_files([
        (os.path.join(related_info_dir, "difficulty_evaluation.md"), "difficulty evaluation"),
        (os.path.join(related_info_dir, "problem_analysis.md"), "problem analysis"),
        (os.path.join(related_info_dir, "related_theorems.md"), "related theorems"),
        (os.path.join(related_info_dir, "error_literature_survey.md"), "error log"),
    ], logger, "Literature Survey")

    logger.finalize(1, 1, "FINISHED", "Literature survey complete.")
    return related_info_dir


# ---------------------------------------------------------------------------
# Proof search loop
# ---------------------------------------------------------------------------

async def _run_parallel_round(
    i: int,
    max_iterations: int,
    output_dir: str,
    problem_file: str,
    claude_opts: dict,
    prompts_dir: str,
    related_info_dir: str,
    proving_skill: str,
    tracker: TokenTracker | None,
    logger: "PipelineLogger",
    verify_dir: str,
    proof_file: str,
    config: dict,
    available_providers: list[str],
    human_help_dir: str,
    prev_round_human_help_dir: str = "",
    resume_from_step: str = "proof_search",
    verification_providers: list[str] | None = None,
) -> str:
    """Execute one parallel round for hard-mode. Returns 'DONE' or 'CONTINUE'.

    Steps: parallel proof search → parallel verification → selector → verdict.
    """
    import asyncio as _asyncio
    from model_runner import run_model

    if verification_providers is None:
        verification_providers = ["claude"]
    multi_verifier = len(verification_providers) > 1

    round_dir = os.path.join(verify_dir, f"round_{i}")
    providers = available_providers  # e.g. ["claude", "codex", "gemini"]

    # Create per-model subdirectories
    model_dirs = {}
    for m in providers:
        mdir = os.path.join(round_dir, m)
        os.makedirs(mdir, exist_ok=True)
        model_dirs[m] = mdir

    # Build previous-round instructions (shared by all models)
    prev_round_dir = os.path.join(verify_dir, f"round_{i-1}")
    prev_instructions = ""
    # For parallel rounds, previous verification is in the selected model's subdir,
    # or at the round level as selection.md
    if i > 1:
        # Try to find the selected model's verification from previous round
        prev_selection = os.path.join(prev_round_dir, "selection.md")
        if os.path.exists(prev_selection):
            prev_instructions += f"- Read the PREVIOUS round's proof selection report from {prev_selection}.\n"
        # Also point to each model's verification from previous round
        for m in providers:
            mdir = os.path.join(prev_round_dir, m)
            # Check new split layout first, then legacy
            prev_v_files = (
                _find_verification_files(os.path.join(mdir, "verification_file", "detailed"))
                or _find_verification_files(os.path.join(mdir, "verification_file", "structural"))
                or _find_verification_files(mdir)
            )
            for prev_v in prev_v_files:
                prev_instructions += f"- Previous round's {m} verification result: {prev_v}\n"
            prev_s = os.path.join(mdir, "proof_status.md")
            if os.path.exists(prev_s):
                prev_instructions += f"- Previous round's {m} proof status: {prev_s}\n"
    if not prev_instructions:
        prev_instructions = "- This is the first round. No previous round data available.\n"

    skip_proof_search = resume_from_step in (
        "parallel_verification", "parallel_detailed_verification",
        "parallel_selection",
    )
    skip_structural = resume_from_step in (
        "parallel_detailed_verification", "parallel_selection",
    )
    skip_detailed = resume_from_step == "parallel_selection"

    single_provider = len(providers) == 1
    # Steps: search, structural, detailed, [select], copy, verdict
    # When only one provider, skip selector agent
    if single_provider:
        total_steps = 6
    else:
        total_steps = 7
    step = 0

    # ==================================================================
    # Step 1: PARALLEL PROOF SEARCH
    # ==================================================================
    step += 1
    if skip_proof_search:
        logger.log(f"--- Resuming round {i}: skipping proof search (already complete) ---")
        logger.append_history(f"Iteration {i}: Parallel proof search SKIPPED (resume)")
    else:
        # Back up proof.md before the round
        proof_backup = os.path.join(round_dir, "proof_before_round.md")
        if os.path.exists(proof_file):
            shutil.copy2(proof_file, proof_backup)

        logger.update_status(i, max_iterations, f"{step}/{total_steps} Parallel Proof Search", "RUNNING",
                             f"Running proof search on {len(providers)} models in parallel...")
        logger.append_history(f"Iteration {i}: Parallel proof search started ({', '.join(providers)})")

        async def _proof_search(provider):
            mdir = model_dirs[provider]
            m_proof = os.path.join(mdir, "proof.md")
            m_status = os.path.join(mdir, "proof_status.md")
            # Each model writes to its own proof.md and proof_status.md
            search_prompt = load_prompt(
                prompts_dir, "proof_search.md",
                problem_file=problem_file,
                proof_file=m_proof,
                output_dir=output_dir,
                related_info_dir=related_info_dir,
                round_num=i,
                proof_status_file=m_status,
                previous_round_instructions=prev_instructions,
                human_help_dir=human_help_dir,
                prev_round_human_help_dir=prev_round_human_help_dir,
                skill_file=os.path.join(os.path.dirname(prompts_dir), "skill", "super_math_skill.md"),
                scratch_pad_file=os.path.join(mdir, "scratch_pad.md"),
                error_file=os.path.join(mdir, "error_proof_search.md"),
            )
            search_prompt += (
                f"\n\nThis is round {i}. Write or refine the proof. "
                f"If one approach doesn't work after much effort, try a completely different proof strategy."
            )
            # Copy the current best proof into the model's working dir as starting point
            if os.path.exists(proof_file) and not os.path.exists(m_proof):
                shutil.copy2(proof_file, m_proof)

            response = await run_model(
                provider, search_prompt, output_dir, config,
                claude_opts=claude_opts, logger=logger, tracker=tracker,
                call_name=f"Proof Search R{i} [{provider}]",
                instructions=proving_skill or None,
            )
            _fallback_save_response(response,
                [os.path.join(mdir, "proof.md"), os.path.join(mdir, "proof_status.md")],
                [os.path.join(mdir, "error_proof_search.md")],
                logger, step_name=f"Proof Search R{i} [{provider}]")

        await _asyncio.gather(*[_proof_search(p) for p in providers])
        for p in providers:
            mdir = model_dirs[p]
            _check_expected_files([
                (os.path.join(mdir, "proof.md"), f"{p} proof"),
                (os.path.join(mdir, "proof_status.md"), f"{p} proof status"),
                (os.path.join(mdir, "error_proof_search.md"), f"{p} error log"),
            ], logger, f"Parallel Proof Search R{i} [{p}]")
        logger.append_history(f"Iteration {i}: Parallel proof search completed")

    # ==================================================================
    # Step 2: PARALLEL STRUCTURAL VERIFICATION
    # ==================================================================
    step += 1
    # Create per-model structural/detailed subdirectories
    for m in providers:
        mdir = model_dirs[m]
        os.makedirs(os.path.join(mdir, "verification_file", "structural"), exist_ok=True)
        os.makedirs(os.path.join(mdir, "verification_file", "detailed"), exist_ok=True)

    if skip_structural:
        logger.log(f"--- Resuming round {i}: skipping structural verification (already complete) ---")
        logger.append_history(f"Iteration {i}: Parallel structural verification SKIPPED (resume)")
    else:
        verifier_names = ", ".join(verification_providers)
        logger.update_status(i, max_iterations, f"{step}/{total_steps} Parallel Structural Verification", "RUNNING",
                             f"Structural verification: {len(providers)} proofs × {len(verification_providers)} verifiers...")
        logger.append_history(f"Iteration {i}: Parallel structural verification started (verifiers: {verifier_names})")

        async def _structural_verify_for_model(proof_provider):
            mdir = model_dirs[proof_provider]
            m_proof = os.path.join(mdir, "proof.md")
            s_dir = os.path.join(mdir, "verification_file", "structural")

            await _run_multi_verification(
                verifiers=verification_providers,
                prompt_template="proof_verify_structural.md",
                prompt_kwargs=dict(
                    problem_file=problem_file,
                    proof_file=m_proof,
                    output_dir=output_dir,
                ),
                base_dir=s_dir,
                prompts_dir=prompts_dir,
                config=config,
                claude_opts=claude_opts,
                logger=logger,
                tracker=tracker,
                call_name_prefix=f"Structural Verification R{i} [{proof_provider}]",
                round_num=i,
            )

        await _asyncio.gather(*[_structural_verify_for_model(p) for p in providers])
        logger.append_history(f"Iteration {i}: Parallel structural verification completed")

    # ==================================================================
    # Step 3: STRUCTURAL VERDICT (round-level)
    # ==================================================================
    step += 1
    if skip_structural:
        logger.log(f"--- Resuming round {i}: skipping structural verdict (already complete) ---")
    else:
        logger.update_status(i, max_iterations, f"{step}/{total_steps} Structural Verdict", "RUNNING",
                             "Checking structural verification results...")
        logger.append_history(f"Iteration {i}: Structural verdict check started")

        # Collect ALL structural verification files across all models
        all_structural_files = []
        for m in providers:
            s_dir = os.path.join(model_dirs[m], "verification_file", "structural")
            all_structural_files.extend(_find_verification_files(s_dir))

        if not all_structural_files:
            # No structural files found — round fails
            logger.log(f"Iteration {i}: No structural verification files found — round CONTINUE")
            logger.append_history(f"Iteration {i}: Decision = CONTINUE (no structural files)")
            return "CONTINUE"

        # Build verdict prompt from all structural files
        if len(all_structural_files) == 1:
            structural_verdict_prompt = load_prompt(
                prompts_dir, "verdict_proof.md",
                verification_result_file=f"Read the verification result file at `{all_structural_files[0]}`.",
            )
        else:
            files_list = "\n".join(f"- `{f}`" for f in all_structural_files)
            structural_verdict_prompt = load_prompt(
                prompts_dir, "verdict_proof.md",
                verification_result_file=files_list,
            )
        structural_decision = await run_agent_for_verdict(
            claude_opts, structural_verdict_prompt, logger,
            tracker=tracker, call_name=f"Structural Verdict R{i}",
        )
        logger.log(f"Iteration {i}: Structural verdict = {structural_decision}")
        logger.append_history(f"Iteration {i}: Structural verdict = {structural_decision}")

        if structural_decision == "CONTINUE":
            # Structural failed — skip detailed, round fails
            logger.log(f"Iteration {i}: Structural checks FAILED — skipping detailed verification")
            logger.append_history(f"Iteration {i}: Decision = CONTINUE (structural failed)")
            return "CONTINUE"

    # ==================================================================
    # Step 4: PARALLEL DETAILED VERIFICATION
    # ==================================================================
    step += 1
    if skip_detailed:
        logger.log(f"--- Resuming round {i}: skipping detailed verification (already complete) ---")
        logger.append_history(f"Iteration {i}: Parallel detailed verification SKIPPED (resume)")
    else:
        verifier_names = ", ".join(verification_providers)
        logger.update_status(i, max_iterations, f"{step}/{total_steps} Parallel Detailed Verification", "RUNNING",
                             f"Detailed verification: {len(providers)} proofs × {len(verification_providers)} verifiers...")
        logger.append_history(f"Iteration {i}: Parallel detailed verification started (verifiers: {verifier_names})")

        async def _detailed_verify_for_model(proof_provider):
            mdir = model_dirs[proof_provider]
            m_proof = os.path.join(mdir, "proof.md")
            s_dir = os.path.join(mdir, "verification_file", "structural")
            d_dir = os.path.join(mdir, "verification_file", "detailed")

            # Find the structural report for this model to pass as input
            s_files = _find_verification_files(s_dir)
            structural_report = s_files[0] if s_files else os.path.join(s_dir, "verification_result.md")

            await _run_multi_verification(
                verifiers=verification_providers,
                prompt_template="proof_verify_detailed.md",
                prompt_kwargs=dict(
                    problem_file=problem_file,
                    proof_file=m_proof,
                    structural_report_file=structural_report,
                    output_dir=output_dir,
                ),
                base_dir=d_dir,
                prompts_dir=prompts_dir,
                config=config,
                claude_opts=claude_opts,
                logger=logger,
                tracker=tracker,
                call_name_prefix=f"Detailed Verification R{i} [{proof_provider}]",
                round_num=i,
            )

        await _asyncio.gather(*[_detailed_verify_for_model(p) for p in providers])
        logger.append_history(f"Iteration {i}: Parallel detailed verification completed")

    # ==================================================================
    # Step 5: SELECTOR AGENT — only when multiple providers
    # ==================================================================
    selection_file = os.path.join(round_dir, "selection.md")

    if single_provider:
        # Single provider: skip selector agent entirely, use the only provider
        selected_model = providers[0]
        logger.log(f"--- Round {i}: skipping proof selection (single provider: {selected_model}) ---")
        logger.append_history(f"Iteration {i}: Proof selection SKIPPED (single provider: {selected_model})")
    else:
        step += 1
        logger.update_status(i, max_iterations, f"{step}/{total_steps} Proof Selection", "RUNNING",
                             "Selecting best proof from verification reports...")
        logger.append_history(f"Iteration {i}: Proof selection started")

        # Build dynamic verification reports block for selector prompt
        # Point to detailed verification files (which contain the final verdicts)
        block_lines = []
        proof_paths = {}
        for m in ("claude", "codex", "gemini"):
            mdir = os.path.join(round_dir, m)
            pf = os.path.join(mdir, "proof.md")
            proof_paths[m] = pf if os.path.exists(pf) else "(not available — model not used)"
            if not os.path.isdir(mdir):
                block_lines.append(f"- **{m.title()}'s proof verification:** (not available — model not used)")
                continue
            # Check detailed first, then structural, then legacy location
            d_dir = os.path.join(mdir, "verification_file", "detailed")
            s_dir = os.path.join(mdir, "verification_file", "structural")
            vfiles = (
                _find_verification_files(d_dir)
                or _find_verification_files(s_dir)
                or _find_verification_files(mdir)
            )
            if vfiles:
                block_lines.append(f"**{m.title()}'s proof verification(s):**")
                for vf in vfiles:
                    block_lines.append(f"- `{vf}`")
            else:
                block_lines.append(f"- **{m.title()}'s proof verification:** (not available)")
        verification_reports_block = "\n".join(block_lines)

        # Get selector provider from config (default to claude)
        select_provider = config.get("pipeline", {}).get("proof_select", {}).get("provider", "claude")

        select_prompt = load_prompt(
            prompts_dir, "proof_select.md",
            problem_file=problem_file,
            verification_reports_block=verification_reports_block,
            proof_claude=proof_paths["claude"],
            proof_codex=proof_paths["codex"],
            proof_gemini=proof_paths["gemini"],
            selection_file=selection_file,
            error_file=os.path.join(round_dir, "error_proof_select.md"),
        )
        response = await run_auxiliary_agent(
            provider=select_provider,
            prompt=select_prompt,
            working_dir=claude_opts.get("cwd", output_dir),
            config=config,
            claude_opts=claude_opts,
            logger=logger,
            tracker=tracker,
            call_name=f"Proof Selection R{i}",
        )
        _fallback_save_response(response, [selection_file],
            [os.path.join(round_dir, "error_proof_select.md")],
            logger, step_name=f"Proof Selection R{i}")
        _check_expected_files([
            (selection_file, "selection report"),
            (os.path.join(round_dir, "error_proof_select.md"), "error log"),
        ], logger, f"Proof Selection R{i}")
        logger.append_history(f"Iteration {i}: Proof selection completed")
        selected_model = _parse_selected_model(selection_file, providers)

    # ==================================================================
    # Step 6: Copy selected proof to main proof.md
    # ==================================================================
    step += 1
    logger.update_status(i, max_iterations, f"{step}/{total_steps} Applying Selection", "RUNNING",
                         "Copying selected proof to main proof.md...")
    logger.log(f"Iteration {i}: Selected model = {selected_model}")
    logger.append_history(f"Iteration {i}: Selected model = {selected_model}")

    selected_proof = os.path.join(round_dir, selected_model, "proof.md")
    if os.path.exists(selected_proof):
        shutil.copy2(selected_proof, proof_file)

    # ==================================================================
    # Step 7: VERDICT AGENT (Claude) — uses selected model's detailed verification(s)
    # ==================================================================
    step += 1
    selected_mdir = os.path.join(round_dir, selected_model)
    # Check detailed dir first, then structural, then legacy location
    verification_files = (
        _find_verification_files(os.path.join(selected_mdir, "verification_file", "detailed"))
        or _find_verification_files(os.path.join(selected_mdir, "verification_file", "structural"))
        or _find_verification_files(selected_mdir)
    )
    if not verification_files:
        # Fallback — should not happen if verification ran successfully
        verification_files = [os.path.join(selected_mdir, "verification_file", "detailed", "verification_result.md")]
    logger.update_status(i, max_iterations, f"{step}/{total_steps} Checking Verdict", "RUNNING",
                         "Analyzing selected verification results...")
    logger.append_history(f"Iteration {i}: Checking verdict (using {selected_model}'s verification, {len(verification_files)} report(s))")

    if len(verification_files) == 1:
        verdict_prompt = load_prompt(
            prompts_dir, "verdict_proof.md",
            verification_result_file=f"Read the verification result file at `{verification_files[0]}`.",
        )
    else:
        files_list = "\n".join(f"- `{f}`" for f in verification_files)
        verdict_prompt = load_prompt(
            prompts_dir, "verdict_proof.md",
            verification_result_file=files_list,
        )
    decision = await run_agent_for_verdict(claude_opts, verdict_prompt, logger,
                                           tracker=tracker, call_name=f"Verdict R{i}")
    logger.log(f"Iteration {i}: Decision is {decision}")
    logger.append_history(f"Iteration {i}: Decision = {decision}")
    return decision


def _parse_selected_model(selection_file: str, available: list[str]) -> str:
    """Parse 'SELECTED: <model>' from selection.md. Falls back to first available."""
    if not os.path.exists(selection_file):
        return available[0]
    with open(selection_file) as f:
        for line in f:
            upper = line.upper()
            if "SELECTED:" in upper:
                for m in ("claude", "codex", "gemini"):
                    if m in line.lower():
                        return m
    return available[0]


async def run_proof_loop(
    output_dir: str,
    problem_file: str,
    claude_opts: dict,
    prompts_dir: str,
    max_iterations: int,
    related_info_dir: str,
    proving_skill: str = "",
    tracker: TokenTracker | None = None,
    start_round: int = 1,
    resume_from_step: str = "proof_search",
    difficulty: str = "unknown",
    multi_model_config: dict | None = None,
    verification_providers: list[str] | None = None,
    config: dict | None = None,
) -> bool:
    """Run the proof search/verification/verdict loop.

    Args:
        start_round: Round number to begin from (for resume support).
            Rounds before this are assumed already complete on disk.
        resume_from_step: Which step to resume from within start_round:
            "proof_search"                    — start the round from scratch
            "verification"                    — skip proof search, start from structural verification
            "detailed_verification"           — structural done, start from detailed verification
            "parallel_verification"           — parallel round, skip proof search
            "parallel_detailed_verification"  — parallel round, structural done
            "parallel_selection"              — parallel round, skip to selector
        difficulty: Problem difficulty from the literature survey
            ("easy", "medium", "hard", or "unknown"). Easy problems use
            a lightweight verification prompt.
        multi_model_config: Dict with keys "providers" (list[str]), "config" (full
            config dict). When set, enables parallel multi-model proof search.

    Returns True if successful (DONE), False if max iterations reached.
    """
    if verification_providers is None:
        verification_providers = ["claude"]
    multi_verifier = len(verification_providers) > 1

    easy_mode = (difficulty == "easy")
    # Multi-model parallel proof search: enabled when multi_model_config is set
    # (determined solely by config, not by difficulty)
    parallel_proof_search = multi_model_config is not None
    proof_file = os.path.join(output_dir, "proof.md")
    verify_dir = os.path.join(output_dir, "verification")

    # Resolve human_help_dir
    script_dir = os.path.dirname(os.path.abspath(__file__))
    project_base = os.path.dirname(script_dir)
    human_help_dir = os.path.join(project_base, "human_help")

    logger = PipelineLogger(verify_dir, "Proof Search")

    # Create initial empty proof file
    if not os.path.exists(proof_file):
        with open(proof_file, "w") as f:
            f.write("<!-- Proof will be written here by the proof search agent -->\n")

    # --- Resume check: parse the last complete round's verdict from disk ---
    if start_round > 1 and resume_from_step == "proof_search":
        prev_complete = start_round - 1
        prev_round_dir = os.path.join(verify_dir, f"round_{prev_complete}")
        # Find verification files — check new structure first, then old
        prev_verify_files = (
            _find_verification_files(os.path.join(prev_round_dir, "verification_file", "detailed"))
            or _find_verification_files(prev_round_dir)
        )
        if not prev_verify_files and _is_parallel_round(prev_round_dir):
            # For parallel rounds, check selected model's verification(s)
            selected = _parse_selected_model(
                os.path.join(prev_round_dir, "selection.md"),
                multi_model_config.get("providers", ["claude"]) if multi_model_config else ["claude"],
            )
            sel_mdir = os.path.join(prev_round_dir, selected)
            prev_verify_files = (
                _find_verification_files(os.path.join(sel_mdir, "verification_file", "detailed"))
                or _find_verification_files(sel_mdir)
            )
        if prev_verify_files:
            # All verification files must be PASS for the round to count as done
            verdicts = [_parse_verdict_from_file(f) for f in prev_verify_files]
            all_pass = all(v == "PASS" for v in verdicts)
            verdict_str = "PASS" if all_pass else "FAIL"
            logger.log(f"\n--- Resuming: round {prev_complete} verdict from file(s) = {verdict_str} ({len(prev_verify_files)} report(s)) ---")
            logger.append_history(f"Resume: parsed verdict for round {prev_complete} = {verdict_str}")
            if all_pass:
                logger.finalize(prev_complete, max_iterations, "FINISHED",
                                "Proof already verified in previous run!")
                logger.append_history("SUCCESS - Proof already verified (resume check)")
                return True

    for i in range(start_round, max_iterations + 1):
        round_dir = os.path.join(verify_dir, f"round_{i}")
        os.makedirs(round_dir, exist_ok=True)

        # Create per-round human_help directory with empty file
        round_hh_dir = os.path.join(round_dir, "human_help")
        os.makedirs(round_hh_dir, exist_ok=True)
        round_hh_file = os.path.join(round_hh_dir, "human_help.md")
        if not os.path.exists(round_hh_file):
            with open(round_hh_file, "w") as f:
                f.write("")

        # Previous round's human_help (read by this round's proof search)
        prev_round_hh_dir = os.path.join(verify_dir, f"round_{i-1}", "human_help")
        if i == 1 or not os.path.isdir(prev_round_hh_dir):
            prev_round_hh_dir = ""

        logger.log(f"\n========================================")
        logger.log(f"=== ITERATION {i} of {max_iterations} ===")
        logger.log(f"========================================")
        logger.append_history(f"Iteration {i} started (round dir: round_{i})")

        if parallel_proof_search:
            # ==============================================================
            # HARD MODE: parallel proof search across multiple models
            # ==============================================================
            # Determine resume step for this specific round
            round_resume = "proof_search"
            if i == start_round and resume_from_step.startswith("parallel_"):
                round_resume = resume_from_step

            decision = await _run_parallel_round(
                i=i,
                max_iterations=max_iterations,
                output_dir=output_dir,
                problem_file=problem_file,
                claude_opts=claude_opts,
                prompts_dir=prompts_dir,
                related_info_dir=related_info_dir,
                proving_skill=proving_skill,
                tracker=tracker,
                logger=logger,
                verify_dir=verify_dir,
                proof_file=proof_file,
                config=multi_model_config["config"],
                available_providers=multi_model_config["providers"],
                human_help_dir=human_help_dir,
                prev_round_human_help_dir=prev_round_hh_dir,
                resume_from_step=round_resume,
                verification_providers=verification_providers,
            )

            if decision == "DONE":
                logger.finalize(i, max_iterations, "FINISHED", "Proof verified successfully!")
                logger.append_history("SUCCESS - Proof verified")
                return True

        else:
            # ==============================================================
            # SINGLE-MODEL MODE (easy / medium / unknown)
            # ==============================================================
            proof_status_file = os.path.join(round_dir, "proof_status.md")

            # Find previous round's verification files (check new structure first, then old)
            prev_round_dir = os.path.join(verify_dir, f"round_{i-1}")
            prev_verify_files_list = (
                _find_verification_files(os.path.join(prev_round_dir, "verification_file", "detailed"))
                or _find_verification_files(os.path.join(prev_round_dir, "verification_file", "structural"))
                or _find_verification_files(prev_round_dir)  # backward compat with old layout
            )
            prev_proof_status = os.path.join(prev_round_dir, "proof_status.md")

            # Determine which steps to skip for this round (resume case)
            skip_proof_search = (i == start_round and resume_from_step in ("verification", "detailed_verification"))

            # Step labels depend on mode: easy=3 (search, verify, verdict),
            # non-easy=4 (search, structural, detailed, verdict)
            total_steps = 3 if easy_mode else 4

            # ------------------------------------------------------------------
            # Step 1: Proof Search
            # ------------------------------------------------------------------
            if skip_proof_search:
                logger.log(f"--- Resuming round {i}: skipping proof search (already complete) ---")
                logger.append_history(f"Iteration {i}: Proof search SKIPPED (resume)")
            else:
                # Build previous-round instructions
                prev_instructions = ""
                for pvf in prev_verify_files_list:
                    prev_instructions += f"- Read the PREVIOUS round's verification result from {pvf}.\n"
                if os.path.exists(prev_proof_status):
                    prev_instructions += f"- Read the PREVIOUS round's proof status from {prev_proof_status}. It contains which approaches were tried and FAILED — do NOT repeat these.\n"
                if not prev_instructions:
                    prev_instructions = "- This is the first round. No previous round data available.\n"

                # Back up proof.md before the proof search agent modifies it
                proof_backup = os.path.join(round_dir, "proof_before_round.md")
                if os.path.exists(proof_file):
                    shutil.copy2(proof_file, proof_backup)

                logger.update_status(i, max_iterations, f"1/{total_steps} Proof Search", "RUNNING", "Running proof search agent...")
                logger.append_history(f"Iteration {i}: Proof search started")

                search_prompt = load_prompt(
                    prompts_dir, "proof_search.md",
                    problem_file=problem_file,
                    proof_file=proof_file,
                    output_dir=output_dir,
                    related_info_dir=related_info_dir,
                    round_num=i,
                    proof_status_file=proof_status_file,
                    previous_round_instructions=prev_instructions,
                    human_help_dir=human_help_dir,
                    prev_round_human_help_dir=prev_round_hh_dir,
                    skill_file=os.path.join(os.path.dirname(prompts_dir), "skill", "super_math_skill.md"),
                    scratch_pad_file=os.path.join(round_dir, "scratch_pad.md"),
                    error_file=os.path.join(round_dir, "error_proof_search.md"),
                )
                search_prompt += f"\n\nThis is round {i}. Write or refine the proof. If one approach doesn't work after much effort, try a completely different proof strategy."

                response = await run_agent(claude_opts, search_prompt, logger, instructions=proving_skill or None,
                                           tracker=tracker, call_name=f"Proof Search R{i}")
                _fallback_save_response(response, [proof_file, proof_status_file],
                    [os.path.join(round_dir, "error_proof_search.md")],
                    logger, step_name=f"Proof Search R{i}")
                _check_expected_files([
                    (proof_file, "proof"),
                    (proof_status_file, "proof status"),
                    (os.path.join(round_dir, "error_proof_search.md"), "error log"),
                ], logger, f"Proof Search R{i}")
                logger.append_history(f"Iteration {i}: Proof search completed")

            if easy_mode:
                # ==============================================================
                # EASY MODE: use lightweight verification (unchanged)
                # ==============================================================

                # Step 2/3: Easy Verification
                verifier_label = f" ({', '.join(verification_providers)})" if multi_verifier else ""
                logger.update_status(i, max_iterations, f"2/3 Verification (easy){verifier_label}", "RUNNING", "Running easy verification agent...")
                logger.append_history(f"Iteration {i}: Easy verification started")

                verification_files = await _run_multi_verification(
                    verifiers=verification_providers,
                    prompt_template="proof_verify_easy.md",
                    prompt_kwargs=dict(
                        problem_file=problem_file,
                        proof_file=proof_file,
                        output_dir=output_dir,
                    ),
                    base_dir=round_dir,
                    prompts_dir=prompts_dir,
                    config=config or {},
                    claude_opts=claude_opts,
                    logger=logger,
                    tracker=tracker,
                    call_name_prefix=f"Verification (easy) R{i}",
                    round_num=i,
                )
                logger.append_history(f"Iteration {i}: Easy verification completed")

                # Step 3/3: Verdict
                logger.update_status(i, max_iterations, "3/3 Checking Verdict", "RUNNING", "Analyzing verification results...")
                logger.append_history(f"Iteration {i}: Checking verdict")

                # Build verdict prompt — supports single or multiple verification files
                if len(verification_files) == 1:
                    verdict_prompt = load_prompt(
                        prompts_dir, "verdict_proof.md",
                        verification_result_file=f"Read the verification result file at `{verification_files[0]}`.",
                    )
                else:
                    files_list = "\n".join(f"- `{f}`" for f in verification_files)
                    verdict_prompt = load_prompt(
                        prompts_dir, "verdict_proof.md",
                        verification_result_file=files_list,
                    )
                decision = await run_agent_for_verdict(claude_opts, verdict_prompt, logger,
                                                       tracker=tracker, call_name=f"Verdict R{i}")

            else:
                # ==============================================================
                # NON-EASY MODE: structural → verdict → detailed → verdict
                # ==============================================================

                # Create subdirectories for structural and detailed verification
                structural_dir = os.path.join(round_dir, "verification_file", "structural")
                detailed_dir = os.path.join(round_dir, "verification_file", "detailed")
                os.makedirs(structural_dir, exist_ok=True)
                os.makedirs(detailed_dir, exist_ok=True)

                # Determine which sub-steps to skip (resume case)
                skip_structural = (i == start_round and resume_from_step == "detailed_verification")

                # total_steps already set to 4 above (search, structural_verify, detailed_verify, verdict)

                if skip_structural:
                    logger.log(f"--- Resuming round {i}: skipping structural verification (already complete) ---")
                    logger.append_history(f"Iteration {i}: Structural verification SKIPPED (resume)")
                else:
                    # Step 2/4: Structural Verification (Phases 1–3)
                    verifier_label = f" ({', '.join(verification_providers)})" if multi_verifier else ""
                    logger.update_status(i, max_iterations, f"2/{total_steps} Structural Verification{verifier_label}", "RUNNING", "Running structural verification agent...")
                    logger.append_history(f"Iteration {i}: Structural verification started")

                    structural_files = await _run_multi_verification(
                        verifiers=verification_providers,
                        prompt_template="proof_verify_structural.md",
                        prompt_kwargs=dict(
                            problem_file=problem_file,
                            proof_file=proof_file,
                            output_dir=output_dir,
                        ),
                        base_dir=structural_dir,
                        prompts_dir=prompts_dir,
                        config=config or {},
                        claude_opts=claude_opts,
                        logger=logger,
                        tracker=tracker,
                        call_name_prefix=f"Structural Verification R{i}",
                        round_num=i,
                    )
                    logger.append_history(f"Iteration {i}: Structural verification completed")

                    # Structural verdict: check if structural checks passed
                    if len(structural_files) == 1:
                        structural_verdict_prompt = load_prompt(
                            prompts_dir, "verdict_proof.md",
                            verification_result_file=f"Read the verification result file at `{structural_files[0]}`.",
                        )
                    else:
                        files_list = "\n".join(f"- `{f}`" for f in structural_files)
                        structural_verdict_prompt = load_prompt(
                            prompts_dir, "verdict_proof.md",
                            verification_result_file=files_list,
                        )
                    structural_decision = await run_agent_for_verdict(
                        claude_opts, structural_verdict_prompt, logger,
                        tracker=tracker, call_name=f"Structural Verdict R{i}",
                    )
                    logger.log(f"Iteration {i}: Structural verdict = {structural_decision}")
                    logger.append_history(f"Iteration {i}: Structural verdict = {structural_decision}")

                    if structural_decision == "CONTINUE":
                        # Structural failed — skip detailed, round fails
                        logger.log(f"Iteration {i}: Structural checks FAILED — skipping detailed verification")
                        logger.append_history(f"Iteration {i}: Decision = CONTINUE (structural failed)")
                        await asyncio.sleep(2)
                        continue

                # Step 3/4: Detailed Verification (Phase 4)
                # Find structural report file(s) to pass to detailed verification
                structural_files = _find_verification_files(structural_dir)
                if not structural_files:
                    structural_files = [os.path.join(structural_dir, "verification_result.md")]

                # Build structural_report_file reference for the detailed prompt
                if len(structural_files) == 1:
                    structural_report_ref = structural_files[0]
                else:
                    # Multiple structural reports (multi-verifier) — use the first as primary
                    structural_report_ref = structural_files[0]

                verifier_label = f" ({', '.join(verification_providers)})" if multi_verifier else ""
                logger.update_status(i, max_iterations, f"3/{total_steps} Detailed Verification{verifier_label}", "RUNNING", "Running detailed verification agent...")
                logger.append_history(f"Iteration {i}: Detailed verification started")

                verification_files = await _run_multi_verification(
                    verifiers=verification_providers,
                    prompt_template="proof_verify_detailed.md",
                    prompt_kwargs=dict(
                        problem_file=problem_file,
                        proof_file=proof_file,
                        structural_report_file=structural_report_ref,
                        output_dir=output_dir,
                    ),
                    base_dir=detailed_dir,
                    prompts_dir=prompts_dir,
                    config=config or {},
                    claude_opts=claude_opts,
                    logger=logger,
                    tracker=tracker,
                    call_name_prefix=f"Detailed Verification R{i}",
                    round_num=i,
                )
                logger.append_history(f"Iteration {i}: Detailed verification completed")

                # Step 4/4: Final Verdict
                logger.update_status(i, max_iterations, f"4/{total_steps} Checking Verdict", "RUNNING", "Analyzing detailed verification results...")
                logger.append_history(f"Iteration {i}: Checking final verdict")

                # Build verdict prompt from detailed verification files
                if len(verification_files) == 1:
                    verdict_prompt = load_prompt(
                        prompts_dir, "verdict_proof.md",
                        verification_result_file=f"Read the verification result file at `{verification_files[0]}`.",
                    )
                else:
                    files_list = "\n".join(f"- `{f}`" for f in verification_files)
                    verdict_prompt = load_prompt(
                        prompts_dir, "verdict_proof.md",
                        verification_result_file=files_list,
                    )
                decision = await run_agent_for_verdict(claude_opts, verdict_prompt, logger,
                                                       tracker=tracker, call_name=f"Verdict R{i}")

            logger.log(f"Iteration {i}: Decision is {decision}")
            logger.append_history(f"Iteration {i}: Decision = {decision}")

            if decision == "DONE":
                logger.finalize(i, max_iterations, "FINISHED", "Proof verified successfully!")
                logger.append_history("SUCCESS - Proof verified")
                return True

        await asyncio.sleep(2)

    logger.finalize(max_iterations, max_iterations, "STOPPED", "Max iterations reached.")
    logger.append_history("STOPPED - Max iterations reached")
    return False


# ---------------------------------------------------------------------------
# Main pipeline
# ---------------------------------------------------------------------------

async def main():
    parser = argparse.ArgumentParser(description="Proof Agent: natural-language proof search pipeline")
    parser.add_argument("--input", required=True, help="Path to problem.tex file")
    parser.add_argument("--output", required=True, help="Output directory for proof and logs")
    parser.add_argument("--config", required=True, help="Path to config.yaml")
    args = parser.parse_args()

    check_prerequisites()

    with open(args.config) as f:
        config = yaml.safe_load(f)

    pipeline_cfg = config.get("pipeline", {})
    claude_cfg = config.get("claude", {})
    max_proof = pipeline_cfg.get("max_proof_iterations", 9)

    problem_file = os.path.abspath(args.input)
    output_dir = os.path.abspath(args.output)

    if not os.path.exists(problem_file):
        print(f"ERROR: Input file not found: {problem_file}")
        sys.exit(1)

    # Resolve paths relative to project root (one level up from code/)
    script_dir = os.path.dirname(os.path.abspath(__file__))
    project_base = os.path.dirname(script_dir)  # proof_agent/
    prompts_dir = os.path.join(project_base, "prompts")
    skill_dir = os.path.join(project_base, "skill")

    # Load math skill (used as system prompt for proof search agent)
    math_skill_path = os.path.join(skill_dir, "super_math_skill.md")
    proving_skill = ""
    if os.path.exists(math_skill_path):
        with open(math_skill_path) as f:
            proving_skill = f.read()

    # Create output directory
    os.makedirs(output_dir, exist_ok=True)

    # Copy problem file into output for reference
    problem_copy = os.path.join(output_dir, "problem.tex")
    if not os.path.exists(problem_copy):
        shutil.copy2(problem_file, problem_copy)

    # Build Claude CLI options
    claude_opts = make_claude_options(claude_cfg, output_dir)

    # Token usage tracker — writes TOKEN_USAGE.md after every agent call
    tracker = TokenTracker(output_dir, claude_opts["model"])

    # -------------------------------------------------------
    # Determine providers BEFORE resume detection (needed for completeness checks)
    # -------------------------------------------------------
    # Multi-model proof search providers
    mm_cfg = pipeline_cfg.get("multi_model", {})
    proof_providers = ["claude"]  # default single-model
    if mm_cfg.get("enabled", False):
        proof_providers = check_multi_model_providers(config)

    # Verification providers
    verification_providers = check_verification_providers(config)

    # -------------------------------------------------------
    # Detect resume state
    # -------------------------------------------------------
    skip_survey, start_round, resume_from_step = detect_resume_state(
        output_dir,
        verification_providers=verification_providers,
        proof_providers=proof_providers,
    )

    print("=" * 60)
    print("  Proof Agent Pipeline")
    print("=" * 60)
    print(f"  Problem:    {problem_file}")
    print(f"  Output:     {output_dir}")
    print(f"  Max rounds: {max_proof}")
    print(f"  Token log:  {tracker.md_path}")
    if skip_survey or start_round > 1 or resume_from_step != "proof_search":
        print()
        print("  RESUMING previous run:")
        if skip_survey:
            print("    - Literature survey: SKIP (already complete)")
        if resume_from_step == "verification":
            print(f"    - Proof loop: resuming round {start_round} from structural verification step")
        elif resume_from_step == "detailed_verification":
            print(f"    - Proof loop: resuming round {start_round} from detailed verification step")
        elif resume_from_step.startswith("parallel_"):
            print(f"    - Proof loop: resuming round {start_round} from {resume_from_step}")
        elif start_round > 1:
            print(f"    - Proof loop: resuming from round {start_round}")
    print()

    # -------------------------------------------------------
    # Stage 0: Literature Survey
    # -------------------------------------------------------
    related_info_dir = os.path.join(output_dir, "related_info")
    if skip_survey:
        print("=" * 60)
        print("STAGE 0: Literature Survey  [SKIPPED — already complete]")
        print("=" * 60)
        print(f"  Using existing survey at: {related_info_dir}")
    else:
        print("=" * 60)
        print("STAGE 0: Literature Survey")
        print("=" * 60)
        related_info_dir = await run_literature_survey(
            output_dir, problem_file, claude_opts, prompts_dir,
            config=config, math_skill=proving_skill, tracker=tracker,
        )
        print(f"  Survey saved to: {related_info_dir}")

    # -------------------------------------------------------
    # Parse difficulty for adaptive verification
    # -------------------------------------------------------
    difficulty = _parse_difficulty(output_dir)
    if difficulty != "unknown":
        print(f"  Difficulty: {difficulty.upper()}")
        if difficulty == "easy":
            print("  (Easy mode: using lightweight verification)")
    print()

    # -------------------------------------------------------
    # Multi-model proof search setup (providers already determined above)
    # -------------------------------------------------------
    multi_model_config = None
    if mm_cfg.get("enabled", False) and proof_providers:
        multi_model_config = {
            "providers": proof_providers,
            "config": config,
        }
        print(f"  Multi-model proof search: ACTIVE")
        print(f"  Proof search providers: {', '.join(proof_providers)}")

    # -------------------------------------------------------
    # Multi-verifier setup (providers already determined above)
    # -------------------------------------------------------
    if len(verification_providers) > 1:
        print(f"  Multi-verifier mode: ACTIVE")
        print(f"  Verification providers: {', '.join(verification_providers)}")
    print()

    # -------------------------------------------------------
    # Stage 1: Proof Search Loop
    # -------------------------------------------------------
    print("=" * 60)
    if resume_from_step.startswith("parallel_"):
        print(f"STAGE 1: Proof Search  [RESUMING round {start_round} from {resume_from_step}]")
    elif resume_from_step in ("verification", "detailed_verification"):
        print(f"STAGE 1: Proof Search  [RESUMING round {start_round} from {resume_from_step}]")
    elif start_round > 1:
        print(f"STAGE 1: Proof Search  [RESUMING from round {start_round}]")
    else:
        print("STAGE 1: Proof Search")
    if multi_model_config:
        print(f"  (Parallel mode: {', '.join(multi_model_config['providers'])})")
    print("=" * 60)
    ok = await run_proof_loop(
        output_dir, problem_file, claude_opts, prompts_dir,
        max_proof, related_info_dir=related_info_dir,
        proving_skill=proving_skill, tracker=tracker,
        start_round=start_round,
        resume_from_step=resume_from_step,
        difficulty=difficulty,
        multi_model_config=multi_model_config,
        verification_providers=verification_providers,
        config=config,
    )

    # -------------------------------------------------------
    # Stage 2: Proof Effort Summary
    # -------------------------------------------------------
    summary_file = os.path.join(output_dir, "proof_effort_summary.md")

    if _file_nonempty(summary_file):
        print()
        print("=" * 60)
        print("STAGE 2: Proof Effort Summary  [SKIPPED — already exists]")
        print("=" * 60)
        print(f"  Using existing summary at: {summary_file}")
    else:
        # Count how many rounds actually exist on disk
        verify_dir = os.path.join(output_dir, "verification")
        total_rounds = 0
        if os.path.isdir(verify_dir):
            total_rounds = sum(
                1 for name in os.listdir(verify_dir) if name.startswith("round_")
            )

        outcome = "PASS — Proof verified successfully" if ok else "FAIL — Maximum iterations reached without a verified proof"

        print()
        print("=" * 60)
        print("STAGE 2: Proof Effort Summary")
        print("=" * 60)

        summary_log_dir = os.path.join(output_dir, "summary_log")
        summary_logger = PipelineLogger(summary_log_dir, "Proof Effort Summary")

        # Get summary provider from config (default to claude)
        summary_provider = config.get("pipeline", {}).get("proof_summary", {}).get("provider", "claude")
        summary_logger.update_status(1, 1, "Summary", "RUNNING", f"Writing proof effort summary ({summary_provider})...")

        summary_prompt = load_prompt(
            prompts_dir, "proof_effort_summary.md",
            output_dir=output_dir,
            outcome=outcome,
            total_rounds=total_rounds,
            max_rounds=max_proof,
            summary_file=summary_file,
            error_file=os.path.join(output_dir, "error_proof_effort_summary.md"),
        )
        response = await run_auxiliary_agent(
            provider=summary_provider,
            prompt=summary_prompt,
            working_dir=claude_opts.get("cwd", output_dir),
            config=config,
            claude_opts=claude_opts,
            logger=summary_logger,
            tracker=tracker,
            call_name="Proof Effort Summary",
        )
        _fallback_save_response(response, [summary_file],
            [os.path.join(output_dir, "error_proof_effort_summary.md")],
            summary_logger, step_name="Proof Effort Summary")
        _check_expected_files([
            (summary_file, "proof effort summary"),
            (os.path.join(output_dir, "error_proof_effort_summary.md"), "error log"),
        ], summary_logger, "Proof Effort Summary")
        summary_logger.finalize(1, 1, "FINISHED", "Summary complete.")
        print(f"  Summary saved to: {summary_file}")

    # -------------------------------------------------------
    # Done
    # -------------------------------------------------------
    print()
    print("=" * 60)
    if ok:
        print("  PIPELINE COMPLETE — Proof verified!")
    else:
        print("  PIPELINE STOPPED — Max iterations reached")
    print("=" * 60)
    print(f"  Proof at:    {os.path.join(output_dir, 'proof.md')}")
    print(f"  Summary at:  {summary_file}")
    print(f"  Token usage: {tracker.md_path}")
    print(f"  Output:      {output_dir}")


if __name__ == "__main__":
    asyncio.run(main())
