#!/usr/bin/env python3
"""Decomposition-based prover for the QED pipeline.

This module implements a structured proof workflow that:
1. Decomposes a problem into a proof plan (intermediate steps)
2. Single prover writes a complete proof following the plan
3. Structural verification (Phases 1-5): problem integrity, completeness, citations, subgoal tree
4. Detailed verification (Phase 6): step-by-step logical analysis
5. Regulator decides next action on failure: REVISE_PROOF, REVISE_PLAN, or REWRITE

Pipeline flow:
    Decomposer → Single Prover → Structural Verifier → Detailed Verifier
         ↑                                                    ↓
         └──────────────────── Regulator ←────────────────────┘

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
    "max_proof_attempts": 3,     # REVISE_PROOF limit: proof attempts per revision
    "max_revisions": 2,          # REVISE_PLAN limit: plan revisions per attempt
    "max_decompositions": 3,     # REWRITE limit: total decomposition attempts
}

DEFAULT_MODELS = {
    "decomposer": "claude",
    "single_prover": "claude",
    "regulator": "claude",
    "structural_verifier": "claude",
    "detailed_verifier": "claude",
    "verdict": "claude",
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
    """Extract PASS or FAIL from verifier response.

    NOTE: This function is currently unused. The main orchestration loop uses
    run_verdict() instead, which returns DONE/CONTINUE. Kept for potential
    future use or alternative parsing needs.
    """
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
    """Logger for decomposition prover that writes structured logs."""

    def __init__(self, output_dir: str):
        self.output_dir = output_dir
        self.decomp_dir = os.path.join(output_dir, "decomposition")
        os.makedirs(self.decomp_dir, exist_ok=True)

        # Main status and timeline log (top-level, not per-attempt)
        self.status_file = os.path.join(self.decomp_dir, "STATUS.md")
        self.main_log_file = os.path.join(self.decomp_dir, "log.txt")

        # Initialize status
        self._write_status({
            "state": "STARTING",
            "attempt": 1,
            "revision": 1,
            "proof": 1,
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
| Attempt | {status.get('attempt', 1)} |
| Revision | {status.get('revision', 1)} |
| Proof | {status.get('proof', 1)} |

## Recent Activity

{status.get('recent_activity', '')}
"""
        write_file(self.status_file, content)

    def update_status(
        self,
        state: str = None,
        attempt: int = None,
        revision: int = None,
        proof: int = None,
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
                "revision": 1,
                "proof": 1,
                "recent_activity": "",
            }

        # Update with new values
        if state is not None:
            existing["state"] = state
        if attempt is not None:
            existing["attempt"] = attempt
        if revision is not None:
            existing["revision"] = revision
        if proof is not None:
            existing["proof"] = proof
        if recent_activity is not None:
            existing["recent_activity"] = recent_activity

        existing["last_update"] = datetime.now().isoformat()
        self._write_status(existing)

    def log(self, message: str, agent: str = None) -> None:
        """Log a message to the main timeline log."""
        timestamp = self._timestamp()
        log_line = f"[{timestamp}] {message}\n"

        # Write to main log
        with open(self.main_log_file, "a") as f:
            f.write(log_line)

        # Print to console
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
        self.log(message)

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
        self.log(" | ".join(parts))

    def save_agent_output(
        self,
        output: str,
        path: str,
    ) -> None:
        """Save raw LLM response to a structured path."""
        write_file(path, output)


def parse_regulator_decision(response: str) -> str:
    """Extract decision from regulator response.

    Returns one of: REVISE_PROOF, REVISE_PLAN, REWRITE
    """
    response_upper = response.upper()
    # Check for new 3-option decisions first
    for decision in ["REVISE_PROOF", "REVISE_PLAN", "REWRITE"]:
        if f"DECISION: {decision}" in response_upper:
            return decision
        if f"## DECISION: {decision}" in response_upper:
            return decision
    # Legacy support: map old REVISE to REVISE_PROOF
    if "DECISION: REVISE" in response_upper or "## DECISION: REVISE" in response_upper:
        return "REVISE_PROOF"
    # Default to REVISE_PROOF if unclear (cheapest retry option)
    return "REVISE_PROOF"


# ---------------------------------------------------------------------------
# Decomposition state management
# ---------------------------------------------------------------------------

class DecompositionState:
    """Tracks the state of a decomposition-based proof attempt.

    Three-level hierarchy:
    - attempt: Complete decomposition strategy (REWRITE creates new attempt)
    - revision: Version of the decomposition within an attempt (REVISE_PLAN creates new revision)
    - proof: Proof attempt for a given decomposition (REVISE_PROOF creates new proof)

    Directory structure:
        attempt_1/
          revision_1/
            decomposition.yaml
            proof_1/
              proof.md, verification reports, regulator_decision.md
            proof_2/
              ...
          revision_2/
            decomposition.yaml  (revised plan)
            proof_1/
              ...
        attempt_2/  (new strategy)
          ...
    """

    def __init__(self, output_dir: str):
        self.output_dir = output_dir
        self.decomp_dir = os.path.join(output_dir, "decomposition")
        self.attempt = 1
        self.revision = 1
        self.proof = 1
        self.decomposition = None
        self.attempt_history = []  # list of failure info for rewrites

    def get_attempt_dir(self) -> str:
        """Get directory for current decomposition attempt."""
        return os.path.join(self.decomp_dir, f"attempt_{self.attempt}")

    def get_revision_dir(self) -> str:
        """Get directory for current revision within attempt."""
        return os.path.join(self.get_attempt_dir(), f"revision_{self.revision}")

    def get_proof_dir(self) -> str:
        """Get directory for current proof attempt."""
        return os.path.join(self.get_revision_dir(), f"proof_{self.proof}")

    def save_decomposition(self, decomposition: dict) -> None:
        """Save decomposition to the current revision directory.

        Each revision has one decomposition.yaml that applies to all proofs within it.
        """
        self.decomposition = decomposition
        revision_dir = self.get_revision_dir()
        os.makedirs(revision_dir, exist_ok=True)
        path = os.path.join(revision_dir, "decomposition.yaml")
        write_file(path, yaml.dump(decomposition, default_flow_style=False))

    def load_decomposition(self) -> dict | None:
        """Load decomposition from the current revision directory."""
        path = os.path.join(self.get_revision_dir(), "decomposition.yaml")
        content = read_file(path)
        if content:
            self.decomposition = yaml.safe_load(content)
            return self.decomposition
        return None

    def save_proof(self, proof: str) -> None:
        """Save proof to the current proof attempt directory."""
        proof_dir = self.get_proof_dir()
        os.makedirs(proof_dir, exist_ok=True)
        write_file(os.path.join(proof_dir, "proof.md"), proof)
        # Also save to top-level output for final result
        write_file(os.path.join(self.output_dir, "proof.md"), proof)

    def save_regulator_decision(self, decision: str, response: str) -> None:
        """Save regulator decision."""
        path = os.path.join(self.get_proof_dir(), "regulator_decision.md")
        write_file(path, response)

    def new_proof(self) -> None:
        """Start a new proof attempt with the same decomposition (REVISE_PROOF)."""
        self.proof += 1
        os.makedirs(self.get_proof_dir(), exist_ok=True)

    def new_revision(self) -> None:
        """Start a new revision of the decomposition (REVISE_PLAN)."""
        self.revision += 1
        self.proof = 1
        os.makedirs(self.get_revision_dir(), exist_ok=True)
        os.makedirs(self.get_proof_dir(), exist_ok=True)

    def new_attempt(self) -> None:
        """Start a completely new decomposition attempt (REWRITE)."""
        # Save failure history
        if self.decomposition:
            self.attempt_history.append({
                "attempt": self.attempt,
                "revisions": self.revision,
                "proofs": self.proof,
                "decomposition": self.decomposition,
            })
        # Reset for new attempt
        self.attempt += 1
        self.revision = 1
        self.proof = 1
        self.decomposition = None
        os.makedirs(self.get_attempt_dir(), exist_ok=True)
        os.makedirs(self.get_revision_dir(), exist_ok=True)
        os.makedirs(self.get_proof_dir(), exist_ok=True)

    def get_failure_history(self) -> str:
        """Get formatted failure history for rewrite mode."""
        if not self.attempt_history:
            return "No previous attempts."

        lines = ["# Previous Attempt Failures\n"]
        for hist in self.attempt_history:
            lines.append(f"## Attempt {hist['attempt']}\n")
            lines.append(f"Revisions tried: {hist['revisions']}\n")
            lines.append(f"Proofs in last revision: {hist['proofs']}\n")
            lines.append("\n")
        return "".join(lines)

    def get_revision_summary(self) -> str:
        """Get summary of proof attempts in current revision for regulator."""
        lines = []
        for i in range(1, self.proof):
            proof_dir = os.path.join(self.get_revision_dir(), f"proof_{i}")
            reg_decision = read_file(os.path.join(proof_dir, "regulator_decision.md"))
            if reg_decision:
                lines.append(f"## Proof Attempt {i}\n")
                lines.append(f"Regulator decision:\n{reg_decision[:500]}...\n\n")
        return "".join(lines) if lines else "This is the first proof attempt for this revision."

    def get_full_attempt_history(self) -> str:
        """Get comprehensive history of all attempts for FINAL mode failure analysis."""
        lines = ["# Complete Attempt History\n\n"]

        # Include saved history from previous attempts
        for hist in self.attempt_history:
            lines.append(f"## Attempt {hist['attempt']}\n")
            lines.append(f"- Total revisions: {hist['revisions']}\n")
            lines.append(f"- Proofs in last revision: {hist['proofs']}\n\n")

        # Include current attempt details
        lines.append(f"## Attempt {self.attempt} (current)\n")
        lines.append(f"- Total revisions: {self.revision}\n")
        lines.append(f"- Proofs in current revision: {self.proof}\n\n")

        # Scan all proof directories for verification reports and decisions
        lines.append("### Detailed Proof History\n\n")
        for a in range(1, self.attempt + 1):
            attempt_dir = os.path.join(self.decomp_dir, f"attempt_{a}")
            if not os.path.isdir(attempt_dir):
                continue
            max_rev = self.revision if a == self.attempt else _find_max_numbered_dir(attempt_dir, "revision_")
            for r in range(1, max_rev + 1):
                revision_dir = os.path.join(attempt_dir, f"revision_{r}")
                if not os.path.isdir(revision_dir):
                    continue
                max_proof = self.proof if (a == self.attempt and r == self.revision) else _find_max_numbered_dir(revision_dir, "proof_")
                for p in range(1, max_proof + 1):
                    proof_dir = os.path.join(revision_dir, f"proof_{p}")
                    lines.append(f"#### Attempt {a}, Revision {r}, Proof {p}\n")

                    # Check verification results
                    structural = read_file(os.path.join(proof_dir, "structural_verification.md"))
                    detailed = read_file(os.path.join(proof_dir, "detailed_verification.md"))
                    decision = read_file(os.path.join(proof_dir, "regulator_decision.md"))

                    if structural:
                        # Extract verdict summary (first few lines)
                        verdict_lines = structural.split('\n')[:5]
                        lines.append(f"Structural verification:\n```\n{chr(10).join(verdict_lines)}...\n```\n")
                    if detailed:
                        verdict_lines = detailed.split('\n')[:5]
                        lines.append(f"Detailed verification:\n```\n{chr(10).join(verdict_lines)}...\n```\n")
                    if decision:
                        lines.append(f"Regulator decision:\n```\n{decision[:300]}...\n```\n")
                    lines.append("\n")

        return "".join(lines)


# ---------------------------------------------------------------------------
# Resume detection
# ---------------------------------------------------------------------------

def _file_nonempty(path: str) -> bool:
    """Check if a file exists and is non-empty."""
    return os.path.isfile(path) and os.path.getsize(path) > 0


def _find_max_numbered_dir(parent_dir: str, prefix: str) -> int:
    """Find the highest numbered subdirectory with given prefix."""
    if not os.path.isdir(parent_dir):
        return 0
    nums = []
    for name in os.listdir(parent_dir):
        if name.startswith(prefix):
            try:
                nums.append(int(name.split("_", 1)[1]))
            except (ValueError, IndexError):
                continue
    return max(nums) if nums else 0


def detect_decomposition_resume(output_dir: str) -> dict:
    """Scan the output directory for decomposition progress from a previous run.

    Three-level hierarchy:
    - attempt_N/           (REWRITE creates new attempt)
      - revision_M/        (REVISE_PLAN creates new revision)
        - decomposition.yaml
        - proof_K/         (REVISE_PROOF creates new proof)
          - proof.md
          - structural_verification.md
          - detailed_verification.md
          - regulator_decision.md

    Returns a dict with resume information:
        {
            "has_progress": bool,
            "attempt": int,
            "revision": int,
            "proof": int,
            "decomposition": dict|None,
            "resume_point": str,        # "fresh", "decompose", "prove",
                                        # "verify_structural", "verify_detailed",
                                        # "regulator", "done"
            "attempt_history": list,
        }
    """
    decomp_dir = os.path.join(output_dir, "decomposition")
    result = {
        "has_progress": False,
        "attempt": 1,
        "revision": 1,
        "proof": 1,
        "decomposition": None,
        "resume_point": "fresh",
        "attempt_history": [],
    }

    if not os.path.isdir(decomp_dir):
        return result

    # Find the highest attempt directory
    latest_attempt = _find_max_numbered_dir(decomp_dir, "attempt_")
    if latest_attempt == 0:
        return result

    # Build attempt history from previous (failed) attempts
    attempt_history = []
    for att_num in range(1, latest_attempt):
        att_dir = os.path.join(decomp_dir, f"attempt_{att_num}")
        last_rev = _find_max_numbered_dir(att_dir, "revision_")
        if last_rev > 0:
            rev_dir = os.path.join(att_dir, f"revision_{last_rev}")
            decomp_path = os.path.join(rev_dir, "decomposition.yaml")
            decomp_content = read_file(decomp_path)
            if decomp_content:
                last_proof = _find_max_numbered_dir(rev_dir, "proof_")
                try:
                    decomp = yaml.safe_load(decomp_content)
                except yaml.YAMLError:
                    decomp = None  # Skip invalid YAML in history
                attempt_history.append({
                    "attempt": att_num,
                    "revisions": last_rev,
                    "proofs": last_proof,
                    "decomposition": decomp,
                })

    result["attempt_history"] = attempt_history
    result["attempt"] = latest_attempt

    # Analyze the latest attempt
    att_dir = os.path.join(decomp_dir, f"attempt_{latest_attempt}")
    latest_revision = _find_max_numbered_dir(att_dir, "revision_")

    if latest_revision == 0:
        # No revision directories yet - need to start fresh decomposition
        result["has_progress"] = len(attempt_history) > 0
        result["resume_point"] = "decompose"
        return result

    result["revision"] = latest_revision
    rev_dir = os.path.join(att_dir, f"revision_{latest_revision}")

    # Check if decomposition exists in this revision
    decomp_path = os.path.join(rev_dir, "decomposition.yaml")
    decomp_content = read_file(decomp_path)
    if not decomp_content:
        # Revision directory exists but no decomposition - need decomposition
        result["has_progress"] = len(attempt_history) > 0 or latest_revision > 1
        # If revision > 1, we're in the middle of a REVISE operation
        # If revision == 1, we're starting fresh (CREATE or REWRITE)
        if latest_revision > 1:
            result["resume_point"] = "decompose_revise"
        else:
            result["resume_point"] = "decompose"
        return result

    try:
        decomposition = yaml.safe_load(decomp_content)
    except yaml.YAMLError:
        # Invalid YAML in current decomposition - need to re-decompose
        result["has_progress"] = len(attempt_history) > 0 or latest_revision > 1
        result["resume_point"] = "decompose" if latest_revision == 1 else "decompose_revise"
        return result
    result["decomposition"] = decomposition
    result["has_progress"] = True

    # Find the latest proof attempt
    latest_proof = _find_max_numbered_dir(rev_dir, "proof_")
    if latest_proof == 0:
        # No proof directories yet - need to start proving
        result["resume_point"] = "prove"
        return result

    result["proof"] = latest_proof
    proof_dir = os.path.join(rev_dir, f"proof_{latest_proof}")

    # Check verification status
    detailed_path = os.path.join(proof_dir, "detailed_verification.md")
    structural_path = os.path.join(proof_dir, "structural_verification.md")
    proof_path = os.path.join(proof_dir, "proof.md")
    regulator_path = os.path.join(proof_dir, "regulator_decision.md")

    # Check if detailed verification exists and passed
    if _file_nonempty(detailed_path):
        content = read_file(detailed_path)
        if "OVERALL VERDICT: PASS" in content.upper():
            result["resume_point"] = "done"
            return result
        # Detailed failed - check if regulator was called
        if _file_nonempty(regulator_path):
            # Regulator was called, need to start new proof attempt
            result["proof"] = latest_proof + 1
            result["resume_point"] = "prove"
            return result
        # No regulator decision yet - need to call regulator
        result["resume_point"] = "regulator"
        return result

    # Check if structural verification exists
    if _file_nonempty(structural_path):
        content = read_file(structural_path)
        if "OVERALL VERDICT: PASS" in content.upper():
            # Structural passed, need detailed
            result["resume_point"] = "verify_detailed"
            return result
        # Structural failed - check if regulator was called
        if _file_nonempty(regulator_path):
            result["proof"] = latest_proof + 1
            result["resume_point"] = "prove"
            return result
        result["resume_point"] = "regulator"
        return result

    # Check if proof exists
    if _file_nonempty(proof_path):
        result["resume_point"] = "verify_structural"
        return result

    # Have decomposition but no proof yet
    result["resume_point"] = "prove"
    return result


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
    verification_feedback: str = "",
    regulator_guidance: str = "",
    decomp_logger: DecompositionLogger = None,
    tracker=None,
) -> dict:
    """Run the decomposer agent to create/revise/rewrite a decomposition.

    Args:
        mode: "CREATE" for fresh decomposition, "REVISE" to modify plan based on
              verification feedback, "REWRITE" for completely new strategy.
        verification_feedback: Combined verification report (for REVISE mode).
        regulator_guidance: Regulator's suggestions for the decomposer (for REVISE/REWRITE).
    """

    model_provider = get_agent_model(config, "decomposer")

    # Build revision context based on mode
    revision_context = ""
    if mode == "REVISE":
        # Get the previous revision's decomposition and last proof
        prev_rev_num = state.revision - 1
        prev_rev_dir = os.path.join(state.get_attempt_dir(), f"revision_{prev_rev_num}")
        prev_decomp_file = os.path.join(prev_rev_dir, "decomposition.yaml")
        # Find the last proof in the previous revision
        last_proof = _find_max_numbered_dir(prev_rev_dir, "proof_")
        prev_proof_file = os.path.join(prev_rev_dir, f"proof_{last_proof}", "proof.md") if last_proof > 0 else ""

        revision_context = f"""
### Current Decomposition (to revise)
```
{prev_decomp_file}
```

### Verification Feedback
{verification_feedback}

### Regulator Guidance
{regulator_guidance}

### Previous Proof Attempt
```
{prev_proof_file}
```
"""
    elif mode == "REWRITE":
        revision_context = f"""
### Failure History
{state.get_failure_history()}

### Regulator Guidance
{regulator_guidance}

### Previous Decomposition Attempts
See {state.decomp_dir}/attempt_*/revision_*/decomposition.yaml
"""

    # Resolve human help file from output_dir (run.sh copies the global files
    # there, and the UI edits them in-place).
    human_help_file = os.path.join(state.output_dir, "human_help", "additional_prove_human_help_global.md")

    # Ensure revision directory exists for output
    revision_dir = state.get_revision_dir()
    os.makedirs(revision_dir, exist_ok=True)
    decomposition_output_file = os.path.join(revision_dir, "decomposition.yaml")

    # For REVISE mode, point to previous revision's decomposition
    prev_rev_num = state.revision - 1 if mode == "REVISE" else state.revision
    prev_decomp_file = os.path.join(state.get_attempt_dir(), f"revision_{prev_rev_num}", "decomposition.yaml")

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
        output_file=decomposition_output_file,
        current_decomposition_file=prev_decomp_file if mode == "REVISE" else decomposition_output_file,
        verification_feedback=verification_feedback,
        regulator_guidance=regulator_guidance,
        previous_proof_file=prev_proof_file if mode == "REVISE" else "",
        failure_history_file=os.path.join(state.decomp_dir, "failure_history.md"),
        human_help_file=human_help_file,
    )

    if decomp_logger:
        decomp_logger.log_agent_call(
            "decomposer", f"{mode} mode",
            model_provider,
            {"attempt": state.attempt, "revision": state.revision, "proof": state.proof}
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

    # Save LLM response into structured path (in revision directory)
    if decomp_logger:
        resp_path = os.path.join(revision_dir, "decomposer_response.md")
        decomp_logger.save_agent_output(response, resp_path)
        decomp_logger.log_agent_result("decomposer", f"{mode} completed")

    # Read the file the agent wrote via tool call
    try:
        decomposition = state.load_decomposition()
        if decomposition:
            return decomposition
    except yaml.YAMLError as e:
        # Agent wrote invalid YAML to file - will try parsing response instead
        if decomp_logger:
            decomp_logger.log(f"Warning: Decomposition file has invalid YAML: {e}")

    # Agent failed to write the file or wrote invalid YAML — fall back to parsing response text
    try:
        decomposition = parse_decomposition(response)
    except (yaml.YAMLError, AttributeError) as e:
        raise RuntimeError(
            f"Decomposer did not produce valid YAML and did not write to {decomposition_output_file}.\n"
            f"Parse error: {e}\nResponse preview: {response[:500]}"
        ) from e

    state.save_decomposition(decomposition)
    return decomposition


async def run_single_prover(
    state: DecompositionState,
    problem_file: str,
    related_work_file: str,
    prompts_dir: str,
    config: dict,
    claude_opts: dict,
    decomp_logger: DecompositionLogger = None,
    tracker=None,
) -> str:
    """Run the single prover agent to write a complete proof following the decomposition plan."""

    model_provider = get_agent_model(config, "single_prover")

    # Resolve human help file from output_dir (run.sh copies the global files
    # there, and the UI edits them in-place).
    human_help_file = os.path.join(state.output_dir, "human_help", "additional_prove_human_help_global.md")

    # Build the output path
    proof_dir = state.get_proof_dir()
    os.makedirs(proof_dir, exist_ok=True)
    output_file = os.path.join(proof_dir, "proof.md")

    # Decomposition is in the revision directory
    decomposition_file = os.path.join(state.get_revision_dir(), "decomposition.yaml")

    # Previous proof context: only for REVISE_PROOF (same revision, proof > 1)
    # For new revision or new attempt, these are empty
    previous_proof_file = ""
    previous_verification_file = ""
    if state.proof > 1:
        prev_proof_dir = os.path.join(state.get_revision_dir(), f"proof_{state.proof - 1}")
        previous_proof_file = os.path.join(prev_proof_dir, "proof.md")
        # Use structural if detailed doesn't exist, otherwise use detailed
        detailed_path = os.path.join(prev_proof_dir, "detailed_verification.md")
        structural_path = os.path.join(prev_proof_dir, "structural_verification.md")
        if os.path.exists(detailed_path):
            previous_verification_file = detailed_path
        elif os.path.exists(structural_path):
            previous_verification_file = structural_path

    scratchpad_file = os.path.join(proof_dir, "scratchpad.md")

    prompt = load_prompt(
        prompts_dir,
        "decomposition-prover/single_prover.md",
        problem_file=problem_file,
        related_work_file=related_work_file,
        decomposition_file=decomposition_file,
        human_help_file=human_help_file,
        previous_proof_file=previous_proof_file,
        previous_verification_file=previous_verification_file,
        output_file=output_file,
        output_dir=state.output_dir,
        scratchpad_file=scratchpad_file,
    )

    if decomp_logger:
        decomp_logger.log_agent_call(
            "single_prover", "Writing complete proof",
            model_provider,
            {"attempt": state.attempt, "revision": state.revision, "proof": state.proof}
        )
        decomp_logger.update_status(
            state="PROVING",
            proof=state.proof,
            recent_activity=f"Writing proof (attempt {state.attempt}, revision {state.revision}, proof {state.proof})"
        )

    response = await run_model(
        provider=model_provider,
        prompt=prompt,
        working_dir=state.output_dir,
        config=config,
        claude_opts=get_claude_opts_for_model(config, model_provider) if model_provider == "claude" else claude_opts,
        tracker=tracker,
        call_name=f"single_prover_a{state.attempt}_r{state.revision}_p{state.proof}",
    )

    # Save LLM response into structured path
    if decomp_logger:
        resp_path = os.path.join(proof_dir, "prover_response.md")
        decomp_logger.save_agent_output(response, resp_path)
        decomp_logger.log_agent_result("single_prover", "Proof written")

    # Read the file the agent wrote via tool call
    content = read_file(output_file)
    if not content:
        # Agent failed to write the file — fall back to response text
        content = response
        if "# Proof" in content:
            proof_start = content.find("# Proof")
            if proof_start >= 0:
                content = content[proof_start:]

    # Save the proof
    state.save_proof(content)
    return content


async def run_regulator(
    state: DecompositionState,
    verification_report: str,
    proof_file: str,
    config: dict,
    prompts_dir: str,
    claude_opts: dict,
    decomp_logger: DecompositionLogger = None,
    tracker=None,
    mode: str = "DECIDE",
) -> str:
    """Run the regulator agent to decide next action after verification failure.

    Args:
        mode: "DECIDE" for normal decision, "FINAL" for failure analysis when all limits exhausted

    Returns:
        In DECIDE mode: one of REVISE_PROOF, REVISE_PLAN, REWRITE
        In FINAL mode: "FAILED" (failure analysis is written to file)
    """

    model_provider = get_agent_model(config, "regulator")
    decomp_config = config.get("decomposition", DEFAULT_CONFIG)

    # Build state file content
    state_content = f"""
attempt: {state.attempt}
revision: {state.revision}
proof: {state.proof}
max_proof_attempts: {decomp_config.get('max_proof_attempts', 3)}
max_revisions: {decomp_config.get('max_revisions', 2)}
max_decompositions: {decomp_config.get('max_decompositions', 3)}
"""

    # Get attempt history (all attempts for FINAL mode, current revision for DECIDE mode)
    if mode == "FINAL":
        attempt_history = state.get_full_attempt_history()
        output_file = os.path.join(state.decomp_dir, "failure_analysis.md")
    else:
        attempt_history = state.get_revision_summary()
        output_file = os.path.join(state.get_proof_dir(), "regulator_decision.md")

    prompt = load_prompt(
        prompts_dir,
        "decomposition-prover/regulator.md",
        mode=mode,
        state_file=state_content,
        decomposition_file=os.path.join(state.get_revision_dir(), "decomposition.yaml"),
        proof_file=proof_file,
        verification_report=verification_report,
        attempt_history=attempt_history,
        max_proof_attempts=decomp_config.get('max_proof_attempts', 3),
        max_revisions=decomp_config.get('max_revisions', 2),
        max_decompositions=decomp_config.get('max_decompositions', 3),
        output_file=output_file,
    )

    if decomp_logger:
        if mode == "FINAL":
            decomp_logger.log_agent_call(
                "regulator", "Writing failure analysis",
                model_provider,
                {"mode": "FINAL", "attempt": state.attempt, "revision": state.revision, "proof": state.proof}
            )
            decomp_logger.update_status(
                state="ANALYZING_FAILURE",
                recent_activity="Regulator writing failure analysis (all limits exhausted)"
            )
        else:
            decomp_logger.log_agent_call(
                "regulator", "Evaluating verification failure",
                model_provider,
                {"attempt": state.attempt, "revision": state.revision, "proof": state.proof}
            )
            decomp_logger.update_status(
                state="REGULATING",
                recent_activity=f"Regulator evaluating after proof {state.proof} failed verification"
            )

    response = await run_model(
        provider=model_provider,
        prompt=prompt,
        working_dir=state.output_dir,
        config=config,
        claude_opts=get_claude_opts_for_model(config, model_provider) if model_provider == "claude" else claude_opts,
        tracker=tracker,
        call_name=f"regulator_{mode.lower()}_a{state.attempt}_r{state.revision}_p{state.proof}",
    )

    # Check if the agent wrote the file via tool call
    content = read_file(output_file)
    if not content:
        content = response
        # Save content to expected location
        write_file(output_file, content)

    if mode == "FINAL":
        if decomp_logger:
            decomp_logger.log_agent_result("regulator", "Failure analysis complete")
        return "FAILED"

    decision = parse_regulator_decision(content)
    state.save_regulator_decision(decision, content)

    if decomp_logger:
        decomp_logger.log_agent_result("regulator", f"Decision: {decision}")

    return decision


# ---------------------------------------------------------------------------
# Proof verification
# ---------------------------------------------------------------------------

async def run_structural_verification(
    state: DecompositionState,
    problem_file: str,
    proof_file: str,
    prompts_dir: str,
    config: dict,
    claude_opts: dict,
    decomp_logger: DecompositionLogger = None,
    tracker=None,
) -> str:
    """Run structural verification on the proof.

    Returns the path to the verification report file.
    The verdict is determined separately by run_verdict().
    """
    model_provider = get_agent_model(config, "structural_verifier")

    # Use proof directory for verification outputs
    proof_dir = state.get_proof_dir()
    os.makedirs(proof_dir, exist_ok=True)

    output_file = os.path.join(proof_dir, "structural_verification.md")
    error_file = os.path.join(proof_dir, "error_structural_verification.md")
    decomposition_file = os.path.join(state.get_revision_dir(), "decomposition.yaml")

    # Global verification rules from output_dir (run.sh copies the global files
    # there, and the UI edits them in-place).
    additional_verify_rule_global_file = os.path.join(
        state.output_dir, "human_help", "additional_verify_rule_global.md"
    )

    prompt = load_prompt(
        prompts_dir,
        "decomposition-prover/proof_verify_structural.md",
        problem_file=problem_file,
        proof_file=proof_file,
        decomposition_file=decomposition_file,
        output_file=output_file,
        error_file=error_file,
        output_dir=state.output_dir,
        additional_verify_rule_global_file=additional_verify_rule_global_file,
    )

    if decomp_logger:
        decomp_logger.log_agent_call(
            "structural_verifier", "Structural verification of aggregated proof",
            model_provider,
            {}
        )
        decomp_logger.update_status(
            state="VERIFYING_PROOF_STRUCTURAL",
            recent_activity="Running structural verification on aggregated proof"
        )

    await run_model(
        provider=model_provider,
        prompt=prompt,
        working_dir=state.output_dir,
        config=config,
        claude_opts=get_claude_opts_for_model(config, model_provider) if model_provider == "claude" else claude_opts,
        tracker=tracker,
        call_name="proof_verify_structural",
    )

    if decomp_logger:
        decomp_logger.log_agent_result("structural_verifier", "Structural verification report generated")

    return output_file


async def run_detailed_verification(
    state: DecompositionState,
    problem_file: str,
    proof_file: str,
    structural_report_file: str,
    prompts_dir: str,
    config: dict,
    claude_opts: dict,
    decomp_logger: DecompositionLogger = None,
    tracker=None,
) -> str:
    """Run detailed verification on the proof.

    Returns the path to the verification report file.
    The verdict is determined separately by run_verdict().
    """
    model_provider = get_agent_model(config, "detailed_verifier")

    # Use proof directory for verification outputs
    proof_dir = state.get_proof_dir()
    os.makedirs(proof_dir, exist_ok=True)

    output_file = os.path.join(proof_dir, "detailed_verification.md")
    error_file = os.path.join(proof_dir, "error_detailed_verification.md")
    decomposition_file = os.path.join(state.get_revision_dir(), "decomposition.yaml")

    prompt = load_prompt(
        prompts_dir,
        "decomposition-prover/proof_verify_detailed.md",
        problem_file=problem_file,
        proof_file=proof_file,
        structural_report_file=structural_report_file,
        decomposition_file=decomposition_file,
        output_file=output_file,
        error_file=error_file,
        output_dir=state.output_dir,
    )

    if decomp_logger:
        decomp_logger.log_agent_call(
            "detailed_verifier", "Detailed verification of aggregated proof",
            model_provider,
            {}
        )
        decomp_logger.update_status(
            state="VERIFYING_PROOF_DETAILED",
            recent_activity="Running detailed verification on aggregated proof"
        )

    await run_model(
        provider=model_provider,
        prompt=prompt,
        working_dir=state.output_dir,
        config=config,
        claude_opts=get_claude_opts_for_model(config, model_provider) if model_provider == "claude" else claude_opts,
        tracker=tracker,
        call_name="proof_verify_detailed",
    )

    if decomp_logger:
        decomp_logger.log_agent_result("detailed_verifier", "Detailed verification report generated")

    return output_file


async def run_verdict(
    state: DecompositionState,
    structural_report_file: str,
    prompts_dir: str,
    config: dict,
    claude_opts: dict,
    mode: str = "FINAL",
    detailed_report_file: str = "",
    decomp_logger: DecompositionLogger = None,
    tracker=None,
) -> str:
    """Run verdict check on verification reports.

    Args:
        mode: "STRUCTURAL" to check structural only, "FINAL" to check both
        structural_report_file: Path to structural verification report
        detailed_report_file: Path to detailed verification report (only for FINAL mode)

    Returns "DONE" if verification passes, "CONTINUE" otherwise.
    """
    model_provider = get_agent_model(config, "verdict")

    prompt = load_prompt(
        prompts_dir,
        "decomposition-prover/verdict_proof.md",
        mode=mode,
        structural_verification_file=structural_report_file,
        detailed_verification_file=detailed_report_file if mode == "FINAL" else "",
    )

    if decomp_logger:
        if mode == "STRUCTURAL":
            decomp_logger.log_agent_call(
                "verdict", "Verdict on structural verification",
                model_provider,
                {"mode": mode}
            )
            decomp_logger.update_status(
                state="VERDICT_STRUCTURAL",
                recent_activity="Running structural verdict check"
            )
        else:
            decomp_logger.log_agent_call(
                "verdict", "Final verdict on verification results",
                model_provider,
                {"mode": mode}
            )
            decomp_logger.update_status(
                state="VERDICT_FINAL",
                recent_activity="Running final verdict check"
            )

    response = await run_model(
        provider=model_provider,
        prompt=prompt,
        working_dir=state.output_dir,
        config=config,
        claude_opts=get_claude_opts_for_model(config, model_provider) if model_provider == "claude" else claude_opts,
        tracker=tracker,
        call_name=f"verdict_{mode.lower()}",
    )

    # Parse verdict from response
    response_upper = response.strip().upper()
    if "DONE" in response_upper:
        verdict = "DONE"
    else:
        verdict = "CONTINUE"

    if decomp_logger:
        decomp_logger.log_agent_result("verdict", f"{mode} verdict: {verdict}")

    return verdict


async def run_proof_verification(
    state: DecompositionState,
    problem_file: str,
    proof_file: str,
    prompts_dir: str,
    config: dict,
    claude_opts: dict,
    decomp_logger: DecompositionLogger = None,
    tracker=None,
) -> tuple[str, str]:
    """Run full verification (structural + detailed) on the aggregated proof.

    NOTE: This function is currently unused. The main orchestration loop calls
    run_structural_verification(), run_verdict(), run_detailed_verification(),
    and run_verdict() directly for finer control over the flow. Kept as a
    convenience wrapper for potential future use or testing.

    Returns (final_verdict, combined_feedback) where final_verdict is "DONE" or "CONTINUE"
    and combined_feedback is the content of the verification reports for use by the
    decomposer on the next round.
    """
    # Step 1: Structural verification
    structural_report_file = await run_structural_verification(
        state=state,
        problem_file=problem_file,
        proof_file=proof_file,
        prompts_dir=prompts_dir,
        config=config,
        claude_opts=claude_opts,
        decomp_logger=decomp_logger,
        tracker=tracker,
    )

    # Step 2: Structural verdict
    structural_verdict = await run_verdict(
        state=state,
        structural_report_file=structural_report_file,
        prompts_dir=prompts_dir,
        config=config,
        claude_opts=claude_opts,
        mode="STRUCTURAL",
        decomp_logger=decomp_logger,
        tracker=tracker,
    )

    if structural_verdict == "CONTINUE":
        feedback = read_file(structural_report_file)
        if decomp_logger:
            decomp_logger.log("Structural verdict: CONTINUE — skipping detailed verification")
        return "CONTINUE", feedback

    # Step 3: Detailed verification (only if structural passed)
    detailed_report_file = await run_detailed_verification(
        state=state,
        problem_file=problem_file,
        proof_file=proof_file,
        structural_report_file=structural_report_file,
        prompts_dir=prompts_dir,
        config=config,
        claude_opts=claude_opts,
        decomp_logger=decomp_logger,
        tracker=tracker,
    )

    # Step 4: Final verdict
    final_verdict = await run_verdict(
        state=state,
        structural_report_file=structural_report_file,
        prompts_dir=prompts_dir,
        config=config,
        claude_opts=claude_opts,
        mode="FINAL",
        detailed_report_file=detailed_report_file,
        decomp_logger=decomp_logger,
        tracker=tracker,
    )

    # Combine feedback from both reports
    structural_report = read_file(structural_report_file)
    detailed_report = read_file(detailed_report_file)
    combined_feedback = f"# Structural Verification\n\n{structural_report}\n\n---\n\n# Detailed Verification\n\n{detailed_report}"

    return final_verdict, combined_feedback


# ---------------------------------------------------------------------------
# Main orchestration
# ---------------------------------------------------------------------------

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

    Simplified pipeline:
    1. Decomposer creates a proof plan (decomposition)
    2. Single prover writes a complete proof following the plan
    3. Structural verification (Phases 1-5)
    4. Detailed verification (Phase 6)
    5. If verification fails, regulator decides: REVISE_PROOF, REVISE_PLAN, or REWRITE

    Returns the content of the final proof.md.
    """
    decomp_config = config.get("decomposition", DEFAULT_CONFIG)
    max_decompositions = decomp_config.get("max_decompositions", 3)
    max_revisions = decomp_config.get("max_revisions", 2)
    max_proof_attempts = decomp_config.get("max_proof_attempts", 3)

    # Initialize state and logger
    state = DecompositionState(output_dir)
    decomp_logger = DecompositionLogger(output_dir)

    # Log model configuration
    models = decomp_config.get("models", DEFAULT_MODELS)
    decomp_logger.log(f"Model configuration: {models}")

    # --- Resume detection ---
    resume_info = detect_decomposition_resume(output_dir)
    resume_point = resume_info["resume_point"]

    # Handle already-done case
    if resume_point == "done":
        decomp_logger.log("RESUMING: Proof already verified successfully, nothing to do.")
        proof_file = os.path.join(output_dir, "proof.md")
        return read_file(proof_file)

    # Restore state from disk
    if resume_info["has_progress"]:
        state.attempt = resume_info["attempt"]
        state.revision = resume_info["revision"]
        state.proof = resume_info["proof"]
        state.attempt_history = resume_info["attempt_history"]
        if resume_info["decomposition"]:
            state.decomposition = resume_info["decomposition"]

        decomp_logger.log(
            f"RESUMING: attempt={state.attempt}, revision={state.revision}, proof={state.proof}, "
            f"resume_point={resume_point}"
        )
        decomp_logger.update_status(
            state="RESUMING",
            attempt=state.attempt,
            revision=state.revision,
            proof=state.proof,
            recent_activity=f"Resuming from {resume_point}"
        )
    else:
        decomp_logger.log("Starting decomposition-based proof (fresh)")
        decomp_logger.update_status(
            state="INITIALIZING",
            attempt=1,
            revision=1,
            proof=1,
            recent_activity="Initializing decomposition prover"
        )

    os.makedirs(state.get_attempt_dir(), exist_ok=True)
    os.makedirs(state.get_revision_dir(), exist_ok=True)
    os.makedirs(state.get_proof_dir(), exist_ok=True)

    # --- Handle mid-pipeline resume points ---
    verification_feedback = ""
    regulator_guidance = ""

    # Handle decompose_revise resume: restore context from previous revision
    if resume_point == "decompose_revise" and state.revision > 1:
        prev_rev_dir = os.path.join(state.get_attempt_dir(), f"revision_{state.revision - 1}")
        # Find the last proof in the previous revision
        last_proof = _find_max_numbered_dir(prev_rev_dir, "proof_")
        if last_proof > 0:
            prev_proof_dir = os.path.join(prev_rev_dir, f"proof_{last_proof}")
            # Restore verification feedback
            detailed = read_file(os.path.join(prev_proof_dir, "detailed_verification.md"))
            structural = read_file(os.path.join(prev_proof_dir, "structural_verification.md"))
            if detailed or structural:
                verification_feedback = f"# Structural Verification\n\n{structural}\n\n---\n\n# Detailed Verification\n\n{detailed}"
            # Restore regulator guidance
            regulator_guidance = read_file(os.path.join(prev_proof_dir, "regulator_decision.md"))
            decomp_logger.log(f"RESUMING: Restored context from revision {state.revision - 1}")

    if resume_point == "verify_structural":
        # Have proof, need to run structural verification
        proof_file = os.path.join(state.get_proof_dir(), "proof.md")
        decomp_logger.log("RESUMING: Running structural verification")

        structural_report_file = await run_structural_verification(
            state=state,
            problem_file=problem_file,
            proof_file=proof_file,
            prompts_dir=prompts_dir,
            config=config,
            claude_opts=claude_opts,
            decomp_logger=decomp_logger,
            tracker=tracker,
        )

        # Get verdict from the structural report
        structural_verdict = await run_verdict(
            state=state,
            structural_report_file=structural_report_file,
            prompts_dir=prompts_dir,
            config=config,
            claude_opts=claude_opts,
            mode="STRUCTURAL",
            decomp_logger=decomp_logger,
            tracker=tracker,
        )

        if structural_verdict == "DONE":
            resume_point = "verify_detailed"
        else:
            # Need regulator decision
            resume_point = "regulator"
            verification_feedback = read_file(structural_report_file)

    if resume_point == "verify_detailed":
        # Structural passed, need detailed verification
        proof_file = os.path.join(state.get_proof_dir(), "proof.md")
        structural_report_file = os.path.join(state.get_proof_dir(), "structural_verification.md")
        decomp_logger.log("RESUMING: Running detailed verification")

        detailed_report_file = await run_detailed_verification(
            state=state,
            problem_file=problem_file,
            proof_file=proof_file,
            structural_report_file=structural_report_file,
            prompts_dir=prompts_dir,
            config=config,
            claude_opts=claude_opts,
            decomp_logger=decomp_logger,
            tracker=tracker,
        )

        # Get final verdict from both reports
        final_verdict = await run_verdict(
            state=state,
            structural_report_file=structural_report_file,
            prompts_dir=prompts_dir,
            config=config,
            claude_opts=claude_opts,
            mode="FINAL",
            detailed_report_file=detailed_report_file,
            decomp_logger=decomp_logger,
            tracker=tracker,
        )

        if final_verdict == "DONE":
            decomp_logger.log("Proof verification PASSED on resume. Done!")
            decomp_logger.update_status(
                state="COMPLETED",
                recent_activity="Proof completed and verified successfully (resumed)"
            )
            return read_file(proof_file)

        # Need regulator decision
        resume_point = "regulator"
        structural_report = read_file(structural_report_file)
        detailed_report = read_file(detailed_report_file)
        verification_feedback = f"# Structural Verification\n\n{structural_report}\n\n---\n\n# Detailed Verification\n\n{detailed_report}"

    if resume_point == "regulator":
        # Need to call regulator
        proof_file = os.path.join(state.get_proof_dir(), "proof.md")
        decomp_logger.log("RESUMING: Calling regulator after verification failure")

        # If verification_feedback is empty (initial resume at "regulator"), read from files
        if not verification_feedback:
            proof_dir = state.get_proof_dir()
            structural_report = read_file(os.path.join(proof_dir, "structural_verification.md"))
            detailed_report = read_file(os.path.join(proof_dir, "detailed_verification.md"))
            if structural_report or detailed_report:
                verification_feedback = f"# Structural Verification\n\n{structural_report}\n\n---\n\n# Detailed Verification\n\n{detailed_report}"

        decision = await run_regulator(
            state=state,
            verification_report=verification_feedback,
            proof_file=proof_file,
            config=config,
            prompts_dir=prompts_dir,
            claude_opts=claude_opts,
            decomp_logger=decomp_logger,
            tracker=tracker,
        )

        # Get regulator guidance from the decision file
        regulator_decision_file = os.path.join(state.get_proof_dir(), "regulator_decision.md")
        regulator_guidance = read_file(regulator_decision_file)

        if decision == "REVISE_PROOF":
            state.new_proof()
            resume_point = "prove"
        elif decision == "REVISE_PLAN":
            state.new_revision()
            resume_point = "decompose_revise"
        else:  # REWRITE
            state.new_attempt()
            resume_point = "decompose"

    # --- Main loop ---
    while state.attempt <= max_decompositions:
        # === Step 1: Create/Revise/Rewrite decomposition ===
        if resume_point in ("fresh", "decompose"):
            mode = "CREATE" if state.attempt == 1 and not resume_info["has_progress"] else "REWRITE"
            decomp_logger.log(f"Running decomposer in {mode} mode (attempt {state.attempt})")

            decomposition = await run_decomposer(
                state=state,
                problem_file=problem_file,
                related_work_file=related_work_file,
                difficulty_file=difficulty_file,
                prompts_dir=prompts_dir,
                config=config,
                claude_opts=claude_opts,
                mode=mode,
                regulator_guidance=regulator_guidance,
                decomp_logger=decomp_logger,
                tracker=tracker,
            )
            regulator_guidance = ""  # Clear after use

        elif resume_point == "decompose_revise":
            decomp_logger.log(f"Running decomposer in REVISE mode (attempt {state.attempt})")

            decomposition = await run_decomposer(
                state=state,
                problem_file=problem_file,
                related_work_file=related_work_file,
                difficulty_file=difficulty_file,
                prompts_dir=prompts_dir,
                config=config,
                claude_opts=claude_opts,
                mode="REVISE",
                verification_feedback=verification_feedback,
                regulator_guidance=regulator_guidance,
                decomp_logger=decomp_logger,
                tracker=tracker,
            )
            verification_feedback = ""
            regulator_guidance = ""

        else:
            # Resuming with existing decomposition
            decomposition = state.decomposition
            if not decomposition:
                try:
                    decomposition = state.load_decomposition()
                except yaml.YAMLError as e:
                    decomp_logger.log(f"Warning: Decomposition file has invalid YAML: {e}")
                    decomposition = None
            if not decomposition:
                # Safety fallback: decomposition missing or corrupted, start fresh
                decomp_logger.log("Warning: Decomposition missing on resume, creating new one")
                state.proof = 1  # Reset proof counter since we're starting fresh
                decomposition = await run_decomposer(
                    state=state,
                    problem_file=problem_file,
                    related_work_file=related_work_file,
                    difficulty_file=difficulty_file,
                    prompts_dir=prompts_dir,
                    config=config,
                    claude_opts=claude_opts,
                    mode="CREATE",
                    decomp_logger=decomp_logger,
                    tracker=tracker,
                )

        key_steps = decomposition.get("key_steps", [])
        total_steps = len(decomposition.get("steps", []))
        decomp_logger.log(f"Decomposition: {total_steps} steps, {len(key_steps)} key steps")

        # === Step 2-5: Proof attempts loop ===
        while state.proof <= max_proof_attempts:
            proof_dir = state.get_proof_dir()
            os.makedirs(proof_dir, exist_ok=True)
            proof_file = os.path.join(proof_dir, "proof.md")

            # === Step 2: Run single prover ===
            if resume_point in ("fresh", "decompose", "decompose_revise", "prove"):
                decomp_logger.log(f"Running single prover (attempt {state.attempt}, revision {state.revision}, proof {state.proof})")
                decomp_logger.update_status(
                    state="PROVING",
                    attempt=state.attempt,
                    revision=state.revision,
                    proof=state.proof,
                    recent_activity=f"Writing proof (proof {state.proof})"
                )

                proof = await run_single_prover(
                    state=state,
                    problem_file=problem_file,
                    related_work_file=related_work_file,
                    prompts_dir=prompts_dir,
                    config=config,
                    claude_opts=claude_opts,
                    decomp_logger=decomp_logger,
                    tracker=tracker,
                )

            resume_point = "verify_structural"  # Normal flow continues to verification

            # === Step 3: Structural verification ===
            decomp_logger.log("Running structural verification")
            decomp_logger.update_status(
                state="VERIFYING_STRUCTURAL",
                recent_activity="Running structural verification (Phases 1-5)"
            )

            structural_report_file = await run_structural_verification(
                state=state,
                problem_file=problem_file,
                proof_file=proof_file,
                prompts_dir=prompts_dir,
                config=config,
                claude_opts=claude_opts,
                decomp_logger=decomp_logger,
                tracker=tracker,
            )

            # === Step 3b: Structural verdict ===
            decomp_logger.log("Running structural verdict check...")
            structural_verdict = await run_verdict(
                state=state,
                structural_report_file=structural_report_file,
                prompts_dir=prompts_dir,
                config=config,
                claude_opts=claude_opts,
                mode="STRUCTURAL",
                decomp_logger=decomp_logger,
                tracker=tracker,
            )

            if structural_verdict == "CONTINUE":
                # Get feedback and consult regulator
                verification_feedback = read_file(structural_report_file)
                decomp_logger.log("Structural verification FAILED. Consulting regulator...")

                decision = await run_regulator(
                    state=state,
                    verification_report=verification_feedback,
                    proof_file=proof_file,
                    config=config,
                    prompts_dir=prompts_dir,
                    claude_opts=claude_opts,
                    decomp_logger=decomp_logger,
                    tracker=tracker,
                )

                regulator_decision_file = os.path.join(proof_dir, "regulator_decision.md")
                regulator_guidance = read_file(regulator_decision_file)

                if decision == "REVISE_PROOF":
                    decomp_logger.log("Regulator: REVISE_PROOF - trying new proof with same plan")
                    state.new_proof()
                    resume_point = "prove"
                    continue  # Continue proof attempts loop

                elif decision == "REVISE_PLAN":
                    decomp_logger.log("Regulator: REVISE_PLAN - revising decomposition")
                    state.new_revision()
                    resume_point = "decompose_revise"
                    break  # Break to outer loop for decomposition revision

                else:  # REWRITE
                    decomp_logger.log("Regulator: REWRITE - starting new decomposition attempt")
                    state.new_attempt()
                    resume_point = "decompose"
                    break  # Break to outer loop for new attempt

            # === Step 4: Detailed verification ===
            decomp_logger.log("Structural verdict DONE. Running detailed verification...")
            decomp_logger.update_status(
                state="VERIFYING_DETAILED",
                recent_activity="Running detailed verification (Phase 6)"
            )

            detailed_report_file = await run_detailed_verification(
                state=state,
                problem_file=problem_file,
                proof_file=proof_file,
                structural_report_file=structural_report_file,
                prompts_dir=prompts_dir,
                config=config,
                claude_opts=claude_opts,
                decomp_logger=decomp_logger,
                tracker=tracker,
            )

            # === Step 5: Final verdict ===
            decomp_logger.log("Running final verdict check...")
            final_verdict = await run_verdict(
                state=state,
                structural_report_file=structural_report_file,
                prompts_dir=prompts_dir,
                config=config,
                claude_opts=claude_opts,
                mode="FINAL",
                detailed_report_file=detailed_report_file,
                decomp_logger=decomp_logger,
                tracker=tracker,
            )

            if final_verdict == "DONE":
                # === Success! ===
                decomp_logger.log("Final verdict: DONE. Proof completed successfully!")
                decomp_logger.update_status(
                    state="COMPLETED",
                    recent_activity="Proof completed and verified successfully"
                )
                return read_file(proof_file)

            # === Step 6: Verification failed - consult regulator ===
            structural_report = read_file(structural_report_file)
            detailed_report = read_file(detailed_report_file)
            verification_feedback = f"# Structural Verification\n\n{structural_report}\n\n---\n\n# Detailed Verification\n\n{detailed_report}"

            decomp_logger.log("Final verdict: CONTINUE. Consulting regulator...")

            decision = await run_regulator(
                state=state,
                verification_report=verification_feedback,
                proof_file=proof_file,
                config=config,
                prompts_dir=prompts_dir,
                claude_opts=claude_opts,
                decomp_logger=decomp_logger,
                tracker=tracker,
            )

            regulator_decision_file = os.path.join(proof_dir, "regulator_decision.md")
            regulator_guidance = read_file(regulator_decision_file)

            if decision == "REVISE_PROOF":
                decomp_logger.log("Regulator: REVISE_PROOF - trying new proof with same plan")
                state.new_proof()
                resume_point = "prove"
                continue  # Continue proof attempts loop

            elif decision == "REVISE_PLAN":
                decomp_logger.log("Regulator: REVISE_PLAN - revising decomposition")
                state.new_revision()
                resume_point = "decompose_revise"
                break  # Break to outer loop for decomposition revision

            else:  # REWRITE
                decomp_logger.log("Regulator: REWRITE - starting new decomposition attempt")
                state.new_attempt()
                resume_point = "decompose"
                break  # Break to outer loop for new attempt

        # Check if we exhausted proof attempts without breaking for revision/rewrite
        if state.proof > max_proof_attempts:
            # Try to escalate: first to REVISE_PLAN, then to REWRITE
            # Clear stale regulator_guidance from REVISE_PROOF decisions and provide
            # appropriate guidance for the escalation type
            if state.revision < max_revisions:
                decomp_logger.log(f"Max proof attempts ({max_proof_attempts}) exhausted, triggering plan revision")
                state.new_revision()
                resume_point = "decompose_revise"
                # Provide synthetic guidance for plan revision (not stale REVISE_PROOF guidance)
                regulator_guidance = (
                    f"# Automatic Escalation to Plan Revision\n\n"
                    f"All {max_proof_attempts} proof attempts for the previous revision failed verification. "
                    f"The repeated failures suggest structural issues with the decomposition plan rather than "
                    f"execution errors. Please revise the plan to address the persistent verification failures "
                    f"shown in the verification feedback above."
                )
            else:
                decomp_logger.log(f"Max revisions ({max_revisions}) exhausted, triggering rewrite")
                state.new_attempt()
                resume_point = "decompose"
                # Provide synthetic guidance for rewrite
                regulator_guidance = (
                    f"# Automatic Escalation to Complete Rewrite\n\n"
                    f"All {max_revisions} plan revisions have been exhausted without success. "
                    f"The fundamental proof strategy appears to be flawed. Please design a "
                    f"completely different approach to prove this problem."
                )

        # If we broke out for REVISE_PLAN, check revision limit
        if resume_point == "decompose_revise":
            if state.revision > max_revisions:
                decomp_logger.log(f"Max revisions ({max_revisions}) exhausted, triggering rewrite")
                state.new_attempt()
                resume_point = "decompose"
                # Update guidance for rewrite (was set for plan revision)
                regulator_guidance = (
                    f"# Escalation from Plan Revision to Complete Rewrite\n\n"
                    f"The regulator suggested revising the plan, but all {max_revisions} revision slots "
                    f"have been used. A completely new proof strategy is needed. "
                    f"The previous plan revision guidance was:\n\n{regulator_guidance}"
                )
            else:
                continue  # Continue with same attempt, revised plan

        # Otherwise (REWRITE or exhausted), check if we have more attempts
        if state.attempt > max_decompositions:
            break

    decomp_logger.log("All decomposition attempts exhausted - running final failure analysis")

    # Find the last proof and verification for FINAL mode regulator
    # We need to step back since state may have incremented past the last valid proof
    last_proof_dir = None
    for a in range(state.attempt, 0, -1):
        attempt_dir = os.path.join(state.decomp_dir, f"attempt_{a}")
        if not os.path.isdir(attempt_dir):
            continue
        max_rev = _find_max_numbered_dir(attempt_dir, "revision_")
        for r in range(max_rev, 0, -1):
            revision_dir = os.path.join(attempt_dir, f"revision_{r}")
            max_p = _find_max_numbered_dir(revision_dir, "proof_")
            if max_p > 0:
                last_proof_dir = os.path.join(revision_dir, f"proof_{max_p}")
                break
        if last_proof_dir:
            break

    if last_proof_dir:
        last_proof_file = os.path.join(last_proof_dir, "proof.md")
        # Get the last verification report (prefer detailed over structural)
        last_verification = read_file(os.path.join(last_proof_dir, "detailed_verification.md"))
        if not last_verification:
            last_verification = read_file(os.path.join(last_proof_dir, "structural_verification.md"))
        if not last_verification:
            last_verification = "No verification report found for last attempt."

        # Call regulator in FINAL mode for failure analysis
        await run_regulator(
            state=state,
            verification_report=last_verification,
            proof_file=last_proof_file,
            config=config,
            prompts_dir=prompts_dir,
            claude_opts=claude_opts,
            decomp_logger=decomp_logger,
            tracker=tracker,
            mode="FINAL",
        )

    decomp_logger.update_status(
        state="FAILED",
        recent_activity="All decomposition attempts exhausted - failure analysis written"
    )

    return ""


# ---------------------------------------------------------------------------
# Entry point for testing
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import sys

    print("Decomposition prover module loaded.")
    print("Use run_decomposition_prover() from pipeline.py to run.")
