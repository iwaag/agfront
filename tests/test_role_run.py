"""Role resolution, the run record, and one real run through the `fake` harness."""

import json
import os
import stat

from agag.agent_config import ResolvedAgent
from agag.harness import HarnessResult

from agfront import role_run, zulip_listener


def resolved(role: str) -> ResolvedAgent:
    return ResolvedAgent(
        role=role,
        profile="stub",
        harness="fake",
        provider="ollama",
        model="ollama/test",
        model_options={},
        command="agent",
        provider_base_url=None,
    )


def wire(monkeypatch, calls, *, output="answer", exit_code=0):
    monkeypatch.setattr(
        role_run, "resolve_agfront_role", lambda role, **kwargs: resolved(role)
    )
    monkeypatch.setattr(
        role_run,
        "run_harness",
        lambda agent, prompt, **kwargs: (
            calls.append((agent, prompt, kwargs))
            or HarnessResult(output, exit_code, {"role": agent.role, "outcome": "done"})
        ),
    )


def test_run_role_writes_its_run_record(monkeypatch, tmp_path):
    calls = []
    wire(monkeypatch, calls)
    record = tmp_path / "records" / "run-0001.json"

    output, run_record, code = role_run.run_role(
        "front", "question", cwd=tmp_path, timeout=30, record=record
    )

    assert (output, code) == ("answer", 0)
    assert run_record["schema"] == "ag.agent-run.v1"
    written = json.loads(record.read_text())
    assert written["schema"] == "ag.agent-run.v1"
    assert written["request_id"] == "run-0001"
    assert written["role"] == "front"
    # The caller's cwd wins: the listener points the role at its generation
    # directory, and nothing here pins a fixed workspace.
    assert calls[0][2]["cwd"] == tmp_path


def test_every_role_carries_a_tool_grant(monkeypatch, tmp_path):
    """A role missing from the table gets no `--allowedTools` at all, and
    claude_code then waits for an interactive answer until the timeout."""
    calls = []
    wire(monkeypatch, calls)
    role_run.run_role("front", "p", cwd=tmp_path, timeout=5)
    assert calls[0][2]["allowed_tools"] == role_run.ROLE_ALLOWED_TOOLS["front"]
    assert set(role_run.ROLE_ALLOWED_TOOLS) == set(
        role_run.load_config(role_run.AGENTS_CONFIG)[0]["roles"]
    )


def test_front_s_grant_is_reading_plus_agentchat():
    """Front routes; the work happens elsewhere. Since p2 it does the asking
    itself, so its grant is the reading tools plus the one command that
    reaches another agent — and no general shell."""
    grant = role_run.ROLE_ALLOWED_TOOLS["front"]
    assert "Bash(agentchat:*)" in grant
    assert "Bash(" not in grant.replace("Bash(agentchat:*)", "")
    # There is no command file any more; nothing in the workspace is read
    # back by the handler, so Front has no reason to write one.
    assert "Write" not in grant


def test_the_run_carries_the_agentchat_identity(tmp_path):
    """The one thing agfront decides about the outbound side: which
    credentials file the run speaks with, handed over as a path so the secret
    stays in `.local/`."""
    environment = role_run.tool_environment(zulip_env=tmp_path / "zulip.env")
    assert environment[role_run.AGENTCHAT_ENV_VARIABLE] == str(tmp_path / "zulip.env")
    # A path, never a value: no credential is inlined into the run env.
    assert not any(key.startswith("ZULIP_") for key in environment)


def test_agentchat_is_reachable_by_its_bare_name(tmp_path):
    """`agentchat` is installed beside the interpreter that runs the
    listener, and the guide names it without a path."""
    (tmp_path / "agentchat").touch()
    environment = role_run.tool_environment(bin_dir=tmp_path)
    assert environment["PATH"].split(os.pathsep)[0] == str(tmp_path)


def test_a_missing_bin_directory_leaves_path_alone(tmp_path):
    environment = role_run.tool_environment(bin_dir=tmp_path / "absent")
    assert "PATH" not in environment


def test_a_resolved_role_carries_the_handover(monkeypatch, tmp_path):
    (tmp_path / "agents.toml").write_text(
        'schema = "ag.agent-config.v1"\n'
        '[models."ollama/test-model"]\n'
        "[profiles.stub]\n"
        'harness = "fake"\n'
        'model = "ollama/test-model"\n'
        "[roles.front]\n"
        'profile = "stub"\n'
        "requires = []\n"
    )
    (tmp_path / "agents.local.toml").write_text(
        'schema = "ag.agent-config.v1"\n[local.harness.fake]\ncommand = "/bin/echo"\n'
    )
    agent = role_run.resolve_agfront_role(
        "front",
        config_path=tmp_path / "agents.toml",
        overlay_path=tmp_path / "agents.local.toml",
    )
    assert agent.environment[role_run.AGENTCHAT_ENV_VARIABLE] == str(role_run.ZULIP_ENV)


def test_resolution_obeys_the_config_pair_it_is_pointed_at(tmp_path, monkeypatch):
    """Nothing may silently fall back to the committed config and launch a
    real, paid harness from inside the test suite."""
    (tmp_path / "agents.toml").write_text(
        'schema = "ag.agent-config.v1"\n'
        '[models."ollama/test-model"]\n'
        "[profiles.stub]\n"
        'harness = "fake"\n'
        'model = "ollama/test-model"\n'
        "[roles.front]\n"
        'profile = "stub"\n'
        "requires = []\n"
    )
    (tmp_path / "agents.local.toml").write_text(
        'schema = "ag.agent-config.v1"\n[local.harness.fake]\ncommand = "/bin/echo"\n'
    )
    agent = role_run.resolve_agfront_role(
        "front",
        config_path=tmp_path / "agents.toml",
        overlay_path=tmp_path / "agents.local.toml",
    )
    assert (agent.harness, agent.command) == ("fake", "/bin/echo")


# --- the whole route, once, on the stub profile ----------------------------


def stub_config(tmp_path, script_body: str) -> None:
    """A `stub`-profile config pair whose `fake` harness is `script_body`."""
    script = tmp_path / "stub-agent.sh"
    script.write_text(f"#!/bin/sh\n{script_body}\n")
    script.chmod(script.stat().st_mode | stat.S_IXUSR)
    (tmp_path / "agents.toml").write_text(
        'schema = "ag.agent-config.v1"\n'
        'project = "agfront"\n'
        '[models."ollama/test-model"]\n'
        "[profiles.stub]\n"
        'harness = "fake"\n'
        'model = "ollama/test-model"\n'
        "[roles.front]\n"
        'profile = "stub"\n'
        "requires = []\n"
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

    def topic_history(self, channel, topic, num_before):
        if channel == "agents":
            return [
                {
                    "id": 99,
                    "sender_id": 13,
                    "sender_full_name": "Forge",
                    "content": "# agforge\n\nOpen a `create-…` topic in `agforge-agstudio1`.",
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
        "command -v agentchat > agentchat.seen\n"
        "echo asking",
    )
    monkeypatch.setattr(role_run, "AGENTS_CONFIG", tmp_path / "agents.toml")
    monkeypatch.setattr(role_run, "AGENTS_LOCAL_CONFIG", tmp_path / "agents.local.toml")
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
    assert posts[-1][2] == "asking"

    workspace = tmp_path / "topics" / "front" / "front-stub" / "1" / "front"
    # The run really saw its prompt on stdin, and the prompt was the placement
    # line plus the guide this repository actually ships — not a fixture.
    prompt = (workspace / "chatlog.seen").read_text()
    assert prompt.startswith("The chatlog is placed in the working directory.")
    assert "agentchat" in prompt
    assert "please advance the work" in (workspace / "chatlog.md").read_text()
    # And it saw the board, the identity, and a reachable `agentchat`.
    assert "agforge-agstudio1" in (workspace / "board.seen").read_text()
    assert (workspace / "identity.seen").read_text().strip() == str(role_run.ZULIP_ENV)
    assert os.path.basename((workspace / "agentchat.seen").read_text().strip()) == "agentchat"
    record = json.loads((tmp_path / "records" / "front" / "run-0001.json").read_text())
    assert record["harness"] == "fake"
