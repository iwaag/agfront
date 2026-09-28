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
