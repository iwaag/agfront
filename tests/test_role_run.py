"""One real run through the `fake` harness, on the skeleton.

Role resolution, the handover (`AGENTCHAT_ZULIP_ENV`, `AGENTCHAT_HOME`,
`agentchat` on PATH) and the run record are `agag.agent.run_role`, tested in
pyagag. What is pinned here is Front's own: its grant in `agents.toml`, and
that the whole route — config pair, harness, workspace, harvest — is wired.
"""

import json
import os
import stat
from dataclasses import replace
from pathlib import Path

from agag.agent_config import load_config, resolve_role

from agfront import zulip_listener
from agfront.instance import SPEC


def test_front_s_grant_is_reading_plus_agentchat_and_schedule():
    """Front routes; the work happens elsewhere. Since p2 it does the asking
    itself, so its grant is the reading tools, the one command that reaches
    another agent, and the schedule-only CLI — with no general shell or file
    writer."""
    config, overlay = load_config(SPEC.agents_config, Path("/nonexistent"))
    grant = resolve_role(config, overlay, "front", check_available=False).allowed_tools
    assert "Bash(agentchat:*)" in grant
    assert "Bash(rtschedule:*)" in grant
    remaining = grant.replace("Bash(agentchat:*)", "").replace("Bash(rtschedule:*)", "")
    assert "Bash(" not in remaining
    assert "Write" not in grant


# --- the whole route, once, on the stub profile ----------------------------


def stub_config(tmp_path, script_body: str) -> None:
    """A `stub`-profile config pair whose `fake` harness is `script_body`."""
    script = tmp_path / "stub-agent.sh"
    script.write_text(f"#!/bin/sh\n{script_body}\n")
    script.chmod(script.stat().st_mode | stat.S_IXUSR)
    (tmp_path / "agents.toml").write_text(
        'schema = "ag.agent-config.v2"\n'
        'project = "agfront"\n'
        '[models."ollama/test-model"]\n'
        "[profiles.stub]\n"
        'harness = "fake"\n'
        'model = "ollama/test-model"\n'
        "[roles.front]\n"
        'profile = "stub"\n'
        "requires = []\n"
        'allowed_tools = "Read"\n'
    )
    (tmp_path / "agents.local.toml").write_text(
        'schema = "ag.agent-config.v1"\n'
        "[local.harness.fake]\n"
        f'command = "{script}"\n'
    )


class Client:
    """Only what the serving skeleton actually calls."""

    email = "front-bot@example.invalid"

    def __init__(self, posts):
        self.posts = posts

    def whoami(self):
        return {"user_id": 15, "full_name": "Front"}

    def stream_id(self, name):
        return 30

    def channel_topics(self, stream_id):
        return ["intro-agforge-agstudio1"]

    def own_rootchat_notes(self, num_before=200):
        return []

    def topic_history(self, channel, topic, num_before):
        if channel == "agents":
            return [
                {
                    "id": 99,
                    "sender_id": 13,
                    "sender_full_name": "Forge",
                    "content": "# agforge\n\nOpen an `assetplan-…` topic in `agforge-agstudio1`.",
                }
            ]
        return [
            {
                "id": 1,
                "sender_id": 8,
                "sender_full_name": "Developer",
                "content": "please advance the work",
            }
        ]


def test_a_stub_run_goes_through_the_real_harness_seam(monkeypatch, tmp_path):
    """The wiring proof: no `run_front` monkeypatch anywhere. The config pair,
    `run_harness`, the generation workspace and the intro harvest are all
    real; only the harness process and Zulip are stubs."""
    stub_config(
        tmp_path,
        "cat > chatlog.seen\n"
        "cp tools/agents.md board.seen\n"
        "printf '%s\\n' \"$AGENTCHAT_ZULIP_ENV\" > identity.seen\n"
        "printf '%s\\n' \"$AGENTCHAT_HOME\" > home.seen\n"
        "command -v agentchat > agentchat.seen\n"
        "echo asking",
    )
    # The spec rooted at tmp_path: its config pair is the stub one above, its
    # credentials path is tmp_path's (the file need not exist — it travels
    # as a path). Nothing can fall back to the committed config and launch a
    # real, paid harness from inside the test suite.
    spec = replace(SPEC, root=tmp_path)
    (tmp_path / ".local").mkdir()
    (tmp_path / ".local" / "agents.local.toml").write_text(
        (tmp_path / "agents.local.toml").read_text()
    )
    monkeypatch.setattr(zulip_listener, "SPEC", spec)
    monkeypatch.setattr(zulip_listener, "TOPICS_ROOT", tmp_path / "topics")
    monkeypatch.setattr(zulip_listener, "RECORDS_ROOT", tmp_path / "records")

    posts = []
    monkeypatch.setattr(
        "agag.topics.topic_write",
        lambda topic, text, **kwargs: posts.append((kwargs.get("channel"), topic, text)),
    )

    zulip_listener.handle_topic(Client(posts), "front", "front-stub")

    # Nothing but the front topic is posted to: the outbound side is Front's
    # own doing now, and agfront has no route of its own left.
    assert {post[0] for post in posts} == {"front"}
    assert posts[-1][2] == "@**Developer**\n\nasking"

    workspace = tmp_path / "topics" / "front" / "front-stub" / "1" / "front"
    # The run really saw its prompt on stdin, and the prompt was the placement
    # line plus the guide this repository actually ships — not a fixture.
    prompt = (workspace / "chatlog.seen").read_text()
    assert prompt.startswith("The chatlog is placed in the working directory.")
    assert "agentchat" in prompt
    assert "please advance the work" in (workspace / "chatlog.md").read_text()
    # And it saw the board, the identity, and a reachable `agentchat`.
    assert "agforge-agstudio1" in (workspace / "board.seen").read_text()
    assert (workspace / "identity.seen").read_text().strip() == str(spec.zulip_env)
    # …and which conversation it is posting on behalf of. That one variable
    # is the whole callback now: `agentchat send` writes it into whatever
    # topic this run posts in, as a root note, and the answer comes back here
    # after the run is over. Nothing is written to a file this agent keeps.
    assert (workspace / "home.seen").read_text().strip() == "front/front-stub"
    assert os.path.basename((workspace / "agentchat.seen").read_text().strip()) == "agentchat"
    record = json.loads((tmp_path / "records" / "front" / "run-0001.json").read_text())
    assert record["harness"] == "fake"
