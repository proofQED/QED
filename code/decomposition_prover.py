#!/usr/bin/env python3
"""Decomposition-based prover for the QED pipeline.

This module implements a sophisticated proof workflow that:
1. Decomposes a problem into intermediate steps
2. Proves each step individually (key steps first)
3. Verifies each step proof
4. Uses a regulator to decide when to revise or rewrite the decomposition
5. Aggregates all step proofs into a final proof.md

This is an optional alternative to the "simple" prover mode in pipeline.py.
"""

import asyncio
import json
import os
import yaml
from datetime import datetime
from pathlib import Path
from typing import Any

from model_runner import run_model, run_claude_agent, ModelRunnerError


# ---------------------------------------------------------------------------
# Configuration defaults
# ---------------------------------------------------------------------------

DEFAULT_CONFIG = {
    "max_prover_rounds": 5,      # rounds per step before asking regulator
    "max_revisions": 3,          # local revisions per step
    "max_decompositions": 3,     # complete rewrites allowed
}

DEFAULT_MODELS = {
    "decomposer": "claude",
    "step_prover": "claude",
    "step_verifier": "claude",
    "regulator": "claude",
    "proof_aggregator": "claude",
}


# ---------------------------------------------------------------------------
# Helper functions
# ---------------------------------------------------------------------------

def load_prompt(prompts_dir: str, name: str, **kwargs) -> str:
    """Load a prompt template and fill placeholders."""
    path = os.path.join(prompts_dir, name)
    with open(path) as f:
        template = f.read()
    # Use safe formatting that doesn't fail on missing keys
    for key, value in kwargs.items():
        template = template.replace("{" + key + "}", str(value))
    return template


def read_file(path: str) -> str:
    """Read file contents, return empty string if not found."""
    try:
        with open(path) as f:
            return f.read()
    except FileNotFoundError:
        return ""


def write_file(path: str, content: str) -> None:
    """Write content to file, creating directories as needed."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        f.write(content)


def parse_decomposition(yaml_content: str) -> dict:
    """Parse decomposition YAML from agent response.

    Extracts the YAML block from markdown code fences if present.
    """
    # Try to extract YAML from code block
    if "```yaml" in yaml_content:
        start = yaml_content.find("```yaml") + 7
        end = yaml_content.find("```", start)
        yaml_content = yaml_content[start:end].strip()
    elif "```" in yaml_content:
        start = yaml_content.find("```") + 3
        end = yaml_content.find("```", start)
        yaml_content = yaml_content[start:end].strip()

    return yaml.safe_load(yaml_content)


def parse_verdict(response: str) -> str:
    """Extract PASS or FAIL from verifier response."""
    response_upper = response.upper()
    # Look for verdict in markdown header
    if "### VERDICT: PASS" in response_upper or "**VERDICT**: PASS" in response_upper:
        return "PASS"
    if "### VERDICT: FAIL" in response_upper or "**VERDICT**: FAIL" in response_upper:
        return "FAIL"
    # Look for standalone PASS/FAIL
    if "VERDICT: PASS" in response_upper:
        return "PASS"
    if "VERDICT: FAIL" in response_upper:
        return "FAIL"
    # Default to FAIL if unclear
    return "FAIL"


# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

class DecompositionLogger:
    """Logger for decomposition prover that writes to multiple log files."""

    def __init__(self, output_dir: str):
        self.output_dir = output_dir
        self.log_dir = os.path.join(output_dir, "decomposition", "logs")
        os.makedirs(self.log_dir, exist_ok=True)

        # Main status log
        self.status_file = os.path.join(self.log_dir, "STATUS.md")
        self.main_log_file = os.path.join(self.log_dir, "decomposition_log.txt")

        # Agent-specific logs
        self.agent_logs = {
            "decomposer": os.path.join(self.log_dir, "decomposer_log.txt"),
            "step_prover": os.path.join(self.log_dir, "step_prover_log.txt"),
            "step_verifier": os.path.join(self.log_dir, "step_verifier_log.txt"),
            "regulator": os.path.join(self.log_dir, "regulator_log.txt"),
            "proof_aggregator": os.path.join(self.log_dir, "proof_aggregator_log.txt"),
        }

        # Initialize status
        self._write_status({
            "state": "STARTING",
            "attempt": 1,
            "revision": 0,
            "current_step": None,
            "current_round": 0,
            "steps_proved": [],
            "steps_failed": [],
            "last_update": datetime.now().isoformat(),
        })

    def _timestamp(self) -> str:
        return datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    def _write_status(self, status: dict) -> None:
        """Write current status to STATUS.md."""
        content = f"""# Decomposition Prover Status

**Last Updated:** {status.get('last_update', datetime.now().isoformat())}

## Current State

| Field | Value |
|-------|-------|
| State | {status.get('state', 'UNKNOWN')} |
| Decomposition Attempt | {status.get('attempt', 1)} |
| Revision | {status.get('revision', 0)} |
| Current Step | {status.get('current_step', 'None')} |
| Current Round | {status.get('current_round', 0)} |

## Progress

**Steps Proved:** {', '.join(status.get('steps_proved', [])) or 'None'}

**Steps Failed:** {', '.join(status.get('steps_failed', [])) or 'None'}

## Recent Activity

{status.get('recent_activity', '')}
"""
        write_file(self.status_file, content)

    def update_status(
        self,
        state: str = None,
        attempt: int = None,
        revision: int = None,
        current_step: str = None,
        current_round: int = None,
        steps_proved: list = None,
        steps_failed: list = None,
        recent_activity: str = None,
    ) -> None:
        """Update the status file with current state."""
        # Read existing status
        existing = {}
        if os.path.exists(self.status_file):
            # Parse existing values (simple approach)
            existing = {
                "state": "UNKNOWN",
                "attempt": 1,
                "revision": 0,
                "current_step": None,
                "current_round": 0,
                "steps_proved": [],
                "steps_failed": [],
                "recent_activity": "",
            }

        # Update with new values
        if state is not None:
            existing["state"] = state
        if attempt is not None:
            existing["attempt"] = attempt
        if revision is not None:
            existing["revision"] = revision
        if current_step is not None:
            existing["current_step"] = current_step
        if current_round is not None:
            existing["current_round"] = current_round
        if steps_proved is not None:
            existing["steps_proved"] = steps_proved
        if steps_failed is not None:
            existing["steps_failed"] = steps_failed
        if recent_activity is not None:
            existing["recent_activity"] = recent_activity

        existing["last_update"] = datetime.now().isoformat()
        self._write_status(existing)

    def log(self, message: str, agent: str = None) -> None:
        """Log a message to the main log and optionally to an agent-specific log."""
        timestamp = self._timestamp()
        log_line = f"[{timestamp}] {message}\n"

        # Write to main log
        with open(self.main_log_file, "a") as f:
            f.write(log_line)

        # Write to agent-specific log if specified
        if agent and agent in self.agent_logs:
            with open(self.agent_logs[agent], "a") as f:
                f.write(log_line)

        # Also print to console
        print(f"[DecompProver] {message}")

    def log_agent_call(
        self,
        agent: str,
        action: str,
        model: str,
        details: dict = None,
    ) -> None:
        """Log an agent call with details."""
        details_str = ""
        if details:
            details_str = " | " + " | ".join(f"{k}={v}" for k, v in details.items())
        message = f"[{agent.upper()}] {action} (model={model}){details_str}"
        self.log(message, agent=agent)

    def log_agent_result(
        self,
        agent: str,
        result: str,
        elapsed: float = None,
        tokens_in: int = None,
        tokens_out: int = None,
    ) -> None:
        """Log an agent result."""
        parts = [f"[{agent.upper()}] Result: {result}"]
        if elapsed is not None:
            parts.append(f"elapsed={elapsed:.1f}s")
        if tokens_in is not None:
            parts.append(f"tokens_in={tokens_in}")
        if tokens_out is not None:
            parts.append(f"tokens_out={tokens_out}")
        self.log(" | ".join(parts), agent=agent)

    def save_agent_output(
        self,
        agent: str,
        output: str,
        filename: str,
    ) -> None:
        """Save agent output to a file."""
        path = os.path.join(self.log_dir, filename)
        write_file(path, output)
        self.log(f"[{agent.upper()}] Output saved to {filename}", agent=agent)


def parse_regulator_decision(response: str) -> str:
    """Extract decision from regulator response."""
    response_upper = response.upper()
    for decision in ["REVISE", "REWRITE"]:
        if f"DECISION: {decision}" in response_upper:
            return decision
        if f"## DECISION: {decision}" in response_upper:
            return decision
    # Default to REVISE if unclear (prefer local fix over full rewrite)
    return "REVISE"


# ---------------------------------------------------------------------------
# Decomposition state management
# ---------------------------------------------------------------------------

class DecompositionState:
    """Tracks the state of a decomposition-based proof attempt."""

    def __init__(self, output_dir: str):
        self.output_dir = output_dir
        self.decomp_dir = os.path.join(output_dir, "decomposition")
        self.attempt = 1
        self.revision = 0
        self.decomposition = None
        self.step_proofs = {}  # step_id -> proof content
        self.step_results = {}  # step_id -> "proved" | "failed"
        self.attempt_history = []  # list of failure info for rewrites

    def get_attempt_dir(self) -> str:
        """Get directory for current attempt."""
        return os.path.join(self.decomp_dir, f"attempt_{self.attempt}")

    def get_revision_dir(self) -> str:
        """Get directory for current revision."""
        return os.path.join(self.get_attempt_dir(), f"revision_{self.revision}")

    def save_decomposition(self, decomposition: dict) -> None:
        """Save decomposition to file."""
        self.decomposition = decomposition
        path = os.path.join(self.get_attempt_dir(), "decomposition.yaml")
        write_file(path, yaml.dump(decomposition, default_flow_style=False))

    def load_decomposition(self) -> dict | None:
        """Load decomposition from file."""
        path = os.path.join(self.get_attempt_dir(), "decomposition.yaml")
        content = read_file(path)
        if content:
            self.decomposition = yaml.safe_load(content)
            return self.decomposition
        return None

    def save_step_proof(self, step_id: str, proof: str) -> None:
        """Save a step proof."""
        self.step_proofs[step_id] = proof
        path = os.path.join(self.get_revision_dir(), f"step_{step_id}_proof.md")
        write_file(path, proof)

    def save_step_verification(self, step_id: str, verification: str) -> None:
        """Save a step verification result."""
        path = os.path.join(self.get_revision_dir(), f"step_{step_id}_verify.md")
        write_file(path, verification)

    def save_regulator_decision(self, step_id: str, decision: str, response: str) -> None:
        """Save regulator decision."""
        path = os.path.join(self.get_revision_dir(), "regulator_decisions.md")
        existing = read_file(path)
        entry = f"\n## Step {step_id}\n\n{response}\n\n---\n"
        write_file(path, existing + entry)

    def mark_step_proved(self, step_id: str) -> None:
        """Mark a step as proved."""
        self.step_results[step_id] = "proved"
        if self.decomposition:
            for step in self.decomposition.get("steps", []):
                if step["id"] == step_id:
                    step["status"] = "proved"
                    break
            self.save_decomposition(self.decomposition)

    def mark_step_failed(self, step_id: str) -> None:
        """Mark a step as failed."""
        self.step_results[step_id] = "failed"

    def new_revision(self) -> None:
        """Start a new revision - clear all step results and start fresh."""
        self.revision += 1
        self.step_proofs = {}
        self.step_results = {}
        os.makedirs(self.get_revision_dir(), exist_ok=True)

    def new_attempt(self) -> None:
        """Start a completely new decomposition attempt."""
        # Save failure history
        if self.decomposition:
            self.attempt_history.append({
                "attempt": self.attempt,
                "revision": self.revision,
                "decomposition": self.decomposition,
                "step_results": self.step_results.copy(),
            })
        # Reset for new attempt
        self.attempt += 1
        self.revision = 0
        self.decomposition = None
        self.step_proofs = {}
        self.step_results = {}
        os.makedirs(self.get_attempt_dir(), exist_ok=True)
        os.makedirs(self.get_revision_dir(), exist_ok=True)

    def get_failure_history(self) -> str:
        """Get formatted failure history for rewrite mode."""
        if not self.attempt_history:
            return "No previous attempts."

        lines = ["# Previous Attempt Failures\n"]
        for hist in self.attempt_history:
            lines.append(f"## Attempt {hist['attempt']}\n")
            lines.append(f"Revisions tried: {hist['revision']}\n")
            lines.append(f"Step results:\n")
            for step_id, result in hist["step_results"].items():
                lines.append(f"- {step_id}: {result}\n")
            lines.append("\n")
        return "".join(lines)


# ---------------------------------------------------------------------------
# Helper to get model for an agent
# ---------------------------------------------------------------------------

def get_agent_model(config: dict, agent_name: str) -> str:
    """Get the model provider for a specific agent from config."""
    decomp_config = config.get("decomposition", {})
    models = decomp_config.get("models", DEFAULT_MODELS)
    return models.get(agent_name, DEFAULT_MODELS.get(agent_name, "claude"))


def get_claude_opts_for_model(config: dict, model_provider: str) -> dict:
    """Get claude_opts dict for the specified model provider."""
    if model_provider == "claude":
        claude_cfg = config.get("claude", {})
        provider = claude_cfg.get("provider", "subscription")
        if provider == "subscription":
            return {
                "cli_path": claude_cfg.get("cli_path", "claude"),
                "model": claude_cfg.get("subscription", {}).get("model", "opus"),
                "env": {},
            }
        elif provider == "bedrock":
            bedrock = claude_cfg.get("bedrock", {})
            return {
                "cli_path": claude_cfg.get("cli_path", "claude"),
                "model": bedrock.get("model", "us.anthropic.claude-sonnet-4-20250514"),
                "env": {
                    "CLAUDE_CODE_USE_BEDROCK": "1",
                    "AWS_PROFILE": bedrock.get("aws_profile", "default"),
                },
            }
        elif provider == "api_key":
            api_cfg = claude_cfg.get("api_key", {})
            return {
                "cli_path": claude_cfg.get("cli_path", "claude"),
                "model": api_cfg.get("model", "claude-sonnet-4-20250514"),
                "env": {
                    "ANTHROPIC_API_KEY": api_cfg.get("key", ""),
                },
            }
    # For codex/gemini, return empty dict - run_model will use config directly
    return {}


# ---------------------------------------------------------------------------
# Agent runners
# ---------------------------------------------------------------------------

async def run_decomposer(
    state: DecompositionState,
    problem_file: str,
    related_work_file: str,
    difficulty_file: str,
    prompts_dir: str,
    config: dict,
    claude_opts: dict,
    mode: str = "CREATE",
    failed_step_id: str = "",
    failure_feedback: str = "",
    decomp_logger: DecompositionLogger = None,
    tracker=None,
) -> dict:
    """Run the decomposer agent to create/revise/rewrite a decomposition."""

    model_provider = get_agent_model(config, "decomposer")

    # Build revision context based on mode
    revision_context = ""
    if mode == "REVISE":
        revision_context = f"""
### Current Decomposition (to revise)
```
{state.get_attempt_dir()}/decomposition.yaml
```

### Failed Step
ID: {failed_step_id}

### Failure Feedback
{failure_feedback}

### Prover Attempts
```
{state.get_revision_dir()}/step_{failed_step_id}_proof.md
```
"""
    elif mode == "REWRITE":
        revision_context = f"""
### Failure History
{state.get_failure_history()}

### Previous Decomposition Attempts
See {state.decomp_dir}/attempt_*/decomposition.yaml
"""

    prompt = load_prompt(
        prompts_dir,
        "decomposition-prover/decomposition.md",
        mode=mode,
        problem_file=problem_file,
        related_work_file=related_work_file,
        difficulty_file=difficulty_file,
        revision_context=revision_context,
        problem_id=os.path.basename(problem_file),
        attempt_number=state.attempt,
        revision_number=state.revision,
        timestamp=datetime.now().isoformat(),
        output_file=os.path.join(state.get_attempt_dir(), "decomposition.yaml"),
        current_decomposition_file=os.path.join(state.get_attempt_dir(), "decomposition.yaml"),
        failed_step_id=failed_step_id,
        failure_feedback=failure_feedback,
        prover_attempts_file=os.path.join(state.get_revision_dir(), f"step_{failed_step_id}_proof.md"),
        failure_history_file=os.path.join(state.decomp_dir, "failure_history.md"),
    )

    if decomp_logger:
        decomp_logger.log_agent_call(
            "decomposer", f"{mode} mode",
            model_provider,
            {"attempt": state.attempt, "revision": state.revision}
        )
        decomp_logger.update_status(
            state="DECOMPOSING",
            attempt=state.attempt,
            revision=state.revision,
            recent_activity=f"Running decomposer in {mode} mode"
        )

    # Run the model
    response = await run_model(
        provider=model_provider,
        prompt=prompt,
        working_dir=state.output_dir,
        config=config,
        claude_opts=get_claude_opts_for_model(config, model_provider) if model_provider == "claude" else claude_opts,
        tracker=tracker,
        call_name=f"decomposer_{mode.lower()}",
    )

    # Save output log
    if decomp_logger:
        decomp_logger.save_agent_output(
            "decomposer",
            response,
            f"decomposer_{mode.lower()}_attempt{state.attempt}_rev{state.revision}.md"
        )
        decomp_logger.log_agent_result("decomposer", f"{mode} completed")

    # Parse and save decomposition
    decomposition = parse_decomposition(response)
    state.save_decomposition(decomposition)

    return decomposition


async def run_step_prover(
    state: DecompositionState,
    step: dict,
    inputs: list[dict],
    problem_file: str,
    related_work_file: str,
    prompts_dir: str,
    config: dict,
    claude_opts: dict,
    round_number: int = 1,
    previous_attempts: str = "",
    decomp_logger: DecompositionLogger = None,
    tracker=None,
) -> str:
    """Run the step prover agent to prove a single step."""

    model_provider = get_agent_model(config, "step_prover")

    # Build step file content
    step_content = yaml.dump(step, default_flow_style=False)

    # Build inputs file content
    inputs_content = "# Input Statements\n\n"
    for inp in inputs:
        inputs_content += f"## {inp['id']}\n\n{inp.get('statement', '')}\n\n"

    # Build previous attempts context
    previous_attempts_context = ""
    if previous_attempts:
        previous_attempts_context = f"""
### Previous Attempts

This is round {round_number} of proving this step. Previous attempts:

{previous_attempts}

Use the verifier feedback to improve your proof.
"""

    prompt = load_prompt(
        prompts_dir,
        "decomposition-prover/step_prover.md",
        step_file=step_content,
        step_id=step["id"],
        inputs_file=inputs_content,
        problem_file=problem_file,
        related_work_file=related_work_file,
        round_number=round_number,
        previous_attempts_context=previous_attempts_context,
        output_file=os.path.join(state.get_revision_dir(), f"step_{step['id']}_proof.md"),
        output_dir=state.output_dir,
    )

    if decomp_logger:
        decomp_logger.log_agent_call(
            "step_prover", f"Proving {step['id']}",
            model_provider,
            {"round": round_number, "is_key": step.get("is_key_step", False)}
        )
        decomp_logger.update_status(
            state="PROVING_STEP",
            current_step=step["id"],
            current_round=round_number,
            recent_activity=f"Proving step {step['id']} (round {round_number})"
        )

    response = await run_model(
        provider=model_provider,
        prompt=prompt,
        working_dir=state.output_dir,
        config=config,
        claude_opts=get_claude_opts_for_model(config, model_provider) if model_provider == "claude" else claude_opts,
        tracker=tracker,
        call_name=f"step_prover_{step['id']}_r{round_number}",
    )

    # Save output log
    if decomp_logger:
        decomp_logger.save_agent_output(
            "step_prover",
            response,
            f"step_prover_{step['id']}_round{round_number}.md"
        )
        decomp_logger.log_agent_result("step_prover", f"Step {step['id']} proof attempt complete")

    state.save_step_proof(step["id"], response)
    return response


async def run_step_verifier(
    state: DecompositionState,
    step: dict,
    proof: str,
    inputs: list[dict],
    prompts_dir: str,
    config: dict,
    claude_opts: dict,
    decomp_logger: DecompositionLogger = None,
    tracker=None,
) -> tuple[str, str]:
    """Run the step verifier agent. Returns (verdict, full_response)."""

    model_provider = get_agent_model(config, "step_verifier")

    # Build step file content
    step_content = yaml.dump(step, default_flow_style=False)

    # Build inputs file content
    inputs_content = "# Input Statements\n\n"
    for inp in inputs:
        inputs_content += f"## {inp['id']}\n\n{inp.get('statement', '')}\n\n"

    prompt = load_prompt(
        prompts_dir,
        "decomposition-prover/step_verifier.md",
        step_file=step_content,
        step_id=step["id"],
        proof_file=proof,
        inputs_file=inputs_content,
        output_file=os.path.join(state.get_revision_dir(), f"step_{step['id']}_verify.md"),
        output_dir=state.output_dir,
    )

    if decomp_logger:
        decomp_logger.log_agent_call(
            "step_verifier", f"Verifying {step['id']}",
            model_provider,
            {}
        )
        decomp_logger.update_status(
            state="VERIFYING_STEP",
            current_step=step["id"],
            recent_activity=f"Verifying step {step['id']}"
        )

    response = await run_model(
        provider=model_provider,
        prompt=prompt,
        working_dir=state.output_dir,
        config=config,
        claude_opts=get_claude_opts_for_model(config, model_provider) if model_provider == "claude" else claude_opts,
        tracker=tracker,
        call_name=f"step_verifier_{step['id']}",
    )

    # Save output log
    if decomp_logger:
        decomp_logger.save_agent_output(
            "step_verifier",
            response,
            f"step_verifier_{step['id']}.md"
        )

    state.save_step_verification(step["id"], response)
    verdict = parse_verdict(response)

    if decomp_logger:
        decomp_logger.log_agent_result("step_verifier", f"Step {step['id']} verdict: {verdict}")

    return verdict, response


async def run_regulator(
    state: DecompositionState,
    step: dict,
    attempts_history: str,
    latest_verification: str,
    config: dict,
    prompts_dir: str,
    claude_opts: dict,
    rounds_used: int,
    decomp_logger: DecompositionLogger = None,
    tracker=None,
) -> str:
    """Run the regulator agent to decide next action. Returns decision string."""

    model_provider = get_agent_model(config, "regulator")
    decomp_config = config.get("decomposition", DEFAULT_CONFIG)

    # Build state file content
    state_content = f"""
attempt: {state.attempt}
revision: {state.revision}
step_id: {step['id']}
rounds_for_step: {rounds_used}
max_prover_rounds: {decomp_config.get('max_prover_rounds', 5)}
max_revisions: {decomp_config.get('max_revisions', 3)}
max_decompositions: {decomp_config.get('max_decompositions', 3)}
"""

    prompt = load_prompt(
        prompts_dir,
        "decomposition-prover/regulator.md",
        state_file=state_content,
        step_file=yaml.dump(step, default_flow_style=False),
        attempts_file=attempts_history,
        verification_file=latest_verification,
        max_prover_rounds=decomp_config.get('max_prover_rounds', 5),
        max_revisions=decomp_config.get('max_revisions', 3),
        max_decompositions=decomp_config.get('max_decompositions', 3),
        output_file=os.path.join(state.get_revision_dir(), "regulator_decision.md"),
    )

    if decomp_logger:
        decomp_logger.log_agent_call(
            "regulator", f"Evaluating {step['id']}",
            model_provider,
            {"rounds_used": rounds_used}
        )
        decomp_logger.update_status(
            state="REGULATING",
            current_step=step["id"],
            recent_activity=f"Regulator evaluating step {step['id']} after {rounds_used} rounds"
        )

    response = await run_model(
        provider=model_provider,
        prompt=prompt,
        working_dir=state.output_dir,
        config=config,
        claude_opts=get_claude_opts_for_model(config, model_provider) if model_provider == "claude" else claude_opts,
        tracker=tracker,
        call_name=f"regulator_{step['id']}",
    )

    decision = parse_regulator_decision(response)
    state.save_regulator_decision(step["id"], decision, response)

    # Save output log
    if decomp_logger:
        decomp_logger.save_agent_output(
            "regulator",
            response,
            f"regulator_{step['id']}_decision.md"
        )
        decomp_logger.log_agent_result("regulator", f"Decision for {step['id']}: {decision}")

    return decision


async def run_proof_aggregator(
    state: DecompositionState,
    problem_file: str,
    prompts_dir: str,
    config: dict,
    claude_opts: dict,
    output_file: str,
    decomp_logger: DecompositionLogger = None,
    tracker=None,
) -> str:
    """Run the proof aggregator to combine step proofs into final proof.md."""

    model_provider = get_agent_model(config, "proof_aggregator")

    prompt = load_prompt(
        prompts_dir,
        "decomposition-prover/proof_aggregator.md",
        decomposition_file=os.path.join(state.get_attempt_dir(), "decomposition.yaml"),
        step_proofs_dir=state.get_revision_dir(),
        problem_file=problem_file,
        output_file=output_file,
    )

    if decomp_logger:
        decomp_logger.log_agent_call(
            "proof_aggregator", "Assembling final proof",
            model_provider,
            {}
        )
        decomp_logger.update_status(
            state="AGGREGATING",
            recent_activity="Assembling final proof from step proofs"
        )

    response = await run_model(
        provider=model_provider,
        prompt=prompt,
        working_dir=state.output_dir,
        config=config,
        claude_opts=get_claude_opts_for_model(config, model_provider) if model_provider == "claude" else claude_opts,
        tracker=tracker,
        call_name="proof_aggregator",
    )

    # Extract proof from response (may be in markdown code block)
    proof = response
    if "# Proof" in proof:
        # Extract from the start of the proof section
        proof_start = proof.find("# Proof")
        if proof_start >= 0:
            proof = proof[proof_start:]

    write_file(output_file, proof)
    return proof


# ---------------------------------------------------------------------------
# Main orchestration
# ---------------------------------------------------------------------------

def get_step_inputs(decomposition: dict, step_id: str) -> list[dict]:
    """Get the input statements for a step."""
    inputs = []
    step = None
    for s in decomposition.get("steps", []):
        if s["id"] == step_id:
            step = s
            break

    if not step:
        return inputs

    input_ids = step.get("inputs", [])

    # Check sources
    for source in decomposition.get("sources", []):
        if source["id"] in input_ids:
            inputs.append(source)

    # Check other steps
    for s in decomposition.get("steps", []):
        if s["id"] in input_ids:
            inputs.append(s)

    return inputs


async def prove_step_with_retries(
    state: DecompositionState,
    step: dict,
    inputs: list[dict],
    problem_file: str,
    related_work_file: str,
    config: dict,
    prompts_dir: str,
    claude_opts: dict,
    decomp_logger: DecompositionLogger = None,
    tracker=None,
) -> tuple[bool, str]:
    """Prove a single step with retries and regulator intervention.

    Returns (success, decision) where decision is the final regulator decision
    if the step failed, or "PROVED" if successful.
    """
    decomp_config = config.get("decomposition", DEFAULT_CONFIG)
    max_rounds = decomp_config.get("max_prover_rounds", 5)

    attempts_history = ""
    verification = ""

    for round_num in range(1, max_rounds + 1):
        # Run step prover
        proof = await run_step_prover(
            state=state,
            step=step,
            inputs=inputs,
            problem_file=problem_file,
            related_work_file=related_work_file,
            prompts_dir=prompts_dir,
            config=config,
            claude_opts=claude_opts,
            round_number=round_num,
            previous_attempts=attempts_history,
            decomp_logger=decomp_logger,
            tracker=tracker,
        )

        # Run step verifier
        verdict, verification = await run_step_verifier(
            state=state,
            step=step,
            proof=proof,
            inputs=inputs,
            prompts_dir=prompts_dir,
            config=config,
            claude_opts=claude_opts,
            decomp_logger=decomp_logger,
            tracker=tracker,
        )

        if verdict == "PASS":
            state.mark_step_proved(step["id"])
            if decomp_logger:
                decomp_logger.update_status(
                    steps_proved=list(k for k, v in state.step_results.items() if v == "proved")
                )
            return True, "PROVED"

        # Add to attempts history for next round
        attempts_history += f"\n## Round {round_num}\n\n### Proof Attempt\n\n{proof[:2000]}...\n\n### Verification Result\n\n{verification[:1000]}...\n\n"

    # Step failed after max rounds - ask regulator
    decision = await run_regulator(
        state=state,
        step=step,
        attempts_history=attempts_history,
        latest_verification=verification,
        config=config,
        prompts_dir=prompts_dir,
        claude_opts=claude_opts,
        rounds_used=max_rounds,
        decomp_logger=decomp_logger,
        tracker=tracker,
    )

    state.mark_step_failed(step["id"])
    if decomp_logger:
        decomp_logger.update_status(
            steps_failed=list(k for k, v in state.step_results.items() if v == "failed")
        )
    return False, decision


async def run_decomposition_prover(
    problem_file: str,
    related_work_file: str,
    difficulty_file: str,
    output_dir: str,
    config: dict,
    prompts_dir: str,
    claude_opts: dict,
    logger=None,
    tracker=None,
) -> str:
    """Main entry point for decomposition-based proving.

    Returns the content of the final proof.md.
    """
    decomp_config = config.get("decomposition", DEFAULT_CONFIG)
    max_decompositions = decomp_config.get("max_decompositions", 3)
    max_revisions = decomp_config.get("max_revisions", 3)

    # Initialize state and logger
    state = DecompositionState(output_dir)
    os.makedirs(state.get_attempt_dir(), exist_ok=True)
    os.makedirs(state.get_revision_dir(), exist_ok=True)

    decomp_logger = DecompositionLogger(output_dir)
    decomp_logger.log("Starting decomposition-based proof")
    decomp_logger.update_status(
        state="INITIALIZING",
        attempt=1,
        revision=0,
        recent_activity="Initializing decomposition prover"
    )

    # Log model configuration
    models = decomp_config.get("models", DEFAULT_MODELS)
    decomp_logger.log(f"Model configuration: {models}")

    for decomp_attempt in range(1, max_decompositions + 1):
        if decomp_attempt > 1:
            state.new_attempt()
            decomp_logger.update_status(
                attempt=decomp_attempt,
                revision=0,
                steps_proved=[],
                steps_failed=[],
                recent_activity=f"Starting decomposition attempt {decomp_attempt}"
            )

        # Create initial decomposition
        mode = "CREATE" if decomp_attempt == 1 else "REWRITE"
        decomposition = await run_decomposer(
            state=state,
            problem_file=problem_file,
            related_work_file=related_work_file,
            difficulty_file=difficulty_file,
            prompts_dir=prompts_dir,
            config=config,
            claude_opts=claude_opts,
            mode=mode,
            decomp_logger=decomp_logger,
            tracker=tracker,
        )

        key_steps = decomposition.get("key_steps", [])
        total_steps = len(decomposition.get("steps", []))
        decomp_logger.log(f"Decomposition created: {total_steps} steps, {len(key_steps)} key steps")

        # Try to prove all steps
        all_proved = False
        revision_count = 0
        decision = ""

        while revision_count <= max_revisions:
            all_proved = True

            # Get proof order: key steps first, then others
            key_steps = decomposition.get("key_steps", [])
            proof_order = decomposition.get("proof_order", [])

            # Reorder to do key steps first
            ordered_steps = []
            for step_id in proof_order:
                if step_id in key_steps:
                    ordered_steps.insert(0, step_id)
                else:
                    ordered_steps.append(step_id)

            # Remove GOAL from proving (it's proved by combining steps)
            ordered_steps = [s for s in ordered_steps if s != "GOAL"]

            for step_id in ordered_steps:
                # Skip already proved steps
                if state.step_results.get(step_id) == "proved":
                    continue

                # Find the step
                step = None
                for s in decomposition.get("steps", []):
                    if s["id"] == step_id:
                        step = s
                        break

                if not step:
                    continue

                inputs = get_step_inputs(decomposition, step_id)

                success, decision = await prove_step_with_retries(
                    state=state,
                    step=step,
                    inputs=inputs,
                    problem_file=problem_file,
                    related_work_file=related_work_file,
                    config=config,
                    prompts_dir=prompts_dir,
                    claude_opts=claude_opts,
                    decomp_logger=decomp_logger,
                    tracker=tracker,
                )

                if not success:
                    all_proved = False

                    if decision == "REWRITE":
                        decomp_logger.log(f"Regulator requested rewrite")
                        break  # Exit step loop, trigger new decomposition

                    else:  # REVISE
                        decomp_logger.log(f"Regulator requested revision around step {step_id}")

                        # Get the last verification for feedback
                        verify_path = os.path.join(state.get_revision_dir(), f"step_{step_id}_verify.md")
                        feedback = read_file(verify_path)

                        state.new_revision()
                        decomp_logger.update_status(
                            revision=state.revision,
                            recent_activity=f"Revising decomposition around step {step_id}"
                        )

                        decomposition = await run_decomposer(
                            state=state,
                            problem_file=problem_file,
                            related_work_file=related_work_file,
                            difficulty_file=difficulty_file,
                            prompts_dir=prompts_dir,
                            config=config,
                            claude_opts=claude_opts,
                            mode="REVISE",
                            failed_step_id=step_id,
                            failure_feedback=feedback,
                            decomp_logger=decomp_logger,
                            tracker=tracker,
                        )
                        revision_count += 1
                        break  # Restart step proving with revised decomposition

            else:
                # All steps proved successfully
                break

            # Check if we need to break out for rewrite
            if decision == "REWRITE":
                break

        # Check if we exhausted revisions without success
        if not all_proved and decision != "REWRITE":
            decomp_logger.log(f"Max revisions ({max_revisions}) exhausted, triggering rewrite")
            decomp_logger.update_status(
                state="REWRITING",
                recent_activity=f"Max revisions exhausted, starting new decomposition attempt"
            )
            # Continue to next decomp_attempt (implicit REWRITE)

        if all_proved:
            decomp_logger.log("All steps proved! Aggregating final proof.")
            decomp_logger.update_status(
                state="AGGREGATING",
                recent_activity="All steps proved, aggregating final proof"
            )

            # Aggregate into final proof
            proof_file = os.path.join(output_dir, "proof.md")
            proof = await run_proof_aggregator(
                state=state,
                problem_file=problem_file,
                prompts_dir=prompts_dir,
                config=config,
                claude_opts=claude_opts,
                output_file=proof_file,
                decomp_logger=decomp_logger,
                tracker=tracker,
            )

            decomp_logger.log("Final proof aggregated successfully.")
            decomp_logger.update_status(
                state="COMPLETED",
                recent_activity="Proof completed successfully"
            )

            return proof

        decomp_logger.log(f"Attempt {decomp_attempt} failed, trying new decomposition")

    decomp_logger.log("All decomposition attempts exhausted")
    decomp_logger.update_status(
        state="FAILED",
        recent_activity="All decomposition attempts exhausted"
    )

    return ""


# ---------------------------------------------------------------------------
# Entry point for testing
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import sys

    print("Decomposition prover module loaded.")
    print("Use run_decomposition_prover() from pipeline.py to run.")
