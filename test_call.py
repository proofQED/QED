#!/usr/bin/env python3

import json
import subprocess
from dataclasses import dataclass


@dataclass
class CallResult:
    response: str
    input_tokens: int
    output_tokens: int
    cached_tokens: int
    tool_calls: dict
    raw: dict


def call_codex(prompt: str) -> CallResult:
    """Call Codex with JSON output to extract metadata."""
    result = subprocess.run(
        [
            "codex",
            "--search",
            "-m",
            "gpt-5.4",
            "-c",
            'model_reasoning_effort="xhigh"',
            "exec",
            "--json",
            "--dangerously-bypass-approvals-and-sandbox",
            "-C",
            "/Users/an/Desktop/cm/proof_agent",
            prompt,
        ],
        check=True,
        capture_output=True,
        text=True,
    )

    # Parse JSONL output
    lines = result.stdout.strip().split("\n")
    events = [json.loads(line) for line in lines if line]

    response = ""
    input_tokens = 0
    output_tokens = 0
    cached_tokens = 0
    tool_calls = {}

    for event in events:
        if event.get("type") == "item.completed":
            item = event.get("item", {})
            if item.get("type") == "agent_message":
                response = item.get("text", "")
            elif item.get("type") == "tool_call":
                tool_name = item.get("name", "unknown")
                tool_calls[tool_name] = tool_calls.get(tool_name, 0) + 1
        elif event.get("type") == "turn.completed":
            usage = event.get("usage", {})
            input_tokens += usage.get("input_tokens", 0)
            output_tokens += usage.get("output_tokens", 0)
            cached_tokens += usage.get("cached_input_tokens", 0)

    return CallResult(
        response=response,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cached_tokens=cached_tokens,
        tool_calls=tool_calls,
        raw=events,
    )


def call_gemini(prompt: str) -> CallResult:
    """Call Gemini CLI with JSON output to extract metadata."""
    result = subprocess.run(
        [
            "gemini",
            "-m",
            "gemini-3-flash-preview",
            "-y",
            "-o",
            "json",
            "-p",
            prompt,
        ],
        check=True,
        capture_output=True,
        text=True,
        cwd="/Users/an/Desktop/cm/proof_agent",
    )

    data = json.loads(result.stdout)

    # Extract token stats across all models
    input_tokens = 0
    output_tokens = 0
    cached_tokens = 0
    thought_tokens = 0

    for _, model_stats in data.get("stats", {}).get("models", {}).items():
        tokens = model_stats.get("tokens", {})
        input_tokens += tokens.get("input", 0)
        output_tokens += tokens.get("candidates", 0)
        cached_tokens += tokens.get("cached", 0)
        thought_tokens += tokens.get("thoughts", 0)

    # Extract tool usage
    tool_stats = data.get("stats", {}).get("tools", {})
    tool_calls = {}
    for tool_name, tool_info in tool_stats.get("byName", {}).items():
        tool_calls[tool_name] = tool_info.get("totalCalls", 0)

    return CallResult(
        response=data.get("response", ""),
        input_tokens=input_tokens,
        output_tokens=output_tokens + thought_tokens,  # Include thinking tokens
        cached_tokens=cached_tokens,
        tool_calls=tool_calls,
        raw=data,
    )


if __name__ == "__main__":
    test_prompt = "what is 1+1"

    print("=== Codex ===")
    codex_result = call_codex(test_prompt)
    print(f"Response: {codex_result.response}")
    print(f"Input tokens: {codex_result.input_tokens}")
    print(f"Output tokens: {codex_result.output_tokens}")
    print(f"Cached tokens: {codex_result.cached_tokens}")
    print(f"Tool calls: {codex_result.tool_calls}")

    print("\n=== Gemini ===")
    gemini_result = call_gemini(test_prompt)
    print(f"Response: {gemini_result.response}")
    print(f"Input tokens: {gemini_result.input_tokens}")
    print(f"Output tokens: {gemini_result.output_tokens}")
    print(f"Cached tokens: {gemini_result.cached_tokens}")
    print(f"Tool calls: {gemini_result.tool_calls}")
