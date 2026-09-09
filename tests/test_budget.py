"""The observation a routine run judges its conditions against
(`refine_routine` p1 step 3).

Nothing here decides whether a condition is reached — that is the run's
judgment. What is pinned is that each observation a run may face is
rendered so that it cannot be misread: a number below the threshold, one
already past it, one from a window whose reset has passed since the read,
and a failed read whose last good numbers are marked stale; that the tool
reads a file as well as the relay; and that a run serving is handed the
document while other roles are not.
"""

import json
from pathlib import Path

from agag.agent_config import load_config, resolve_role

from agfront import budget, zulip_listener
from agfront.instance import SPEC
from test_routine_run import RUN_CHANNEL, RUN_TOPIC, RunBoard, ack, entry, opening, origin_note, runs, wire_runs
from test_zulip_listener import CHANNEL, TOPIC, Client

FIXTURES = Path(__file__).parent / "fixtures" / "budget"
NOW = 1788966000.0  # 2026-09-09T15:00:00Z, the fixtures' "now"


def fixture(name):
    return json.loads((FIXTURES / f"{name}.json").read_text())


def claude_section(text):
    return text.split("## claude_code")[1].split("## codex")[0]


def test_a_reading_below_the_threshold_is_just_the_number():
    text = budget.render(fixture("below"), now=NOW)
    section = claude_section(text)
    assert "5-hour session: 31 % used; resets 2026-09-09 17:00 UTC (in 2h 00m)" in section
    assert "FAILED" not in section and "STALE" not in section and "already passed" not in section
    assert "read at 2026-09-09 14:59 UTC" in section


def test_a_reading_already_past_the_threshold_reads_the_same_way():
    """The tool never says "reached": it says 57 %, and the run reads the
    condition."""
    section = claude_section(budget.render(fixture("reached"), now=NOW))
    assert "5-hour session: 57 % used" in section
    assert "reached" not in section.lower()


def test_a_reset_that_passed_since_the_read_is_flagged():
    section = claude_section(budget.render(fixture("reset"), now=NOW))
    assert "5-hour session: 72 % used; resets 2026-09-09 14:50 UTC (10m ago)" in section
    assert "this reset has already passed since the read" in section
    assert "current usage unknown until re-read" in section


def test_a_failed_read_is_a_failure_with_stale_numbers_marked():
    section = claude_section(budget.render(fixture("failed"), now=NOW))
    assert "READ FAILED" in section and "expired at 21:15" in section
    assert "Last good numbers, read 2026-09-09 12:00 UTC (3h 00m ago) — **STALE**" in section
    assert "5-hour session: 12 % used" in section
    # The failure never becomes a number of its own.
    assert "0 % used" not in section and "usage unknown" not in section.split("STALE")[1]


def test_a_source_that_cannot_be_read_is_rendered_as_that(tmp_path):
    text = budget.observe(str(tmp_path / "missing.json"), now=NOW)
    assert "READ FAILED" in text and "Usage is unknown" in text
    assert "nothing here says a condition was reached" in text


def test_the_cli_reads_a_file_and_prints_the_observation(capsys):
    assert budget.main(["--source", str(FIXTURES / "below.json")]) == 0
    out = capsys.readouterr().out
    assert out.startswith("# Budget observation") and "31 % used" in out and "## codex (plan plus)" in out


def test_the_cli_json_is_the_raw_document(capsys):
    assert budget.main(["--json", "--source", str(FIXTURES / "reached.json")]) == 0
    assert json.loads(capsys.readouterr().out)["harnesses"]["claude_code"]["windows"][0]["percent"] == 57.0


def test_the_source_is_the_environment_or_the_relay():
    assert budget.source_from_environment({}) == "http://127.0.0.1:8094/budget"
    assert budget.source_from_environment({"AGFRONT_BUDGET_URL": "/tmp/x.json"}) == "/tmp/x.json"


def test_the_doc_carries_the_explanation_and_the_observation(tmp_path):
    path = budget.write_budget_doc(tmp_path, str(FIXTURES / "reset.json"))
    text = path.read_text()
    assert path == tmp_path / "tools" / "budget.md"
    assert text.startswith("# Budget windows") and "agbudget" in text
    assert "condition reached, and never as 0" in text
    assert "this reset has already passed" in text


def test_a_run_serving_is_handed_the_observation(monkeypatch, tmp_path):
    calls = []
    wire_runs(monkeypatch, tmp_path, calls, budget_source=str(FIXTURES / "reached.json"))
    client = RunBoard(calls, {(RUN_CHANNEL, RUN_TOPIC): [origin_note(), opening(), ack(), entry()]})
    zulip_listener.handle_topic(client, RUN_CHANNEL, RUN_TOPIC)
    cwd = runs(calls)[0][2]
    text = (cwd / "tools" / "budget.md").read_text()
    assert "57 % used" in text and "# Budget windows" in text


def test_a_failed_observation_does_not_stop_the_run(monkeypatch, tmp_path):
    calls = []
    wire_runs(monkeypatch, tmp_path, calls, budget_source=str(tmp_path / "nowhere.json"))
    client = RunBoard(calls, {(RUN_CHANNEL, RUN_TOPIC): [origin_note(), opening(), ack(), entry()]})
    zulip_listener.handle_topic(client, RUN_CHANNEL, RUN_TOPIC)
    cwd = runs(calls)[0][2]
    assert "READ FAILED" in (cwd / "tools" / "budget.md").read_text()
    assert not any("failed during" in c[3] for c in calls if c[0] == "reply")


def test_an_ordinary_front_run_is_not_handed_a_budget(monkeypatch, tmp_path):
    calls = []
    wire_runs(monkeypatch, tmp_path, calls)
    zulip_listener.handle_topic(Client(calls), CHANNEL, TOPIC)
    cwd = runs(calls)[0][2]
    assert not (cwd / "tools" / "budget.md").exists()


def test_the_run_role_may_read_the_budget_and_nothing_else_new():
    config, overlay = load_config(SPEC.agents_config, Path("/nonexistent"))
    grant = resolve_role(config, overlay, "routine_run", check_available=False).allowed_tools
    assert "Bash(agbudget:*)" in grant and "Bash(agentchat:*)" in grant
    remaining = grant.replace("Bash(agentchat:*)", "").replace("Bash(agbudget:*)", "")
    assert "Bash(" not in remaining and "Write" not in grant
    for role in ("front", "character_talk"):
        assert "agbudget" not in resolve_role(config, overlay, role, check_available=False).allowed_tools


def test_the_run_guide_reads_the_conditions_the_way_the_plan_says():
    text = zulip_listener.guide("routine_run", "guide.md")
    assert "tools/budget.md" in text and "agbudget" in text
    assert "consume N points from the start" in text
    assert "failed or stale read" in text.lower()
    assert "achieved" in text and "reason" in text
