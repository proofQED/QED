#!/usr/bin/env python3
"""
Proof Agent pipeline: takes a problem.tex (LaTeX) and produces a natural-language proof.

Four-agent pipeline:
  0. Literature Survey agent — deep-dives into the problem context and related results
  Then iterative loop:
  1. Proof Search agent  — writes/refines the proof (informed by the survey)
  2. Verification agent  — checks the proof for correctness
  3. Verdict agent       — decides DONE or CONTINUE
"""

import asyncio
import argparse
import json
import os
import re
import shutil
import sys
from datetime import datetime
from pathlib import Path

import yaml
from agent_framework.anthropic import ClaudeAgent


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
    """Build ClaudeAgent default_options from config.

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
        "permission_mode": claude_cfg.get("permission_mode", "bypassPermissions"),
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

        with open(self.history_file, "w") as f:
            f.write("")
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
    after every update so the user can check TOKEN_USAGE.md at any time."""

    def __init__(self, output_dir: str, model: str):
        self.output_dir = output_dir
        self.model = model
        self.calls: list[dict] = []
        self.total_input = 0
        self.total_output = 0
        self.total_elapsed = 0.0
        self.md_path = os.path.join(output_dir, "TOKEN_USAGE.md")
        self.json_path = os.path.join(output_dir, "token_usage.json")
        self.start_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    def record(self, call_name: str, input_tokens: int, output_tokens: int, elapsed: float):
        self.total_input += input_tokens
        self.total_output += output_tokens
        self.total_elapsed += elapsed
        self.calls.append({
            "call": len(self.calls) + 1,
            "name": call_name,
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
            f"**Model:** `{self.model}`  ",
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
            "## Per-Call Breakdown\n",
            "| # | Agent | Input | Output | Time | Cumul In | Cumul Out |",
            "|---|-------|------:|-------:|-----:|---------:|----------:|",
        ]
        for c in self.calls:
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
            "calls": self.calls,
        }
        with open(self.json_path, "w") as f:
            json.dump(data, f, indent=2)


# ---------------------------------------------------------------------------
# Agent runners
# ---------------------------------------------------------------------------

def _format_content_for_log(content) -> str | None:
    """Format a Content object into a human-readable log line."""
    ctype = getattr(content, "type", None)
    if ctype is None:
        return None

    ctype_str = str(ctype)

    if "function_call" in ctype_str:
        name = getattr(content, "name", "") or ""
        args = getattr(content, "arguments", "") or ""
        if isinstance(args, dict):
            cmd = args.get("command", "")
            if cmd:
                preview = cmd[:200] + ("..." if len(cmd) > 200 else "")
                return f">>> Tool: {name} - {preview}"
            args_preview = str(args)[:150]
            return f">>> Tool: {name}({args_preview})"
        return f">>> Tool: {name}"

    if "shell_tool" in ctype_str or "shell_command" in ctype_str:
        cmds = getattr(content, "commands", None) or []
        cmd_str = "; ".join(cmds)[:200] if cmds else ""
        return f">>> Shell: {cmd_str}" if cmd_str else None

    if "function_result" in ctype_str:
        name = getattr(content, "name", "") or ""
        exc = getattr(content, "exception", None)
        if exc:
            return f"<<< Result ({name}): ERROR - {str(exc)[:100]}"
        return None

    if "text" in ctype_str:
        text = getattr(content, "text", "") or ""
        if len(text) > 20:
            return text[:300] + ("..." if len(text) > 300 else "")
        return None

    return None


async def run_agent(
    claude_opts: dict,
    prompt: str,
    logger: PipelineLogger | None = None,
    tools: list | None = None,
    instructions: str | None = None,
    tracker: TokenTracker | None = None,
    call_name: str = "",
) -> str:
    """Run a single ClaudeAgent call with streaming output logged in real time."""
    start_time = datetime.now()
    text_buffer = ""

    def flush_text():
        nonlocal text_buffer
        if logger and text_buffer.strip():
            logger.log(text_buffer.rstrip())
        text_buffer = ""

    agent_kwargs = {}
    if tools:
        agent_kwargs["tools"] = tools
    if instructions:
        agent_kwargs["instructions"] = instructions

    async with ClaudeAgent(default_options=claude_opts, **agent_kwargs) as agent:
        stream = agent.run(prompt, stream=True)
        async for update in stream:
            if logger and hasattr(update, "contents") and update.contents:
                for content in update.contents:
                    ctype_str = str(getattr(content, "type", ""))
                    if "text" in ctype_str:
                        delta = getattr(content, "text", "") or ""
                        text_buffer += delta
                        while "\n" in text_buffer:
                            line, text_buffer = text_buffer.split("\n", 1)
                            if line.strip():
                                logger.log(line)
                    else:
                        flush_text()
                        line = _format_content_for_log(content)
                        if line:
                            logger.log(line)

        flush_text()
        final = await stream.get_final_response()
        elapsed = (datetime.now() - start_time).total_seconds()
        final_text = final.text or ""

        # Extract token counts
        input_tokens = 0
        output_tokens = 0
        usage = getattr(final, "usage_details", None)
        if usage:
            input_tokens = (usage.get("input_token_count", 0) if isinstance(usage, dict)
                            else getattr(usage, "input_token_count", 0)) or 0
            output_tokens = (usage.get("output_token_count", 0) if isinstance(usage, dict)
                             else getattr(usage, "output_token_count", 0)) or 0

        if logger:
            if input_tokens or output_tokens:
                logger.log(f"--- Stats: {elapsed:.0f}s | input={input_tokens} output={output_tokens} ---")
            else:
                logger.log(f"--- Stats: {elapsed:.0f}s ---")

        if tracker and (input_tokens or output_tokens):
            tracker.record(call_name or "agent", input_tokens, output_tokens, elapsed)

        return final_text


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


# ---------------------------------------------------------------------------
# Literature survey
# ---------------------------------------------------------------------------

async def run_literature_survey(
    output_dir: str,
    problem_file: str,
    claude_opts: dict,
    prompts_dir: str,
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

    survey_prompt = load_prompt(
        prompts_dir, "literature_survey.md",
        problem_file=problem_file,
        related_info_dir=related_info_dir,
        output_dir=output_dir,
    )

    await run_agent(claude_opts, survey_prompt, logger, instructions=math_skill or None,
                    tracker=tracker, call_name="Literature Survey")

    logger.finalize(1, 1, "FINISHED", "Literature survey complete.")
    return related_info_dir


# ---------------------------------------------------------------------------
# Proof search loop
# ---------------------------------------------------------------------------

async def run_proof_loop(
    output_dir: str,
    problem_file: str,
    claude_opts: dict,
    prompts_dir: str,
    max_iterations: int,
    related_info_dir: str,
    proving_skill: str = "",
    tracker: TokenTracker | None = None,
) -> bool:
    """Run the proof search/verification/verdict loop.
    Returns True if successful (DONE), False if max iterations reached.
    """
    proof_file = os.path.join(output_dir, "proof.md")
    verify_dir = os.path.join(output_dir, "verification")

    logger = PipelineLogger(verify_dir, "Proof Search")

    # Create initial empty proof file
    if not os.path.exists(proof_file):
        with open(proof_file, "w") as f:
            f.write("<!-- Proof will be written here by the proof search agent -->\n")

    for i in range(1, max_iterations + 1):
        round_dir = os.path.join(verify_dir, f"round_{i}")
        os.makedirs(round_dir, exist_ok=True)
        proof_status_file = os.path.join(round_dir, "proof_status.md")
        verify_result_file = os.path.join(round_dir, "verification_result.md")

        prev_verify = os.path.join(verify_dir, f"round_{i-1}", "verification_result.md")
        prev_proof_status = os.path.join(verify_dir, f"round_{i-1}", "proof_status.md")

        logger.log(f"\n========================================")
        logger.log(f"=== ITERATION {i} of {max_iterations} ===")
        logger.log(f"========================================")
        logger.append_history(f"Iteration {i} started (round dir: round_{i})")

        # Build previous-round instructions
        prev_instructions = ""
        if os.path.exists(prev_verify):
            prev_instructions += f"- Read the PREVIOUS round's verification result from {prev_verify}.\n"
        if os.path.exists(prev_proof_status):
            prev_instructions += f"- Read the PREVIOUS round's proof status from {prev_proof_status}. It contains which approaches were tried and FAILED — do NOT repeat these.\n"
        if not prev_instructions:
            prev_instructions = "- This is the first round. No previous round data available.\n"

        # Step 1: Proof search
        logger.update_status(i, max_iterations, "1/3 Proof Search", "RUNNING", "Running proof search agent...")
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
        )
        search_prompt += f"\n\nThis is round {i}. Write or refine the proof. If one approach doesn't work after much effort, try a completely different proof strategy."

        await run_agent(claude_opts, search_prompt, logger, instructions=proving_skill or None,
                        tracker=tracker, call_name=f"Proof Search R{i}")
        logger.append_history(f"Iteration {i}: Proof search completed")

        # Step 2: Verification
        logger.update_status(i, max_iterations, "2/3 Verification", "RUNNING", "Running verification agent...")
        logger.append_history(f"Iteration {i}: Verification started")

        verify_prompt = load_prompt(
            prompts_dir, "proof_verify.md",
            problem_file=problem_file,
            proof_file=proof_file,
            output_file=verify_result_file,
            output_dir=output_dir,
        )
        verify_prompt += f"\n\nThis is round {i}. Write results to {verify_result_file}."

        await run_agent(claude_opts, verify_prompt, logger,
                        tracker=tracker, call_name=f"Verification R{i}")
        logger.append_history(f"Iteration {i}: Verification completed")

        # Step 3: Verdict
        logger.update_status(i, max_iterations, "3/3 Checking Verdict", "RUNNING", "Analyzing verification results...")
        logger.append_history(f"Iteration {i}: Checking verdict")

        verdict_prompt = load_prompt(
            prompts_dir, "verdict_proof.md",
            verification_result_file=verify_result_file,
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

    # Build ClaudeAgent options
    claude_opts = make_claude_options(claude_cfg, output_dir)

    # Token usage tracker — writes TOKEN_USAGE.md after every agent call
    tracker = TokenTracker(output_dir, claude_opts["model"])

    print("=" * 60)
    print("  Proof Agent Pipeline")
    print("=" * 60)
    print(f"  Problem:    {problem_file}")
    print(f"  Output:     {output_dir}")
    print(f"  Max rounds: {max_proof}")
    print(f"  Token log:  {tracker.md_path}")
    print()

    # -------------------------------------------------------
    # Stage 0: Literature Survey
    # -------------------------------------------------------
    print("=" * 60)
    print("STAGE 0: Literature Survey")
    print("=" * 60)
    related_info_dir = await run_literature_survey(
        output_dir, problem_file, claude_opts, prompts_dir,
        math_skill=proving_skill, tracker=tracker,
    )
    print(f"  Survey saved to: {related_info_dir}")

    # -------------------------------------------------------
    # Stage 1: Proof Search Loop
    # -------------------------------------------------------
    print()
    print("=" * 60)
    print("STAGE 1: Proof Search")
    print("=" * 60)
    ok = await run_proof_loop(
        output_dir, problem_file, claude_opts, prompts_dir,
        max_proof, related_info_dir=related_info_dir,
        proving_skill=proving_skill, tracker=tracker,
    )

    print()
    print("=" * 60)
    if ok:
        print("  PIPELINE COMPLETE — Proof verified!")
    else:
        print("  PIPELINE STOPPED — Max iterations reached")
    print("=" * 60)
    print(f"  Proof at:    {os.path.join(output_dir, 'proof.md')}")
    print(f"  Token usage: {tracker.md_path}")
    print(f"  Output:      {output_dir}")


if __name__ == "__main__":
    asyncio.run(main())
