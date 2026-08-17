"""Resolve an agfront role and launch its configured harness.

agforge's shape, minus the tool handover: agfront ships no CLI of its own and
gives its role no PATH, because Front does not run anything — it reads a
conversation and writes one file.
"""

from __future__ import annotations

from pathlib import Path

from agag.agent_config import ResolvedAgent, load_config, resolve_role
from agag.harness import run_harness, write_run_record

AGFRONT_ROOT = Path(__file__).resolve().parents[2]
AGENTS_CONFIG = AGFRONT_ROOT / "agents.toml"
AGENTS_LOCAL_CONFIG = AGFRONT_ROOT / ".local" / "agents.local.toml"

# A role missing from this table gets no `--allowedTools` at all from
# `build_argv`, and claude_code then sits waiting for an interactive
# permission answer until the timeout. Every new role belongs here.
#
# Front routes; it does not do the work, so it gets no shell. `Write` is here
# for exactly one file — `create.md`, the request the handler posts to forge —
# and the reading tools are for the chatlog in its own workspace. This is the
# role's definition, not a fence around a role that would otherwise wander.
ROLE_ALLOWED_TOOLS = {
    "front": "Read,Write,Glob,Grep",
}


def resolve_agfront_role(
    role: str,
    *,
    profile_override: str | None = None,
    check_available: bool = True,
    config_path: Path | None = None,
    overlay_path: Path | None = None,
) -> ResolvedAgent:
    """Resolve one role against agfront's config pair.

    The config pair is an argument, not a fixed fact, so a caller under test
    can point at its own pair and nothing here can silently fall back to the
    committed config and launch a real, paid harness.
    """
    config, overlay = load_config(
        config_path or AGENTS_CONFIG,
        AGENTS_LOCAL_CONFIG if overlay_path is None else overlay_path,
    )
    return resolve_role(
        config, overlay, role,
        profile_override=profile_override,
        check_available=check_available,
    )


def run_role(
    role: str,
    prompt: str,
    *,
    cwd: Path,
    timeout: float,
    profile: str | None = None,
    transcript: Path | None = None,
    record: Path | None = None,
) -> tuple[str, dict, int]:
    """Resolve `role`, run it once, and return output, record, and exit code."""
    agent = resolve_agfront_role(role, profile_override=profile)
    result = run_harness(
        agent,
        prompt,
        cwd=cwd,
        timeout=timeout,
        allowed_tools=ROLE_ALLOWED_TOOLS.get(role),
        transcript_path=transcript,
    )
    run_record = {"schema": "ag.agent-run.v1", **result.meta}
    if record:
        write_run_record(record, request_id=record.stem, meta=result.meta)
    return result.output, run_record, result.exit_code
