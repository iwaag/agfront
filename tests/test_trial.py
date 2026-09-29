"""agent_guide p2 ex1: a trial from the main checkout touches nothing real."""

from __future__ import annotations

from pathlib import Path

from agag.agent import RECORDS_ROOT_VARIABLE

from agfront import trial, zulip_listener


def _files(root: Path) -> set[str]:
    return {str(p.relative_to(root)) for p in root.rglob("*")} if root.is_dir() else set()


def test_a_dry_run_from_the_checkout_leaves_its_runs_alone(tmp_path, monkeypatch):
    local = trial.ROOT / ".local"
    before = {name: _files(local / name) for name in ("agent", "topics")}
    # What the trial changes for the rest of the process, restored afterwards.
    monkeypatch.setenv(RECORDS_ROOT_VARIABLE, "")
    for name in ("TOPICS_ROOT", "RECORDS_ROOT", "GUIDES", "SHARED_GUIDES", "PYAGAG_SECTIONS", "run_front"):
        monkeypatch.setattr(zulip_listener, name, getattr(zulip_listener, name))
    out = tmp_path / "out"
    assert trial.main(["growbox-thing", "--out", str(out), "--dry-run"]) == 2  # a dry reply passes nothing
    assert {name: _files(local / name) for name in ("agent", "topics")} == before
    assert (out / "prompt.md").is_file() and (out / "outcome.json").is_file()
    assert list((out / "records").rglob("run-*.json")) and list((out / "records").rglob("chatlog.md"))


def test_a_baseline_is_a_revision(tmp_path, monkeypatch):
    monkeypatch.setenv(RECORDS_ROOT_VARIABLE, "")
    for name in ("TOPICS_ROOT", "RECORDS_ROOT", "GUIDES", "SHARED_GUIDES", "PYAGAG_SECTIONS"):
        monkeypatch.setattr(zulip_listener, name, getattr(zulip_listener, name))
    out = tmp_path / "old"
    trial.main(["growbox-thing", "--out", str(out), "--guides-rev", "447bb03", "--no-shared", "--dry-run"])
    prompt = (out / "prompt.md").read_text(encoding="utf-8")
    assert (out / "guides@447bb03" / "agent" / "guides" / "shared" / "work.md").is_file()
    assert "# When you ask another agent" not in prompt  # pyagag's callback section, new in p2


def test_a_delegation_is_answered_and_served_again(tmp_path, monkeypatch):
    """The responder and the serving loop, without a model: a stub run sends
    with the real `agentchat` in the run's own environment, as a run would."""
    import json
    import os
    import subprocess
    from types import SimpleNamespace

    from agag import agent
    from agag.harness import HarnessResult

    monkeypatch.setenv(RECORDS_ROOT_VARIABLE, "")
    for name in ("TOPICS_ROOT", "RECORDS_ROOT", "GUIDES", "SHARED_GUIDES", "PYAGAG_SECTIONS"):
        monkeypatch.setattr(zulip_listener, name, getattr(zulip_listener, name))

    def resolve(spec, role, *, home=None, **_):
        return SimpleNamespace(allowed_tools=(), environment=agent.chat_environment(spec, home=home))

    seen: list[str] = []

    def stub(resolved, prompt, *, cwd, **_):
        threads = "\n".join(p.read_text(encoding="utf-8") for p in Path(cwd).rglob("*.md") if "threads" in p.parts)
        seen.append(threads)
        environment = {**os.environ, **resolved.environment}

        def send(text):
            # The running task's own topic: a door live autolab serves (a new
            # workplan- topic would be a new mission; agent_guide p3 ex2).
            subprocess.run(["agentchat", "send", "work-m20510", "workrun-task1-m20510", text], env=environment,
                           check=True, capture_output=True)

        if len(seen) == 1:
            send("@**autolab-agstudio1** how many hours a day should the lights be on?")
            text = "I asked autolab."
        elif len(seen) == 2:
            send("@**autolab-agstudio1** The Developer chose the cheaper one: 12 h a day.")
            text = "autolab asked; I answered 12 h."
        else:
            text = "autolab set the lights to 12 h a day (b41d0e7)."
        return HarnessResult(output=f"<ag-reply intent=report>\n{text}\n</ag-reply>", exit_code=0, meta={})

    monkeypatch.setattr(agent, "resolve_spec_role", resolve)
    monkeypatch.setattr(agent, "run_harness", stub)
    out = tmp_path / "out"
    trial.main(["delegate-decision", "--out", str(out)])
    outcome = json.loads((out / "outcome.json").read_text(encoding="utf-8"))
    # A stub leaves no session log, so the one rule on tool calls is all it misses.
    assert len(outcome["servings"]) == 3 and outcome["missing"] == ["tool call: agentchat send"], outcome
    assert not outcome["against"] and outcome["met"] == ["12 h"]
    assert "I need a decision" in seen[1] and "b41d0e7" in seen[2]
    assert any("12 h a day" in s for s in outcome["sends"])
    assert [d["door"] for d in outcome["doors"]] == ["answer", "answer"]
