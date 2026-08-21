"""Resolve an agfront role and launch its configured harness.

agforge's shape, including its tool handover. Front used to run nothing and
write one command file; since p2 it talks to other agents itself, so it needs
`agentchat` on PATH and an identity to speak with. Both are decided here.

The identity is a path, never a value: `AGENTCHAT_ZULIP_ENV` names the front
bot's credentials file and the secret stays in `.local/`.

Since `agent_standardize` p7 the handover also carries `AGENTCHAT_HOME`, the
conversation this run is serving. p8 made it the whole of that handover:
`agentchat send` writes the home into the topic it posts in, as a
`[selfnote][rootchat]` note, so what lets an answer reach Front after the run
is over is the chat itself. There is no ledger file and no `AGENTCHAT_LEDGER`.
"""

from __future__ import annotations

import os
import sys
from dataclasses import replace
from pathlib import Path

from agag import selfnote
from agag.agent_config import ResolvedAgent, load_config, resolve_role
from agag.harness import run_harness, write_run_record

AGFRONT_ROOT = Path(__file__).resolve().parents[2]
AGENTS_CONFIG = AGFRONT_ROOT / "agents.toml"
AGENTS_LOCAL_CONFIG = AGFRONT_ROOT / ".local" / "agents.local.toml"
#: The front bot's Zulip credentials. Front speaks as itself — a post it
#: makes on the Developer's behalf is attributable to Front, which is what
#: makes asking permission first meaningful.
ZULIP_ENV = AGFRONT_ROOT / ".local" / "zulip.env"
#: `agag.chat.ENV_VARIABLE`, spelled here so the run and the CLI agree.
AGENTCHAT_ENV_VARIABLE = "AGENTCHAT_ZULIP_ENV"

# A role missing from this table gets no `--allowedTools` at all from
# `build_argv`, and claude_code then sits waiting for an interactive
# permission answer until the timeout. Every new role belongs here.
#
# Front routes; it does not do the work. The reading tools are for the
# chatlog and `tools/agents.md` in its own workspace, and `agentchat` is how
# it reaches the agent that does the work. This is the role's definition, not
# a fence around a role that would otherwise wander: without a grant,
# claude_code sits on an interactive permission prompt until the timeout.
ROLE_ALLOWED_TOOLS = {
    "front": "Read,Glob,Grep,Bash(agentchat:*)",
}


def tool_environment(
    bin_dir: Path | None = None,
    zulip_env: Path | None = None,
    home: tuple[str, str] | None = None,
) -> dict[str, str]:
    """The handover: `agentchat` reachable by name, speaking as the front bot.

    `run_harness` launches with `{**os.environ, **agent.environment}`, so this
    is the whole seam. The bin directory is the one holding the interpreter
    that runs the listener — in a `uv` project that is `.venv/bin`, where the
    `agentchat` console script is installed — so no deployment path is
    written down anywhere.

    `home` is the conversation being served. A run that posts somewhere else
    anchors that topic to it with a root note, which is how the answer finds
    its way back to this topic long after this run has ended.
    """
    directory = Path(sys.executable).parent if bin_dir is None else bin_dir
    environment = {AGENTCHAT_ENV_VARIABLE: str(zulip_env or ZULIP_ENV)}
    if home is not None:
        environment[selfnote.HOME_VARIABLE] = str(selfnote.Conversation(*home))
    if directory.is_dir():
        environment["PATH"] = os.pathsep.join(
            [str(directory), os.environ.get("PATH", "")]
        )
    return environment


def resolve_agfront_role(
    role: str,
    *,
    profile_override: str | None = None,
    check_available: bool = True,
    config_path: Path | None = None,
    overlay_path: Path | None = None,
    home: tuple[str, str] | None = None,
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
    agent = resolve_role(
        config, overlay, role,
        profile_override=profile_override,
        check_available=check_available,
    )
    return replace(
        agent, environment={**agent.environment, **tool_environment(home=home)}
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
    home: tuple[str, str] | None = None,
) -> tuple[str, dict, int]:
    """Resolve `role`, run it once, and return output, record, and exit code."""
    agent = resolve_agfront_role(role, profile_override=profile, home=home)
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
