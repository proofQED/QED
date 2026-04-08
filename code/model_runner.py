#!/usr/bin/env python3
"""Unified multi-model runner for Claude, Codex, and Gemini.

Provides async wrappers around each provider's CLI, returning response text
and feeding token usage into the pipeline's TokenTracker.

All three providers are invoked via their respective CLIs (subprocess),
wrapped in ``asyncio`` executors so the main event loop stays non-blocking.
"""

import asyncio
import json
import os
import subprocess
import tempfile
from datetime import datetime


class ModelRunnerError(Exception):
    """Raised when a model runner encounters a fatal error.

    Attributes:
        provider: The model provider (claude, codex, gemini).
        error_type: Category of error (subprocess_error, non_zero_exit, json_parse_error, empty_response).
        message: Human-readable error message.
        exit_code: Process exit code (if applicable).
        stderr: Stderr output from the CLI (if any).
        stdout: Raw stdout (for debugging).
    """

    def __init__(
        self,
        provider: str,
        error_type: str,
        message: str,
        exit_code: int | None = None,
        stderr: str = "",
        stdout: str = "",
    ):
        self.provider = provider
        self.error_type = error_type
        self.exit_code = exit_code
        self.stderr = stderr
        self.stdout = stdout
        super().__init__(message)

    def __str__(self):
        parts = [f"[{self.provider}] {self.error_type}: {self.args[0]}"]
        if self.exit_code is not None:
            parts.append(f"exit_code={self.exit_code}")
        if self.stderr:
            # Truncate stderr for display
            stderr_preview = self.stderr[:500] + ("..." if len(self.stderr) > 500 else "")
            parts.append(f"stderr={stderr_preview!r}")
        return " | ".join(parts)

    def full_details(self) -> str:
        """Return full error details for logging to file."""
        lines = [
            f"# Model Runner Error",
            f"",
            f"**Provider:** {self.provider}",
            f"**Error Type:** {self.error_type}",
            f"**Message:** {self.args[0]}",
        ]
        if self.exit_code is not None:
            lines.append(f"**Exit Code:** {self.exit_code}")
        if self.stderr:
            lines.extend(["", "## Stderr", "```", self.stderr, "```"])
        if self.stdout:
            lines.extend(["", "## Stdout (first 2000 chars)", "```", self.stdout[:2000], "```"])
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# Claude wrapper
# ---------------------------------------------------------------------------

async def run_claude_agent(
    prompt: str,
    working_dir: str,
    claude_opts: dict,
    logger=None,
    tracker=None,
    call_name: str = "",
    instructions: str | None = None,
) -> str:
    """Run the Claude CLI as a proof-search agent. Returns response text.

    Args:
        prompt: The full prompt string to send.
        working_dir: Directory the agent operates in (cwd for subprocess).
        claude_opts: Dict with keys: cli_path, model, env.
        logger: Optional PipelineLogger for streaming output.
        tracker: Optional TokenTracker for recording token usage.
        call_name: Human-readable label for this call.
        instructions: Optional system prompt to append.
    """
    cli_path = claude_opts.get("cli_path", "claude")
    model = claude_opts.get("model", "opus")
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

    # Build environment: start from inherited env, strip vars that cause
    # provider cross-contamination, then add back only the configured ones.
    _PROVIDER_VARS = ("CLAUDE_CODE_USE_BEDROCK", "ANTHROPIC_API_KEY",
                      "AWS_PROFILE", "ANTHROPIC_MODEL")
    env = {k: v for k, v in os.environ.items() if k not in _PROVIDER_VARS}
    env.update(extra_env)

    def _call():
        return subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            cwd=working_dir,
            env=env,
        )

    start = datetime.now()
    if logger:
        logger.log(f"[Claude] Starting {call_name} (model={model})")

    try:
        result = await asyncio.get_event_loop().run_in_executor(None, _call)
    except Exception as exc:
        elapsed = (datetime.now() - start).total_seconds()
        if logger:
            logger.log(f"[Claude] EXCEPTION: {type(exc).__name__}: {exc}")
        if tracker:
            tracker.record(call_name or "claude", 0, 0, elapsed,
                           provider="claude", model=model)
        raise ModelRunnerError(
            provider="claude",
            error_type="subprocess_error",
            message=f"Failed to execute Claude CLI: {type(exc).__name__}: {exc}",
        )

    elapsed = (datetime.now() - start).total_seconds()

    # Log stderr if present (contains error messages from CLI)
    if result.stderr and result.stderr.strip() and logger:
        logger.log(f"[Claude] stderr:\n{result.stderr.strip()}")

    # --- Parse JSON output ---
    response = ""
    input_tokens = 0
    output_tokens = 0
    json_parse_error = None

    try:
        data = json.loads(result.stdout)
        response = data.get("result", "")

        for _, model_stats in data.get("modelUsage", {}).items():
            input_tokens += model_stats.get("inputTokens", 0)
            output_tokens += model_stats.get("outputTokens", 0)
    except (json.JSONDecodeError, ValueError) as exc:
        json_parse_error = str(exc)
        if logger:
            logger.log(f"[Claude] JSON parse error: {exc}")
            if result.stdout.strip():
                logger.log(f"[Claude] Raw stdout (first 1000 chars): {result.stdout.strip()[:1000]}")
        response = result.stdout.strip()

    # Check for non-zero exit code (indicates CLI failure)
    if result.returncode != 0:
        if logger:
            logger.log(f"[Claude] Non-zero exit code: {result.returncode}")
        if tracker:
            tracker.record(call_name or "claude", input_tokens, output_tokens,
                           elapsed, provider="claude", model=model)
        raise ModelRunnerError(
            provider="claude",
            error_type="non_zero_exit",
            message=f"Claude CLI exited with code {result.returncode}",
            exit_code=result.returncode,
            stderr=result.stderr,
            stdout=result.stdout,
        )

    # Check for empty response (might indicate silent failure)
    if not response.strip():
        if logger:
            logger.log(f"[Claude] Empty response received")
        if tracker:
            tracker.record(call_name or "claude", input_tokens, output_tokens,
                           elapsed, provider="claude", model=model)
        raise ModelRunnerError(
            provider="claude",
            error_type="empty_response",
            message="Claude returned empty response" + (f" (JSON parse error: {json_parse_error})" if json_parse_error else ""),
            exit_code=result.returncode,
            stderr=result.stderr,
            stdout=result.stdout,
        )

    if logger:
        logger.log(f"[Claude] Completed {call_name} in {elapsed:.0f}s "
                    f"({input_tokens} in / {output_tokens} out)")

    if tracker:
        tracker.record(call_name or "claude", input_tokens, output_tokens,
                       elapsed, provider="claude", model=model)

    return response


# ---------------------------------------------------------------------------
# Codex wrapper
# ---------------------------------------------------------------------------

async def run_codex_agent(
    prompt: str,
    working_dir: str,
    codex_config: dict,
    logger=None,
    tracker=None,
    call_name: str = "",
) -> str:
    """Run the Codex CLI as a proof-search agent. Returns response text.

    Args:
        prompt: The full prompt string to send.
        working_dir: Directory the agent operates in (cwd for subprocess).
        codex_config: Dict with keys: cli_path, model, reasoning_effort.
        logger: Optional PipelineLogger for streaming output.
        tracker: Optional TokenTracker for recording token usage.
        call_name: Human-readable label for this call.
    """
    cli_path = codex_config.get("cli_path", "codex")
    model = codex_config.get("model", "gpt-5.4")
    reasoning = codex_config.get("reasoning_effort", "xhigh")

    cmd = [
        cli_path,
        "--search",
        "-m", model,
        "-c", f'model_reasoning_effort="{reasoning}"',
        "exec",
        "--json",
        "--dangerously-bypass-approvals-and-sandbox",
        "-C", working_dir,
        prompt,
    ]

    def _call():
        return subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            cwd=working_dir,
        )

    start = datetime.now()
    if logger:
        logger.log(f"[Codex] Starting {call_name} (model={model})")

    try:
        result = await asyncio.get_event_loop().run_in_executor(None, _call)
    except Exception as exc:
        elapsed = (datetime.now() - start).total_seconds()
        if logger:
            logger.log(f"[Codex] EXCEPTION: {type(exc).__name__}: {exc}")
        if tracker:
            tracker.record(call_name or "codex", 0, 0, elapsed,
                           provider="codex", model=model)
        raise ModelRunnerError(
            provider="codex",
            error_type="subprocess_error",
            message=f"Failed to execute Codex CLI: {type(exc).__name__}: {exc}",
        )

    elapsed = (datetime.now() - start).total_seconds()

    # Log stderr if present (contains error messages from CLI)
    if result.stderr and result.stderr.strip() and logger:
        logger.log(f"[Codex] stderr:\n{result.stderr.strip()}")

    # --- Parse JSONL output (adapted from test_call.py:41-67) ---
    response = ""
    input_tokens = 0
    output_tokens = 0
    json_parse_error = None

    try:
        lines = result.stdout.strip().split("\n")
        events = [json.loads(line) for line in lines if line.strip()]

        for event in events:
            if event.get("type") == "item.completed":
                item = event.get("item", {})
                if item.get("type") == "agent_message":
                    response = item.get("text", "")
            elif event.get("type") == "turn.completed":
                usage = event.get("usage", {})
                input_tokens += usage.get("input_tokens", 0)
                output_tokens += usage.get("output_tokens", 0)
    except (json.JSONDecodeError, ValueError) as exc:
        json_parse_error = str(exc)
        if logger:
            logger.log(f"[Codex] JSON parse error: {exc}")
            if result.stdout.strip():
                logger.log(f"[Codex] Raw stdout (first 1000 chars): {result.stdout.strip()[:1000]}")
        # Fall back to raw stdout as response
        response = result.stdout.strip()

    # Check for non-zero exit code (indicates CLI failure)
    if result.returncode != 0:
        if logger:
            logger.log(f"[Codex] Non-zero exit code: {result.returncode}")
        if tracker:
            tracker.record(call_name or "codex", input_tokens, output_tokens,
                           elapsed, provider="codex", model=model)
        raise ModelRunnerError(
            provider="codex",
            error_type="non_zero_exit",
            message=f"Codex CLI exited with code {result.returncode}",
            exit_code=result.returncode,
            stderr=result.stderr,
            stdout=result.stdout,
        )

    # Check for empty response (might indicate silent failure)
    if not response.strip():
        if logger:
            logger.log(f"[Codex] Empty response received")
        if tracker:
            tracker.record(call_name or "codex", input_tokens, output_tokens,
                           elapsed, provider="codex", model=model)
        raise ModelRunnerError(
            provider="codex",
            error_type="empty_response",
            message="Codex returned empty response" + (f" (JSON parse error: {json_parse_error})" if json_parse_error else ""),
            exit_code=result.returncode,
            stderr=result.stderr,
            stdout=result.stdout,
        )

    if logger:
        logger.log(f"[Codex] Completed {call_name} in {elapsed:.0f}s "
                    f"({input_tokens} in / {output_tokens} out)")

    if tracker:
        tracker.record(call_name or "codex", input_tokens, output_tokens,
                       elapsed, provider="codex", model=model)

    return response


# ---------------------------------------------------------------------------
# Gemini wrapper
# ---------------------------------------------------------------------------

async def run_gemini_agent(
    prompt: str,
    working_dir: str,
    gemini_config: dict,
    logger=None,
    tracker=None,
    call_name: str = "",
) -> str:
    """Run the Gemini CLI as a proof-search agent. Returns response text.

    Args:
        prompt: The full prompt string to send.
        working_dir: Directory the agent operates in (cwd for subprocess).
        gemini_config: Dict with keys: cli_path, model, api_key,
            approval_mode, thinking_level, thinking_budget.
        logger: Optional PipelineLogger for streaming output.
        tracker: Optional TokenTracker for recording token usage.
        call_name: Human-readable label for this call.
    """
    cli_path = gemini_config.get("cli_path", "gemini")
    model = gemini_config.get("model", "gemini-3-flash-preview")
    api_key = gemini_config.get("api_key", "")
    approval_mode = gemini_config.get("approval_mode", "yolo")
    thinking_level = gemini_config.get("thinking_level", "")
    thinking_budget = gemini_config.get("thinking_budget")

    cmd = [
        cli_path,
        "-m", model,
        "--approval-mode", approval_mode,
        "-o", "json",   # JSON output for metadata extraction
        "-p", prompt,
    ]

    def _call():
        env = os.environ.copy()
        if api_key:
            env["GEMINI_API_KEY"] = api_key

        thinking_config = {}
        if thinking_level:
            thinking_config["thinkingLevel"] = thinking_level
        if thinking_budget is not None:
            thinking_config["thinkingBudget"] = thinking_budget

        if thinking_config:
            with tempfile.TemporaryDirectory(prefix="qed-gemini-home-") as gemini_home:
                settings_dir = os.path.join(gemini_home, ".gemini")
                os.makedirs(settings_dir, exist_ok=True)
                settings_path = os.path.join(settings_dir, "settings.json")
                settings = {
                    "modelConfigs": {
                        "overrides": [
                            {
                                "match": {"model": model},
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
                env["GEMINI_CLI_HOME"] = gemini_home
                return subprocess.run(
                    cmd,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                    cwd=working_dir,
                    env=env,
                )

        return subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            cwd=working_dir,
            env=env,
        )

    start = datetime.now()
    if logger:
        logger.log(f"[Gemini] Starting {call_name} (model={model})")

    try:
        result = await asyncio.get_event_loop().run_in_executor(None, _call)
    except Exception as exc:
        elapsed = (datetime.now() - start).total_seconds()
        if logger:
            logger.log(f"[Gemini] EXCEPTION: {type(exc).__name__}: {exc}")
        if tracker:
            tracker.record(call_name or "gemini", 0, 0, elapsed,
                           provider="gemini", model=model)
        raise ModelRunnerError(
            provider="gemini",
            error_type="subprocess_error",
            message=f"Failed to execute Gemini CLI: {type(exc).__name__}: {exc}",
        )

    elapsed = (datetime.now() - start).total_seconds()

    # Log stderr if present (contains error messages from CLI)
    if result.stderr and result.stderr.strip() and logger:
        logger.log(f"[Gemini] stderr:\n{result.stderr.strip()}")

    # --- Parse JSON output (adapted from test_call.py:74-121) ---
    response = ""
    input_tokens = 0
    output_tokens = 0
    json_parse_error = None

    try:
        data = json.loads(result.stdout)
        response = data.get("response", "")

        for _, model_stats in data.get("stats", {}).get("models", {}).items():
            tokens = model_stats.get("tokens", {})
            input_tokens += tokens.get("input", 0)
            output_tokens += tokens.get("candidates", 0)
            output_tokens += tokens.get("thoughts", 0)  # include thinking tokens
    except (json.JSONDecodeError, ValueError) as exc:
        json_parse_error = str(exc)
        if logger:
            logger.log(f"[Gemini] JSON parse error: {exc}")
            if result.stdout.strip():
                logger.log(f"[Gemini] Raw stdout (first 1000 chars): {result.stdout.strip()[:1000]}")
        response = result.stdout.strip()

    # Check for non-zero exit code (indicates CLI failure)
    if result.returncode != 0:
        if logger:
            logger.log(f"[Gemini] Non-zero exit code: {result.returncode}")
        if tracker:
            tracker.record(call_name or "gemini", input_tokens, output_tokens,
                           elapsed, provider="gemini", model=model)
        raise ModelRunnerError(
            provider="gemini",
            error_type="non_zero_exit",
            message=f"Gemini CLI exited with code {result.returncode}",
            exit_code=result.returncode,
            stderr=result.stderr,
            stdout=result.stdout,
        )

    # Check for empty response (might indicate silent failure)
    if not response.strip():
        if logger:
            logger.log(f"[Gemini] Empty response received")
        if tracker:
            tracker.record(call_name or "gemini", input_tokens, output_tokens,
                           elapsed, provider="gemini", model=model)
        raise ModelRunnerError(
            provider="gemini",
            error_type="empty_response",
            message="Gemini returned empty response" + (f" (JSON parse error: {json_parse_error})" if json_parse_error else ""),
            exit_code=result.returncode,
            stderr=result.stderr,
            stdout=result.stdout,
        )

    if logger:
        logger.log(f"[Gemini] Completed {call_name} in {elapsed:.0f}s "
                    f"({input_tokens} in / {output_tokens} out)")

    if tracker:
        tracker.record(call_name or "gemini", input_tokens, output_tokens,
                       elapsed, provider="gemini", model=model)

    return response


# ---------------------------------------------------------------------------
# Unified dispatcher
# ---------------------------------------------------------------------------

async def run_model(
    provider: str,
    prompt: str,
    working_dir: str,
    config: dict,
    *,
    claude_opts: dict | None = None,
    logger=None,
    tracker=None,
    call_name: str = "",
    instructions: str | None = None,
) -> str:
    """Dispatch a prompt to the specified model provider.

    Args:
        provider: One of "claude", "codex", "gemini".
        prompt: The full prompt string.
        working_dir: Agent's working directory.
        config: Full pipeline config dict (with claude/codex/gemini sections).
        claude_opts: Claude CLI options dict (required when provider="claude").
        logger: Optional PipelineLogger.
        tracker: Optional TokenTracker.
        call_name: Human-readable label.
        instructions: System instructions (used only for Claude).

    Returns:
        The agent's response text.
    """
    if provider == "claude":
        return await run_claude_agent(
            prompt, working_dir, claude_opts or {},
            logger=logger, tracker=tracker, call_name=call_name,
            instructions=instructions,
        )
    elif provider == "codex":
        return await run_codex_agent(
            prompt, working_dir, config.get("codex", {}),
            logger=logger, tracker=tracker, call_name=call_name,
        )
    elif provider == "gemini":
        return await run_gemini_agent(
            prompt, working_dir, config.get("gemini", {}),
            logger=logger, tracker=tracker, call_name=call_name,
        )
    else:
        raise ValueError(f"Unknown model provider: {provider!r}. "
                         f"Expected 'claude', 'codex', or 'gemini'.")
