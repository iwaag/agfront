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
from agag.selfnote import last_real_message
from agag.zulip import rootchat_notes, served_marks

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


def anchored_awaiting(client, self_id, bot_name):
    """The topics Front anchored that are waiting on it: the last real post
    there is somebody else's, names Front, and is newer than the served
    mark — the rule `agag.listen.Listener.recover` applies from the mirror's
    index, spelled out over these fixtures' Zulip-shaped board."""
    marks = served_marks(client)
    found = []
    for (channel, topic), _home in rootchat_notes(client):
        last = last_real_message(client.topic_history(channel, topic, num_before=30))
        if last is None or last.get("sender_id") == self_id:
            continue
        if f"@**{bot_name}**" not in str(last.get("content", "")):
            continue
        if int(last.get("id") or 0) <= marks.get((channel, topic), 0):
            continue
        found.append((channel, topic))
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
    assert "RUN GUIDE" in prompt and "FRONT GUIDE" not in prompt and "DESK GUIDE" not in prompt
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
    assert zulip_listener.role_for("front", "front-desk-x") == "desk"
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
    assert role_calls(calls) == [((CHANNEL, DESK_TOPIC), "desk"),
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
    assert role_calls(calls) == [((CHANNEL, DESK_TOPIC), "desk")]
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
    assert role_calls(calls) == [((CHANNEL, DESK_TOPIC), "desk"),
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
    assert anchored_awaiting(RunBoard(calls, histories), BOT_ID, "Front") == [(WORK_CHANNEL, WORK_TOPIC)]
    histories[(RUN_CHANNEL, RUN_TOPIC)].append(
        post(RUN_CHANNEL, RUN_TOPIC, served_note(Conversation(WORK_CHANNEL, WORK_TOPIC), 21), id=13))
    assert anchored_awaiting(RunBoard(calls, histories), BOT_ID, "Front") == []
    # The run topic itself (anchored to the desk, last speaker Front) never
    # counts as waiting on anybody.
    assert (RUN_CHANNEL, RUN_TOPIC) not in anchored_awaiting(RunBoard(calls, histories), BOT_ID, "Front")


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
    # Two posts outside the run and both into the desk: the report, naming the
    # run, and — after it, so a crash between them loses the handoff and never
    # the report — the delivered note. No root note before either.
    posts = [c for c in calls if c[0] == "post"]
    assert [c[1:3] for c in posts] == [(CHANNEL, DESK_TOPIC), (CHANNEL, DESK_TOPIC)]
    delivered, handoff = posts[0][3], posts[1][3]
    assert "goal was reached" in delivered and "commit a99625f" in delivered
    assert f"#{RUN_CHANNEL} › `{RUN_TOPIC}`" in delivered and "selfnote" not in delivered
    assert routine.parse_delivered(handoff) == Conversation(RUN_CHANNEL, RUN_TOPIC)
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
    for role in ("front", "desk"):
        text = zulip_listener.guide(role, "guide.md")
        assert "routinerun-" in text and "agentchat topics routine-" in text
        assert "guide" in text and "schedule" in text  # says there is none
        assert "rtschedule" not in text


# --- a run honours the execution preference it was opened with (step4) -----

from agag.execopt import Selection  # noqa: E402


def exec_command(option, bot="Front"):
    return f"@**{bot}** use {option}"


def test_a_run_topic_carries_its_own_execution_selection(monkeypatch, tmp_path):
    """The run is a conversation of Front's own, so a command in it selects
    how Front drives the run — separately from what Front asks of the agents
    it delegates to, which is settled in *their* topics."""
    calls = []
    wire_runs(monkeypatch, tmp_path, calls)
    history = [
        origin_note(),
        opening(),
        post(RUN_CHANNEL, RUN_TOPIC, exec_command("agy"), id=12,
             sender_id=HUMAN_ID, name="Developer"),
        entry(id=13),
        post(RUN_CHANNEL, RUN_TOPIC, "carry on", id=14,
             sender_id=HUMAN_ID, name="Developer"),
    ]
    client = RunBoard(calls, {(RUN_CHANNEL, RUN_TOPIC): history})
    zulip_listener.handle_topic(client, RUN_CHANNEL, RUN_TOPIC)
    call = runs(calls)[0]
    assert call[4] == zulip_listener.ROUTINE_ROLE
    assert call[6].option == "agy"


# --- continuing the request a finished run reported into ---------------------
#
# `routine_tests` p1 step 1. The delivery is Front's own post, so the sweeps
# will never look at the requester's conversation again — a request made of
# more than one routine had nobody left to start its next stage. What is
# pinned here is the handoff and its bounds, never what Front decided with it.


class LiveRunBoard(RunBoard):
    """A board whose posts land in the histories, so a whole chain can run.

    `RunBoard` is a snapshot; the continuation is about what the *next* read
    of a conversation finds, so the delivery, the note and Front's own reply
    have to be there when it looks.
    """

    def __init__(self, calls, histories, board=None):
        super().__init__(calls, histories, board=board)
        self.next_id = 1000

    def channel_topics(self, stream_id):
        """The topics these fixtures actually hold, so the `✔ ` rename is
        visible to `live_topic_name` the way it is in the realm."""
        return [topic for (_channel, topic) in self.histories] + list(self.board)

    def append(self, channel, topic, content, sender_id=BOT_ID, name="Front"):
        self.next_id += 1
        self.histories.setdefault((channel, topic), []).append(
            post(channel, topic, content, id=self.next_id, sender_id=sender_id, name=name)
        )
        return self.next_id

    def send_to_channel(self, channel, topic, content):
        self.calls.append(("post", channel, topic, content))
        return self.append(channel, topic, content)

    def resolve_topic(self, message_id, topic):
        super().resolve_topic(message_id, topic)
        for (channel, name) in list(self.histories):
            if name == topic:
                self.histories[(channel, f"✔ {topic}")] = self.histories.pop((channel, name))


def wire_live(monkeypatch, tmp_path, calls, client_box, **kw):
    """`wire_runs`, with replies appended to the board as well as recorded.

    A reply is speech, and speech is what disarms the handoff, so a test that
    only records replies cannot tell "served once" from "never stops".
    """
    wire_runs(monkeypatch, tmp_path, calls, **kw)
    from agag import topics as shared_topics

    def reply(client, channel, topic, text, **kwargs):
        calls.append(("reply", channel, topic, text))
        if client_box:
            client_box[0].append(channel, topic, text)
        return 900

    monkeypatch.setattr(shared_topics, "deliver", reply)


def desk_runs(calls):
    """The servings of the requester's conversation, by role and home."""
    return [c for c in calls if c[0] == "front" and c[3] == (CHANNEL, DESK_TOPIC)]


def finished_run_board(calls, *, finish=FINISH, desk_topic=DESK_TOPIC):
    return LiveRunBoard(calls, {
        (CHANNEL, desk_topic): [desk_message("お願い")],
        (RUN_CHANNEL, RUN_TOPIC): [origin_note(origin=(CHANNEL, desk_topic)),
                                   opening(), ack(), entry()],
    })


def test_a_finished_run_serves_the_conversation_that_asked_for_it(monkeypatch, tmp_path):
    """The whole defect, in one test: without the handoff the desk is never
    served again and a second stage is never started."""
    calls = []
    box = []
    wire_live(monkeypatch, tmp_path, calls, box, answer=FINISH)
    client = finished_run_board(calls)
    box.append(client)
    zulip_listener.handle_topic(client, RUN_CHANNEL, RUN_TOPIC)
    assert len(desk_runs(calls)) == 1
    # An ordinary serving of the requester: its own role, its own conversation.
    assert desk_runs(calls)[0][4] == zulip_listener.DESK_ROLE
    # And it happened after the run's own record, never before it.
    assert calls.index(desk_runs(calls)[0]) > max(
        i for i, c in enumerate(calls) if c[0] == "reply" and c[2] == RUN_TOPIC)


def test_the_requester_is_served_once_and_never_answers_itself(monkeypatch, tmp_path):
    """Front's reply is speech, and speech spends the handoff. A request that
    asked for one routine ends here rather than replying to itself forever."""
    calls = []
    box = []
    wire_live(monkeypatch, tmp_path, calls, box, answer=FINISH)
    client = finished_run_board(calls)
    box.append(client)
    zulip_listener.handle_topic(client, RUN_CHANNEL, RUN_TOPIC)
    assert len(desk_runs(calls)) == 1
    assert zulip_listener.continue_deliveries(client) == []
    assert len(desk_runs(calls)) == 1


def test_a_run_that_missed_its_goal_still_returns_to_the_requester(monkeypatch, tmp_path):
    """`achieved: false` is a completion too. A request whose first stage
    failed must reach the conversation that can say what to do about it."""
    calls = []
    box = []
    failed = FINISH.replace('"achieved": true', '"achieved": false')
    wire_live(monkeypatch, tmp_path, calls, box, answer=failed)
    client = finished_run_board(calls)
    box.append(client)
    zulip_listener.handle_topic(client, RUN_CHANNEL, RUN_TOPIC)
    assert "ended without reaching" in [c for c in calls if c[0] == "post"][0][3]
    assert len(desk_runs(calls)) == 1


def test_a_restart_between_the_report_and_the_continuation_loses_neither(monkeypatch, tmp_path):
    """The delivery landed and the listener went down before serving the
    requester. Nothing sweeps that conversation — Front spoke last — so
    startup recovery is the only thing that can find it."""
    calls = []
    box = []
    wire_live(monkeypatch, tmp_path, calls, box, answer="the backlog is published; opening the next run")
    client = LiveRunBoard(calls, {
        (CHANNEL, DESK_TOPIC): [desk_message("お願い")],
        (RUN_CHANNEL, f"✔ {RUN_TOPIC}"): [origin_note(topic=f"✔ {RUN_TOPIC}"),
                                          opening(), ack(), entry()],
    })
    box.append(client)
    client.append(CHANNEL, DESK_TOPIC, "**Routine run finished** — the routine's goal was reached.")
    client.append(CHANNEL, DESK_TOPIC, routine.delivered_note(Conversation(RUN_CHANNEL, RUN_TOPIC)))
    assert zulip_listener.recover_runs(client) == [(CHANNEL, DESK_TOPIC)]
    assert len(desk_runs(calls)) == 1


def test_an_ack_does_not_spend_the_handoff():
    """A crash between a serving's ack and its reply leaves the handoff owed.
    The ack is our own transport noise, not the serving it promises."""
    history = [
        desk_message("お願い"),
        post(CHANNEL, DESK_TOPIC, "**Routine run finished** — …", id=2),
        post(CHANNEL, DESK_TOPIC, routine.delivered_note(Conversation(RUN_CHANNEL, RUN_TOPIC)), id=3),
    ]
    pending = Conversation(RUN_CHANNEL, RUN_TOPIC)
    assert routine.awaiting_continuation(history, BOT_ID) == pending
    assert routine.awaiting_continuation(history + [ack(id=4, channel=CHANNEL, topic=DESK_TOPIC)],
                                         BOT_ID) == pending
    spoken = history + [ack(id=4, channel=CHANNEL, topic=DESK_TOPIC),
                        post(CHANNEL, DESK_TOPIC, "stage A is done; opening stage B", id=5)]
    assert routine.awaiting_continuation(spoken, BOT_ID) is None


def test_the_developer_s_own_next_post_spends_the_handoff():
    """They spoke, so the owner sweep serves the conversation by the ordinary
    route. Continuing it here as well would buy the same run twice."""
    history = [
        desk_message("お願い"),
        post(CHANNEL, DESK_TOPIC, "**Routine run finished** — …", id=2),
        post(CHANNEL, DESK_TOPIC, routine.delivered_note(Conversation(RUN_CHANNEL, RUN_TOPIC)), id=3),
        post(CHANNEL, DESK_TOPIC, "good — now do the second one", id=4,
             sender_id=HUMAN_ID, name="Developer"),
    ]
    assert routine.awaiting_continuation(history, BOT_ID) is None


def test_two_reports_into_one_conversation_serve_it_once(monkeypatch, tmp_path):
    """Duplicate delivery, or two runs of one request finishing together: the
    requester is a conversation, and a conversation is served once."""
    calls = []
    box = []
    wire_live(monkeypatch, tmp_path, calls, box, answer=FINISH)
    second = "routinerun-20260909-1600"
    client = LiveRunBoard(calls, {
        (CHANNEL, DESK_TOPIC): [desk_message("お願い")],
        (RUN_CHANNEL, RUN_TOPIC): [origin_note(), opening(), ack(), entry()],
        (RUN_CHANNEL, second): [origin_note(id=30, topic=second),
                                opening(id=31), ack(id=32, topic=second),
                                entry(id=33, topic=second)],
    })
    box.append(client)
    client.append(CHANNEL, DESK_TOPIC, "**Routine run finished** — …")
    client.append(CHANNEL, DESK_TOPIC, routine.delivered_note(Conversation(RUN_CHANNEL, second)))
    zulip_listener.handle_topic(client, RUN_CHANNEL, RUN_TOPIC)
    assert len(desk_runs(calls)) == 1


def test_a_resolved_requester_is_not_reopened(monkeypatch, tmp_path):
    """Somebody closed the conversation. A report it has already seen is no
    reason to post into it again."""
    calls = []
    box = []
    wire_live(monkeypatch, tmp_path, calls, box, answer="…")
    client = LiveRunBoard(calls, {
        (CHANNEL, f"✔ {DESK_TOPIC}"): [desk_message("お願い")],
        (RUN_CHANNEL, RUN_TOPIC): [origin_note(), opening(), ack(), entry()],
    })
    box.append(client)
    client.append(CHANNEL, f"✔ {DESK_TOPIC}", "**Routine run finished** — …")
    client.append(CHANNEL, f"✔ {DESK_TOPIC}",
                  routine.delivered_note(Conversation(RUN_CHANNEL, RUN_TOPIC)))
    assert zulip_listener.continue_deliveries(client) == []
    assert desk_runs(calls) == []


def test_a_run_opened_by_hand_writes_no_handoff(monkeypatch, tmp_path):
    """There is no requester: the report stays in the run's own topic, and a
    note about a conversation that does not exist would be a lie."""
    calls = []
    box = []
    wire_live(monkeypatch, tmp_path, calls, box, answer=FINISH)
    client = LiveRunBoard(calls, {
        (RUN_CHANNEL, RUN_TOPIC): [opening(sender_id=HUMAN_ID, name="Developer"), ack(), entry()],
    })
    box.append(client)
    zulip_listener.handle_topic(client, RUN_CHANNEL, RUN_TOPIC)
    assert [c for c in calls if c[0] == "post"] == []
    assert zulip_listener.continue_deliveries(client) == []


def test_the_chain_of_stages_is_bounded(monkeypatch, tmp_path):
    """The handoff is generic, so nothing in it knows a request is finite.
    The depth bound is what stops a pathological chain inside one serving;
    the next event picks up whatever is left."""
    calls = []
    box = []
    wire_live(monkeypatch, tmp_path, calls, box, answer=FINISH)
    client = finished_run_board(calls)
    box.append(client)
    assert zulip_listener.continue_deliveries(
        client, depth=zulip_listener.CONTINUATION_DEPTH) == []
    assert desk_runs(calls) == []


# --- a callback for a run that has already ended -----------------------------
#
# `routine_tests` p1 step 3, three times live. Ending a run resolves its topic,
# and resolving *renames* it; the callback route then read the name the root
# note recorded, found nothing under it, and posted — forking a twin beside the
# real conversation, without the origin note, so the twin's report could never
# be delivered to anybody.


def resolved_run_board(calls, *, origin=True, origin_resolved=False):
    desk = f"✔ {DESK_TOPIC}" if origin_resolved else DESK_TOPIC
    run_history = [opening(), ack(), entry()]
    if origin:
        run_history.insert(0, origin_note(topic=f"✔ {RUN_TOPIC}"))
    return LiveRunBoard(calls, {
        (CHANNEL, desk): [desk_message("お願い")],
        (RUN_CHANNEL, f"✔ {RUN_TOPIC}"): run_history,
        (WORK_CHANNEL, WORK_TOPIC): [run_note(), answer()],
    })


def test_a_callback_into_a_finished_run_forks_no_twin(monkeypatch, tmp_path):
    calls = []
    box = []
    wire_live(monkeypatch, tmp_path, calls, box, answer="should never run")
    client = resolved_run_board(calls)
    box.append(client)
    zulip_listener.handle_mention(client, WORK_CHANNEL, WORK_TOPIC)
    # Nothing was served, and above all nothing was posted under the bare name.
    assert runs(calls) == []
    assert (RUN_CHANNEL, RUN_TOPIC) not in client.histories
    assert not any(c[0] in ("post", "reply") and c[2] == RUN_TOPIC for c in calls)


def test_a_late_answer_reaches_the_request_that_asked_for_the_run(monkeypatch, tmp_path):
    """The run is over, but the request may not be. The answer is real work,
    so the conversation that can decide about it is told."""
    calls = []
    box = []
    wire_live(monkeypatch, tmp_path, calls, box, answer="…")
    client = resolved_run_board(calls)
    box.append(client)
    zulip_listener.handle_mention(client, WORK_CHANNEL, WORK_TOPIC)
    posts = [c for c in calls if c[0] == "post" and c[1:3] == (CHANNEL, DESK_TOPIC)]
    assert len(posts) == 2
    assert "already ended" in posts[0][3] and WORK_TOPIC in posts[0][3]
    assert routine.parse_delivered(posts[1][3]) == Conversation(RUN_CHANNEL, RUN_TOPIC)
    # …and that note is what brings the requester back for a decision.
    assert routine.awaiting_continuation(
        client.histories[(CHANNEL, DESK_TOPIC)], BOT_ID
    ) == Conversation(RUN_CHANNEL, RUN_TOPIC)


def test_a_late_answer_is_marked_served_so_a_restart_is_quiet(monkeypatch, tmp_path):
    calls = []
    box = []
    wire_live(monkeypatch, tmp_path, calls, box, answer="…")
    client = resolved_run_board(calls)
    box.append(client)
    zulip_listener.handle_mention(client, WORK_CHANNEL, WORK_TOPIC)
    marks = anchored_awaiting(client, BOT_ID, "Front")
    assert (WORK_CHANNEL, WORK_TOPIC) not in marks


def test_a_late_answer_for_a_finished_request_is_recorded_nowhere(monkeypatch, tmp_path):
    """Both ends closed. There is nobody to tell, and inventing somewhere to
    post it would be worse than saying so in the log."""
    calls = []
    box = []
    wire_live(monkeypatch, tmp_path, calls, box, answer="…")
    client = resolved_run_board(calls, origin_resolved=True)
    box.append(client)
    zulip_listener.handle_mention(client, WORK_CHANNEL, WORK_TOPIC)
    assert [c for c in calls if c[0] == "post" and c[1] == CHANNEL] == []
    assert runs(calls) == []


def test_a_finished_conversation_that_is_not_a_run_is_simply_left(monkeypatch, tmp_path):
    """The rule is about resolved conversations, not about runs. A finished
    `front-` conversation is not reopened either, and has no origin to tell."""
    calls = []
    box = []
    wire_live(monkeypatch, tmp_path, calls, box, answer="…")
    client = LiveRunBoard(calls, {
        (CHANNEL, f"✔ {DESK_TOPIC}"): [desk_message("お願い")],
        (WORK_CHANNEL, WORK_TOPIC): [
            post(WORK_CHANNEL, WORK_TOPIC, rootchat_note(Conversation(CHANNEL, DESK_TOPIC)), id=20),
            answer(),
        ],
    })
    box.append(client)
    zulip_listener.handle_mention(client, WORK_CHANNEL, WORK_TOPIC)
    assert runs(calls) == []
    # Only the served mark, which is a selfnote and buys nobody a run.
    posted = [c for c in calls if c[0] == "post"]
    assert [routine.parse_delivered(c[3]) for c in posted] == [None]
    assert all(c[3].startswith("[selfnote][served]") for c in posted)
