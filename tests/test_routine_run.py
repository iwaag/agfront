"""A routine run: opened by a Front conversation, started by the listener,
resumed by callbacks, finished by the run (`refine_routine` p1 step 2).

What is pinned here is the wiring, never what a run said: which topic is
served and by which role, that a run Front opened is started exactly once
and only after the serving that opened it has replied, that a delegate's
answer resumes the run it was asked from (and no other), that a restart
starts what was left unstarted and re-serves nothing already answered, and
that a finish block ends the run where the request came from.
"""

from agag.agent import SWEEP_ACK
from agag.selfnote import Conversation, rootchat_note, served_note
from agag.zulip import sweep_rootchats

from agfront import routine, zulip_listener
from test_zulip_listener import (
    BOT_ID,
    CHANNEL,
    HUMAN_ID,
    Board,
    desk_message,
    DESK_TOPIC,
    message,
    replies,
    role_calls,
    wire,
)

RUN_CHANNEL = "routine-ghtrends"
RUN_TOPIC = "routinerun-20260909-1500"
WORK_CHANNEL = "work-g-20"
WORK_TOPIC = "workrun-task1-g-20"

OPENING = ("Routine run opened. Requested in #front › front-desk-20260908-1530 (message 1): "
           "\"run ghtrends once\". Conditions: one repository, done when autolab reports the commit. "
           "Guide: #routine-ghtrends › guide, message 5498.")

FINISH = ("Autolab reported commit a99625f in work-g-20 › workrun-task1-g-20 (#7).\n\n"
          "```ag-routinerun\n"
          "{\"schema\": \"ag.routinerun-finish.v1\", \"achieved\": true, "
          "\"reason\": \"the one repository was summarised and committed\", "
          "\"report\": \"microsoft/markitdown summarised; commit a99625f; nothing left\"}\n"
          "```")


def post(channel, topic, content, *, id, sender_id=BOT_ID, name="Front"):
    return {"id": id, "type": "stream", "sender_id": sender_id, "sender_full_name": name,
            "display_recipient": channel, "subject": topic, "content": content}


def opening(id=10, sender_id=BOT_ID, name="Front"):
    return post(RUN_CHANNEL, RUN_TOPIC, OPENING, id=id, sender_id=sender_id, name=name)


def origin_note(id=9, origin=(CHANNEL, DESK_TOPIC), channel=RUN_CHANNEL, topic=RUN_TOPIC):
    return post(channel, topic, rootchat_note(Conversation(*origin)), id=id)


def ack(id=11, channel=RUN_CHANNEL, topic=RUN_TOPIC):
    return post(channel, topic, SWEEP_ACK, id=id)


def entry(id=12, text="asked autolab in work-g-20 › workrun-task1-g-20; waiting", channel=RUN_CHANNEL, topic=RUN_TOPIC):
    return post(channel, topic, text, id=id)


def run_note(id=20, run=(RUN_CHANNEL, RUN_TOPIC), channel=WORK_CHANNEL, topic=WORK_TOPIC):
    """Front's root note in the delegate's topic: opened on behalf of the run."""
    return post(channel, topic, rootchat_note(Conversation(*run)), id=id)


def answer(id=21, channel=WORK_CHANNEL, topic=WORK_TOPIC, text="@**Front** done: commit a99625f"):
    return post(channel, topic, text, id=id, sender_id=11, name="Autolab")


class RunBoard(Board):
    """A board that can also be resolved, and that answers the served-note
    narrow, so the recovery sweeps can be run against it."""

    def __init__(self, calls, histories, board=None):
        super().__init__(calls, histories, board=board)
        self.resolved = []

    def resolve_topic(self, message_id, topic):
        self.calls.append(("resolve", topic, message_id))
        self.resolved.append(topic)

    def own_served_notes(self, num_before=200):
        found = []
        for (channel, topic), history in self.histories.items():
            for row in history:
                if row.get("sender_id") == BOT_ID and str(row.get("content", "")).startswith("[selfnote][served]"):
                    found.append({**row, "type": "stream", "display_recipient": channel, "subject": topic})
        return found


def wire_runs(monkeypatch, tmp_path, calls, *, budget_source=None, **kw):
    """`wire`, plus the run guide and a budget observation that never
    touches the relay: a fixture file, or a path that does not exist (a
    failed read, rendered as one)."""
    wire(monkeypatch, tmp_path, calls, **kw)
    guides = tmp_path / "guides"
    (guides / "routine_run").mkdir(parents=True, exist_ok=True)
    (guides / "routine_run" / "guide.md").write_text("RUN GUIDE")
    monkeypatch.setenv("AGFRONT_BUDGET_URL", budget_source or str(tmp_path / "no-budget.json"))


def runs(calls):
    return [c for c in calls if c[0] == "front"]


# --- the role and its files -------------------------------------------------


def test_a_run_topic_is_served_by_routine_run_with_its_own_guide(monkeypatch, tmp_path):
    calls = []
    wire_runs(monkeypatch, tmp_path, calls, answer="read the guide; asked autolab")
    client = RunBoard(calls, {(RUN_CHANNEL, RUN_TOPIC): [origin_note(), opening()]})
    zulip_listener.handle_topic(client, RUN_CHANNEL, RUN_TOPIC)
    assert role_calls(calls) == [((RUN_CHANNEL, RUN_TOPIC), "routine_run")]
    prompt, cwd = runs(calls)[0][1], runs(calls)[0][2]
    assert "RUN GUIDE" in prompt and "FRONT GUIDE" not in prompt and "CHARACTER GUIDE" not in prompt
    # The record keeps its ids, and the origin note is not part of it.
    chatlog = (cwd / "chatlog.md").read_text()
    assert chatlog.startswith(f"# #{RUN_CHANNEL} › {RUN_TOPIC}\n")
    assert "[Front (you) #10]" in chatlog and "selfnote" not in chatlog
    # Served like an owned topic: ack, then the entry, both at home. A topic
    # holding only Front's own opening post is a run to serve, not an empty
    # topic to wave off.
    assert [c[1:3] for c in calls if c[0] == "reply"] == [(RUN_CHANNEL, RUN_TOPIC)] * 2
    assert replies(calls) == [SWEEP_ACK, "read the guide; asked autolab"]


def test_the_role_is_the_prefix():
    assert zulip_listener.role_for(RUN_CHANNEL, RUN_TOPIC) == "routine_run"
    assert zulip_listener.role_for("front", "front-desk-x") == "character_talk"
    assert zulip_listener.role_for("front", "front-routine-ghtrends-2026-09-07T07:00Z") == "front"


# --- starting ----------------------------------------------------------------


def test_unstarted_is_front_alone_with_no_serving_ack():
    assert routine.unstarted([origin_note(), opening()], BOT_ID)
    assert routine.unstarted([opening(), post(RUN_CHANNEL, RUN_TOPIC, "one more line", id=11)], BOT_ID)
    # The ack is what a serving posts first: started, even if the run then failed.
    assert not routine.unstarted([origin_note(), opening(), ack()], BOT_ID)
    assert not routine.unstarted([opening(), ack(), entry()], BOT_ID)
    # Somebody else's post is the owner route's business.
    assert not routine.unstarted([opening(id=10, sender_id=HUMAN_ID, name="Developer")], BOT_ID)
    # Nothing but notes is nothing.
    assert not routine.unstarted([origin_note()], BOT_ID)
    assert not routine.unstarted([], BOT_ID)


def test_opening_a_run_from_the_desk_starts_it_after_the_desk_reply(monkeypatch, tmp_path):
    """The desk serving opened the run (its root note names the desk); the
    owner sweep would never serve it, so the listener starts it as soon as
    the desk has replied — once, and as the run's own conversation."""
    calls = []
    wire_runs(monkeypatch, tmp_path, calls, answer="opened the run")
    client = RunBoard(calls, {
        (CHANNEL, DESK_TOPIC): [desk_message("ghtrends を回して")],
        (RUN_CHANNEL, RUN_TOPIC): [origin_note(), opening()],
    })
    zulip_listener.handle_topic(client, CHANNEL, DESK_TOPIC)
    assert role_calls(calls) == [((CHANNEL, DESK_TOPIC), "character_talk"),
                                 ((RUN_CHANNEL, RUN_TOPIC), "routine_run")]
    posted = [c[1:3] for c in calls if c[0] == "reply"]
    # desk ack, desk reply, then the run's ack and the run's entry
    assert posted == [(CHANNEL, DESK_TOPIC)] * 2 + [(RUN_CHANNEL, RUN_TOPIC)] * 2
    # The run's serving is its own: home is the run, and the desk is its origin, not a thread.
    assert runs(calls)[1][3] == (RUN_CHANNEL, RUN_TOPIC)


def test_a_started_run_is_not_started_again(monkeypatch, tmp_path):
    calls = []
    wire_runs(monkeypatch, tmp_path, calls)
    client = RunBoard(calls, {
        (CHANNEL, DESK_TOPIC): [desk_message("進捗は？")],
        (RUN_CHANNEL, RUN_TOPIC): [origin_note(), opening(), ack(), entry()],
    })
    zulip_listener.handle_topic(client, CHANNEL, DESK_TOPIC)
    assert role_calls(calls) == [((CHANNEL, DESK_TOPIC), "character_talk")]
    # The desk serving sees the run as a thread beside its chatlog.
    cwd = runs(calls)[0][2]
    assert (cwd / "threads" / RUN_CHANNEL / f"{RUN_TOPIC}.md").read_text().count("[Front (you)") == 2


def test_a_run_opened_by_a_callback_serving_is_started_too(monkeypatch, tmp_path):
    """The desk was called back by autolab, and that serving opened a run."""
    calls = []
    wire_runs(monkeypatch, tmp_path, calls)
    client = RunBoard(calls, {
        (CHANNEL, DESK_TOPIC): [desk_message("お願い")],
        ("work-old", "workrun-old"): [post("work-old", "workrun-old", rootchat_note(Conversation(CHANNEL, DESK_TOPIC)), id=3),
                                      answer(id=4, channel="work-old", topic="workrun-old", text="@**Front** the earlier thing is done")],
        (RUN_CHANNEL, RUN_TOPIC): [origin_note(), opening()],
    })
    zulip_listener.handle_mention(client, "work-old", "workrun-old")
    assert role_calls(calls) == [((CHANNEL, DESK_TOPIC), "character_talk"),
                                 ((RUN_CHANNEL, RUN_TOPIC), "routine_run")]


# --- resuming ----------------------------------------------------------------


def test_a_delegate_s_answer_resumes_the_run_not_the_requester(monkeypatch, tmp_path):
    """The run asked autolab; autolab's answer names Front in a topic whose
    root note names the **run**. The run is served, answers at home, and
    marks the callback served there. The desk hears nothing."""
    calls = []
    wire_runs(monkeypatch, tmp_path, calls, answer="autolab is done; ending next")
    client = RunBoard(calls, {
        (CHANNEL, DESK_TOPIC): [desk_message("お願い")],
        (RUN_CHANNEL, RUN_TOPIC): [origin_note(), opening(), ack(), entry()],
        (WORK_CHANNEL, WORK_TOPIC): [run_note(), answer()],
    })
    zulip_listener.handle_mention(client, WORK_CHANNEL, WORK_TOPIC)
    assert role_calls(calls) == [((RUN_CHANNEL, RUN_TOPIC), "routine_run")]
    cwd = runs(calls)[0][2]
    assert "commit a99625f" in (cwd / "threads" / WORK_CHANNEL / f"{WORK_TOPIC}.md").read_text()
    assert {c[1:3] for c in calls if c[0] == "reply"} == {(RUN_CHANNEL, RUN_TOPIC)}
    assert [c for c in calls if c[0] == "post"] == [
        ("post", RUN_CHANNEL, RUN_TOPIC, served_note(Conversation(WORK_CHANNEL, WORK_TOPIC), 21)),
    ]


def test_two_runs_each_get_their_own_callback(monkeypatch, tmp_path):
    other_run = "routinerun-20260909-1600"
    other_work = ("work-g-21", "workrun-task1-g-21")
    calls = []
    wire_runs(monkeypatch, tmp_path, calls)
    client = RunBoard(calls, {
        (RUN_CHANNEL, RUN_TOPIC): [origin_note(), opening(), ack(), entry()],
        (RUN_CHANNEL, other_run): [origin_note(channel=RUN_CHANNEL, topic=other_run),
                                   post(RUN_CHANNEL, other_run, OPENING, id=30), ack(id=31, topic=other_run), entry(id=32, topic=other_run)],
        (WORK_CHANNEL, WORK_TOPIC): [run_note(), answer()],
        other_work: [run_note(id=40, run=(RUN_CHANNEL, other_run), channel=other_work[0], topic=other_work[1]),
                     answer(id=41, channel=other_work[0], topic=other_work[1])],
    })
    zulip_listener.handle_mention(client, *other_work)
    assert role_calls(calls) == [((RUN_CHANNEL, other_run), "routine_run")]
    assert [c[1:3] for c in calls if c[0] in ("reply", "post")] == [(RUN_CHANNEL, other_run)] * 3


def test_a_callback_already_answered_is_not_served_again():
    """Restart recovery asks the chat which anchored topics await Front; a
    served mark at the answer's id is what keeps an answered callback from
    being served on every restart."""
    calls = []
    histories = {
        (RUN_CHANNEL, RUN_TOPIC): [origin_note(), opening(), ack(), entry()],
        (WORK_CHANNEL, WORK_TOPIC): [run_note(), answer()],
    }
    assert sweep_rootchats(RunBoard(calls, histories), BOT_ID, "Front") == [(WORK_CHANNEL, WORK_TOPIC)]
    histories[(RUN_CHANNEL, RUN_TOPIC)].append(
        post(RUN_CHANNEL, RUN_TOPIC, served_note(Conversation(WORK_CHANNEL, WORK_TOPIC), 21), id=13))
    assert sweep_rootchats(RunBoard(calls, histories), BOT_ID, "Front") == []
    # The run topic itself (anchored to the desk, last speaker Front) never
    # counts as waiting on anybody.
    assert (RUN_CHANNEL, RUN_TOPIC) not in sweep_rootchats(RunBoard(calls, histories), BOT_ID, "Front")


def test_recovery_starts_an_unstarted_run_after_a_restart(monkeypatch, tmp_path):
    started = "routinerun-20260909-1400"
    calls = []
    wire_runs(monkeypatch, tmp_path, calls)
    client = RunBoard(calls, {
        (RUN_CHANNEL, RUN_TOPIC): [origin_note(), opening()],
        (RUN_CHANNEL, started): [origin_note(channel=RUN_CHANNEL, topic=started),
                                 post(RUN_CHANNEL, started, OPENING, id=30), ack(id=31, topic=started)],
    })
    assert zulip_listener.recover_runs(client) == [(RUN_CHANNEL, RUN_TOPIC)]
    assert role_calls(calls) == [((RUN_CHANNEL, RUN_TOPIC), "routine_run")]


# --- finishing ---------------------------------------------------------------


def test_finishing_delivers_the_report_to_the_origin_and_resolves_the_run(monkeypatch, tmp_path):
    calls = []
    wire_runs(monkeypatch, tmp_path, calls, answer=FINISH)
    client = RunBoard(calls, {
        (CHANNEL, DESK_TOPIC): [desk_message("お願い")],
        (RUN_CHANNEL, RUN_TOPIC): [origin_note(), opening(), ack(), entry()],
    })
    zulip_listener.handle_topic(client, RUN_CHANNEL, RUN_TOPIC)
    # One post outside the run: the report, into the desk, naming the run —
    # and no root note before it.
    posts = [c for c in calls if c[0] == "post"]
    assert [c[1:3] for c in posts] == [(CHANNEL, DESK_TOPIC)]
    delivered = posts[0][3]
    assert "goal was reached" in delivered and "commit a99625f" in delivered
    assert f"#{RUN_CHANNEL} › `{RUN_TOPIC}`" in delivered and "selfnote" not in delivered
    # The record at home carries the canonical block, and the run is resolved after it.
    record = replies(calls)[-1]
    assert record.startswith("Autolab reported") and '"schema": "ag.routinerun-finish.v1"' in record
    assert client.resolved == [RUN_TOPIC]
    assert calls.index(("resolve", RUN_TOPIC, 12)) > max(i for i, c in enumerate(calls) if c[0] == "reply")


def test_a_broken_finish_block_keeps_the_run_open(monkeypatch, tmp_path):
    calls = []
    wire_runs(monkeypatch, tmp_path, calls,
              answer="ending\n\n```ag-routinerun\n{\"schema\": \"ag.routinerun-finish.v1\", \"achieved\": \"yes\"}\n```")
    client = RunBoard(calls, {
        (CHANNEL, DESK_TOPIC): [desk_message("お願い")],
        (RUN_CHANNEL, RUN_TOPIC): [origin_note(), opening(), ack(), entry()],
    })
    zulip_listener.handle_topic(client, RUN_CHANNEL, RUN_TOPIC)
    assert [c for c in calls if c[0] == "post"] == []
    assert client.resolved == []
    assert "```ag-routinerun-error\n`achieved` must be true or false" in replies(calls)[-1]


def test_a_run_opened_by_hand_reports_in_its_own_topic(monkeypatch, tmp_path):
    """No root note: the developer wrote the opening post. The owner route
    serves it, and the report has nowhere else to go."""
    calls = []
    wire_runs(monkeypatch, tmp_path, calls, answer=FINISH)
    client = RunBoard(calls, {
        (RUN_CHANNEL, RUN_TOPIC): [opening(sender_id=HUMAN_ID, name="Developer"), ack(), entry()],
    })
    zulip_listener.handle_topic(client, RUN_CHANNEL, RUN_TOPIC)
    assert [c for c in calls if c[0] == "post"] == []
    assert "Routine run finished" in replies(calls)[-1]
    assert client.resolved == [RUN_TOPIC]


def test_a_finish_block_never_carries_a_live_mention():
    reply, finish, error = routine.split_finish(
        "```ag-routinerun\n{\"schema\": \"ag.routinerun-finish.v1\", \"achieved\": false, "
        "\"reason\": \"@**Autolab** never answered\", \"report\": \"see @**Developer**\"}\n```")
    assert error is None and finish.reason == "Autolab never answered" and "@**" not in finish.report


# --- the guides --------------------------------------------------------------


def test_the_run_guide_exists_and_names_the_finish_block():
    text = zulip_listener.guide("routine_run", "guide.md")
    assert "ag-routinerun" in text and "ag.routinerun-finish.v1" in text
    assert "agentchat read" in text and "agentchat send" in text
    assert "agentchat wait" not in text


def test_the_requester_guides_say_how_to_open_a_run():
    for role in ("front", "character_talk"):
        text = zulip_listener.guide(role, "guide.md")
        assert "routinerun-" in text and "agentchat topics routine-" in text
        assert "guide" in text and "schedule" in text  # says there is none
        assert "rtschedule" not in text
