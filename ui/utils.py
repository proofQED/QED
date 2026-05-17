"""Shared constants and helper functions for the QED decomposition-mode UI."""

import json
import os
import re

import yaml

# ---------------------------------------------------------------------------
# Path constants
# ---------------------------------------------------------------------------

UI_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(UI_DIR)
RUN_SH = os.path.join(PROJECT_ROOT, "run.sh")
HUMAN_HELP_DIR = os.path.join(PROJECT_ROOT, "human_help")
DEFAULT_OUTPUT_ROOT = os.path.join(UI_DIR, "proof_runs")
ACTIVE_CONFIG_PATH = os.path.join(PROJECT_ROOT, ".config_run_active.yaml")
ORIGINAL_CONFIG_PATH = os.path.join(PROJECT_ROOT, "config.yaml")

GLOBAL_PROVE_HH = os.path.join(HUMAN_HELP_DIR, "additional_prove_human_help_global.md")
GLOBAL_VERIFY_HH = os.path.join(HUMAN_HELP_DIR, "additional_verify_rule_global.md")

MODEL_PROVIDERS = ("claude", "codex", "gemini")

DECOMP_DIR_NAME = "decomposition"

# Six decomposition-stage agents (config.yaml: decomposition.models.*)
AGENT_NAMES = (
    "decomposer",
    "single_prover",
    "regulator",
    "structural_verifier",
    "detailed_verifier",
    "verdict",
)

# Stage 0 and Stage 2 agents (config.yaml: pipeline.*)
PIPELINE_AGENT_NAMES = ("literature_survey", "proof_summary")

CODEX_REASONING_LEVELS = ("xhigh", "high", "medium", "low")
GEMINI_THINKING_LEVELS = ("HIGH", "MEDIUM", "LOW", "NONE")


# ---------------------------------------------------------------------------
# YAML I/O
# ---------------------------------------------------------------------------

def load_config(path: str) -> dict:
    """Read a YAML config file and return the parsed dict."""
    with open(path) as f:
        return yaml.safe_load(f) or {}


def save_config(config: dict, path: str) -> None:
    """Write *config* dict to a YAML file at *path*."""
    parent = os.path.dirname(path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    with open(path, "w") as f:
        yaml.dump(config, f, default_flow_style=False, sort_keys=False)


# ---------------------------------------------------------------------------
# File I/O
# ---------------------------------------------------------------------------

def read_file(path: str) -> str:
    """Read a text file. Return ``""`` if missing."""
    if not os.path.exists(path):
        return ""
    try:
        with open(path) as f:
            return f.read()
    except OSError:
        return ""


def write_file(path: str, content: str) -> None:
    """Write *content* to *path*, creating parent directories if needed."""
    parent = os.path.dirname(path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    with open(path, "w") as f:
        f.write(content)


def file_nonempty(path: str) -> bool:
    """Return True if *path* exists and has non-whitespace content."""
    if not os.path.exists(path):
        return False
    try:
        with open(path) as f:
            return bool(f.read().strip())
    except OSError:
        return False


# ---------------------------------------------------------------------------
# Survey helpers
# ---------------------------------------------------------------------------

def is_survey_complete(output_dir: str) -> bool:
    """True if Stage 0 finished. Mirrors ``pipeline.literature_survey_complete``:
    Easy → difficulty_evaluation.md + proof.md (no related_work.md required);
    otherwise → difficulty_evaluation.md + related_work.md.
    """
    ri = os.path.join(output_dir, "related_info")
    if not file_nonempty(os.path.join(ri, "difficulty_evaluation.md")):
        return False
    if parse_difficulty(output_dir) == "easy":
        return file_nonempty(os.path.join(output_dir, "proof.md"))
    return file_nonempty(os.path.join(ri, "related_work.md"))


def parse_difficulty(output_dir: str) -> str:
    """Parse difficulty classification. Returns easy/medium/hard/unknown."""
    path = os.path.join(output_dir, "related_info", "difficulty_evaluation.md")
    if not os.path.exists(path):
        return "unknown"
    try:
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
    except OSError:
        pass
    return "unknown"


# ---------------------------------------------------------------------------
# Decomposition path enumerators
# ---------------------------------------------------------------------------

def _numeric_dirs(parent: str, prefix: str) -> list[int]:
    if not os.path.isdir(parent):
        return []
    nums: list[int] = []
    for name in os.listdir(parent):
        if not name.startswith(prefix):
            continue
        tail = name[len(prefix):]
        try:
            nums.append(int(tail))
        except ValueError:
            continue
    nums.sort()
    return nums


def decomp_root(output_dir: str) -> str:
    return os.path.join(output_dir, DECOMP_DIR_NAME)


def attempt_dir(output_dir: str, n: int) -> str:
    return os.path.join(decomp_root(output_dir), f"attempt_{n}")


def revision_dir(output_dir: str, n: int, m: int) -> str:
    return os.path.join(attempt_dir(output_dir, n), f"revision_{m}")


def proof_dir(output_dir: str, n: int, m: int, k: int) -> str:
    return os.path.join(revision_dir(output_dir, n, m), f"proof_{k}")


def list_attempt_dirs(output_dir: str) -> list[int]:
    """Return sorted list of attempt numbers in ``decomposition/``."""
    return _numeric_dirs(decomp_root(output_dir), "attempt_")


def list_revision_dirs(attempt_path: str) -> list[int]:
    """Return sorted list of revision numbers inside an attempt directory."""
    return _numeric_dirs(attempt_path, "revision_")


def list_proof_dirs(revision_path: str) -> list[int]:
    """Return sorted list of proof numbers inside a revision directory."""
    return _numeric_dirs(revision_path, "proof_")


# ---------------------------------------------------------------------------
# Decomposition status / completion
# ---------------------------------------------------------------------------

_STATUS_ROW_RE = re.compile(r"^\|\s*([^|]+?)\s*\|\s*(.*?)\s*\|\s*$")
_LAST_UPDATED_RE = re.compile(r"\*\*Last Updated:\*\*\s*(.+)")


def parse_status_md(output_dir: str) -> dict:
    """Parse ``decomposition/STATUS.md`` into a dict.

    Looks for a markdown table with rows for State/Attempt/Revision/Proof
    and a ``Recent Activity`` section. Returns sentinel values when fields
    are missing.
    """
    path = os.path.join(decomp_root(output_dir), "STATUS.md")
    result = {
        "state": "",
        "attempt": None,
        "revision": None,
        "proof": None,
        "last_updated": "",
        "recent_activity": "",
        "raw": "",
    }
    raw = read_file(path)
    if not raw.strip():
        return result
    result["raw"] = raw

    last_match = _LAST_UPDATED_RE.search(raw)
    if last_match:
        result["last_updated"] = last_match.group(1).strip()

    # Walk the lines, picking out table rows
    for line in raw.splitlines():
        m = _STATUS_ROW_RE.match(line)
        if not m:
            continue
        key = m.group(1).strip().lower()
        value = m.group(2).strip()
        if key == "state":
            result["state"] = value
        elif key in ("attempt", "revision", "proof"):
            try:
                result[key] = int(value)
            except ValueError:
                pass

    # Recent Activity = paragraph after the "## Recent Activity" header
    activity_pos = raw.lower().find("## recent activity")
    if activity_pos != -1:
        tail = raw[activity_pos:].split("\n", 1)
        if len(tail) > 1:
            result["recent_activity"] = tail[1].strip()

    return result


def is_summary_complete(output_dir: str) -> bool:
    """True if Stage 2 wrote ``proof_effort_summary.md``, OR if the pipeline
    took the Easy short-circuit (Stage 2 is intentionally skipped for Easy
    problems — the survey agent writes proof.md directly and the pipeline
    returns without invoking the summary agent).
    """
    if file_nonempty(os.path.join(output_dir, "proof_effort_summary.md")):
        return True
    if parse_difficulty(output_dir) == "easy" and file_nonempty(
        os.path.join(output_dir, "proof.md")
    ):
        return True
    return False


def is_pipeline_complete(output_dir: str) -> bool:
    """Alias for ``is_summary_complete`` (Stage 2 / Easy short-circuit is final)."""
    return is_summary_complete(output_dir)


def proof_succeeded(output_dir: str) -> bool:
    """True if the decomposition loop has produced a verified ``proof.md``.

    A non-empty ``proof.md`` alone is NOT sufficient: the file can be a
    fallback write, an intermediate per-revision write, or a stale leftover
    from a prior run. The decomposition loop is only "done" when its
    ``STATUS.md`` reaches ``COMPLETED``. The Easy short-circuit (survey agent
    writes ``proof.md`` and the pipeline exits before invoking the
    decomposition prover) is also treated as success — recognized by a
    non-empty ``proof.md`` together with the absence of any decomposition
    directory.
    """
    if not file_nonempty(os.path.join(output_dir, "proof.md")):
        return False
    decomp_status_path = os.path.join(decomp_root(output_dir), "STATUS.md")
    if not os.path.exists(decomp_status_path):
        # No decomposition stage at all — Easy short-circuit.
        return True
    return parse_status_md(output_dir).get("state", "").upper() == "COMPLETED"


def decomp_failed(output_dir: str) -> bool:
    """True if the decomposition prover wrote a failure analysis."""
    return os.path.exists(
        os.path.join(decomp_root(output_dir), "failure_analysis.md")
    )


# ---------------------------------------------------------------------------
# Token usage
# ---------------------------------------------------------------------------

def parse_token_usage(output_dir: str) -> dict | None:
    """Read ``token_usage.json`` if present. Return dict or None."""
    path = os.path.join(output_dir, "token_usage.json")
    if not os.path.exists(path):
        return None
    try:
        with open(path) as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return None
