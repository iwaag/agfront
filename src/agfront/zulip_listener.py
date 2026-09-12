"""Serving a `front-*` topic: two files, one run, one reply at home.

The listener is `agfront.listener` (`agag.agent.listener_main` over Front's
`SPEC`); `agag.topics.serve_topic` serves each swept topic — ack, numbered
generation workspace, `chatlog.md`, the run, always reply, then re-check for
posts that arrived during the run.

What is agfront's own is now two files and no routing at all: `chatlog.md`,
the conversation, and `tools/agents.md`, the other agents' own introductions
harvested from `#agents` for this run. Front reads those, decides whether
anybody can serve the request, asks the Developer, and — if permitted —
speaks to that agent itself with `agentchat`.

**No outbound route lives here any more.** Until p2 the run wrote `create.md`
and *this* handler posted it into an asset-request topic in `#general` whose
name it derived itself, because Front was not allowed to name a channel. That was a shackle around an agent
that could not read the board; now it can. Which agent, which channel and
which topic prefix are things Front learns by reading, which is precisely the
capability this phase exists to demonstrate — so grepping this file for
another agent's channel must find nothing.

Multi-turn needs no code: the guide asks permission before acting, and
`serve_topic` re-checks the topic after each run, so the Developer's answer
is simply the next serving.

**Since `agent_standardize` p7 a supervision is not a long run — it is
several short ones.** Front posts into another agent's topic and finishes;
when that agent's reply names Front, `sweep_serve`'s mention route serves
this `front-*` conversation again with the remote thread beside its chatlog,
and Front answers there. Nothing blocks, nothing is backgrounded, and a run
that ends is not a supervision that stopped.

**Since p8 the memory of that is in the chat.** `agentchat send` anchors the
topic it posts into with `[selfnote][rootchat] <channel>/<topic>`, naming the
`front-*` conversation the run was serving, so this listener asks the topic
which home to serve instead of asking a ledger file — and a called-back run
**answers at home**, where the guide's first line ("your reply goes to the
developer") is true. Anything Front wants to say to the other agent is a
deliberate `agentchat send`, never a reply by reflex.

**Since `front_desk` p1 a conversation chooses its role.** A `front-desk-…`
topic — the graphic-novel screen in agdevworld — is served by the
`character_talk` role with its own guide and profile; every other `front-*`
topic is served by `front` as before. The choice is made from the **home**
conversation, so a callback into a Front Desk conversation is answered in the
same voice the conversation was opened in. Nothing else differs: same files,
same `agentchat`, same reply-at-home.

**Since `front_desk` p2 a Front Desk run is given its characters and its
evidence.** The character definition is no longer in the guide: the settings
repository agdevworld syncs (`agfront.settings`) is pinned to one revision at
the start of the serving and copied into the workspace as `characters.md` —
every character's whole lore, and which agents speak as which — so a sync
that lands mid-run changes the next run, not this one. The chatlog and the
threads of that run are rendered by `agfront.evidence` rather than the shared
`format_chatlog`: same conversations, with the message ids, sender ids and
topic names kept, so a line given to another character can cite the post it
came from; a bounded or unreadable thread says so in the file. The pinned
revision is stamped into the run record.

**Since `front_desk` p2 step 3 a Front Desk reply may carry a dialogue.**
The run ends its reply with a fenced `ag-dialogue` JSON block — a few turns
by the characters of the pinned revision, the other agents' lines drawn from
the threads — and `agfront.dialogue` validates and re-serializes it before
the post, stamping the revision. An unusable block becomes an
`ag-dialogue-error` fence after the reply, never a re-run.

**Since `routine_tests` p2 ex1 the conversation is in the prompt.** It used
to be only `chatlog.md` — a file the run had to decide to open — and a reply
produced in one turn with no tool calls therefore saw nothing and said so,
twice out of p2's first two requests, with the request sitting verbatim in
that file. `serve` now renders the conversation once and gives those same
bytes to both the file and the prompt (`agag.topics.conversation_context`),
for all three roles. The file stays: it is the complete copy, and the
prompt's is bounded and says so.

**Since p9 a served callback is marked.** Answering at home means Front never
becomes the last poster in the topic that called it, so recovery would find
that topic still naming Front and serve the exchange again on every restart.
`note_served` writes `[selfnote][served] <channel>/<topic> <message id>` into
the `front-*` conversation, and the startup sweep skips anything at or below
the mark.
"""

from __future__ import annotations

import json
from pathlib import Path

from agag.agent import SWEEP_ACK as ACK_TEXT, exec_options_for, is_ack, run_role
from agag.entrance import EMPTY_REPLY
from agag.topics import (
    HISTORY_MESSAGES,
    TopicResult,
    chatlog_placement,
    chatlog_path,
    conversation_context,
    format_chatlog,
    generation_dir,
    guide as shared_guide,
    next_generation,
    next_record_path,
    prompt_with_guide,
    serve_topic,
    threads_placement,
    topic_workspace,
    write_threads,
)
from agag.execopt import Selection
from agag.intro import write_agents_md
from agag.selfnote import Conversation
from agag.zulip import (
    LAST_SPEAKER_LOOKBACK,
    RESOLVED_TOPIC_PREFIX,
    ZulipClient,
    live_topic_name,
    log,
    note_served,
    remotes_for_home,
    rootchat_home,
)

from .budget import write_budget_doc
from .dialogue import finish_reply
from .evidence import format_evidence, write_evidence_threads
from .instance import SPEC
from .routine import (
    ROUTINE_ROLE,
    delivered_note,
    delivery_text,
    late_answer_text,
    is_run_topic,
    opened_runs,
    origin_of,
    pending_continuations,
    record_text,
    recover_unstarted_runs,
    split_finish,
    unstarted,
)
from .settings import SettingsUnavailable, characters_markdown, pin


#: The Front Desk's conversations: `#front` › `front-desk-<id>`. Inside the
#: `front-` sweep, so no listener change routes them; only the role differs.
FRONT_DESK_PREFIX = "front-desk-"
CHARACTER_ROLE = "character_talk"
FRONT_ROLE = "front"

# The skeleton's paths, named here so a test can point a serving elsewhere.
ZULIP_ENV = SPEC.zulip_env
TOPICS_ROOT = SPEC.topics_root
GUIDES = SPEC.guides
RECORDS_ROOT = SPEC.records_root

# Front reads a conversation, reads the board, and posts a message or two. It
# generates, builds and runs nothing, and it no longer waits for anybody:
# 360 s, the pre-p5 ceiling, restored in `agent_standardize` p7.
#
# p5 raised this to 3600 so a supervising run could block on `agentchat wait`.
# p6 showed the ceiling was never the binding constraint — the run that
# failed used 242 s of it and ended itself. Supervision is now several short
# servings, each brought back by a mention, so there is nothing left for an
# hour-long budget to buy. What it did cost was real: this listener is
# serial, so a supervising run was also how long the Developer's next post
# waited to be answered.
FRONT_TIMEOUT_SECONDS = 360

#: How many stage handoffs one chain of servings may make before it stops and
#: leaves the rest to the next event. A composite request is sequential — each
#: stage needs a run that is opened, delegated and answered, so in practice the
#: chain unwinds long before this — and the bound is here so that a routine
#: that somehow finished on sight could not recurse without end.
CONTINUATION_DEPTH = 8

__all__ = [
    "CHARACTER_ROLE",
    "CONTINUATION_DEPTH",
    "FRONT_DESK_PREFIX",
    "FRONT_ROLE",
    "ROUTINE_ROLE",
    "SPEC",
    "ZULIP_ENV",
    "ListenerError",
    "characters_placement",
    "continue_deliveries",
    "front_prompt",
    "guide",
    "handle_mention",
    "handle_topic",
    "relay_late_answer",
    "recover_runs",
    "role_for",
    "run_front",
    "serve",
    "start_opened_runs",
]


class ListenerError(RuntimeError):
    """One front-topic workflow could not complete."""


def guide(*parts: str) -> str:
    return shared_guide(GUIDES, *parts)


def role_for(channel: str, topic: str) -> str:
    """Which role serves this conversation: the Front Desk voice for a
    `front-desk-…` topic, the ordinary front for every other `front-*` one.

    Decided from the conversation being served — the *home* — never from the
    topic a mention arrived in, so a callback keeps the voice of the
    conversation it belongs to.
    """
    del channel  # the prefix is the whole rule; `#front` is where the sweep looks
    if is_run_topic(topic):
        return ROUTINE_ROLE
    return CHARACTER_ROLE if topic.startswith(FRONT_DESK_PREFIX) else FRONT_ROLE


def characters_placement(revision: str | None, reason: str | None = None) -> str:
    """One line saying where the characters are — or that they are not.

    A run without settings is told so in the placement rather than handed a
    guide that presumes a file: the guide then says what to do in that case.
    """
    if revision:
        return (f"The characters are placed beside it in \"characters.md\" "
                f"(settings revision {revision}); the section marked as you is who you are.")
    return f"No character settings are available for this run ({reason or 'unknown reason'})."


def front_prompt(
    bot_name: str, threads=(), root: Path | None = None, role: str = FRONT_ROLE,
    *, characters: str | None = None, conversation: str = "",
) -> str:
    """The conversation, the placement lines, then the role's guide.

    Placement says where the files are; the guide says what to produce. The
    threads line only appears when there are threads, so a first request
    never carries a sentence about files that are not there. The guide is the
    role's own (`agent/guides/<role>/guide.md`): the voice is defined there,
    not by the role's name. `characters` is the Front Desk's placement line
    for `characters.md`, present only for that role.

    `conversation` is the rendered chatlog of *this* serving — the same bytes
    written to `chatlog.md`, carried in the prompt by
    `agag.topics.conversation_context`. Before `routine_tests` p2 ex1 the
    conversation reached a run only as a file it had to decide to open, and a
    one-turn reply with no tool calls answered a real request as though the
    topic were empty. The file stays: it is complete, and the prompt copy is
    bounded. All three roles get it — a Front Desk turn, a `front-*` request
    and a routine run are all servings of a conversation.
    """
    lines = [chatlog_placement(bot_name)]
    if placement := threads_placement(threads, root or Path(".")):
        lines.append(placement)
    if characters:
        lines.append(characters)
    if conversation:
        lines.append("")
        lines.append(conversation)
    return prompt_with_guide(lines, guide(role, "guide.md"))


def run_front(
    prompt: str, cwd: Path, home: tuple[str, str], role: str = FRONT_ROLE,
    *, extra_meta: dict | None = None, selection: Selection | None = None,
) -> str:
    """One run of `role` in the topic workspace, with its `ag.agent-run.v1` record.

    `home` is the `front-*` conversation being served. Anything this run
    posts elsewhere is recorded against it, so the answer comes back here.
    The record is filed under the role, so a Front Desk run is told apart
    from an ordinary front run by where its record is. `extra_meta` is
    stamped into the record — the settings revision a Front Desk run drew on.
    """
    record = next_record_path(RECORDS_ROOT / role)
    output, _, exit_code = run_role(
        SPEC,
        role,
        prompt,
        cwd=cwd,
        timeout=FRONT_TIMEOUT_SECONDS,
        record=record,
        home=home,
        extra_meta=extra_meta or None,
        selection=selection,
    )
    if exit_code != 0:
        raise ListenerError(f"{role} run exited {exit_code}: {output.strip()[:500]}")
    return output.strip()


def serve(context) -> TopicResult:
    """agfront's part of one serving: give the run its files, then run.

    Three kinds of file now: the conversation being served (`chatlog.md`),
    the conversations Front has taken part in elsewhere (`threads/`), and the
    board (`tools/agents.md`). One of the threads is usually why this run is
    happening at all. A Front Desk serving gets a fourth, `characters.md`,
    and its chatlog and threads keep their message ids (`agfront.evidence`).
    """
    role = role_for(context.channel, context.topic)
    desk = role == CHARACTER_ROLE
    run = role == ROUTINE_ROLE
    # A run's chatlog is its own record and its threads are its evidence:
    # both keep their message ids, like the Front Desk's.
    evidence = desk or run
    number = next_generation(topic_workspace(TOPICS_ROOT, context.channel, context.topic))
    front_dir = generation_dir(TOPICS_ROOT, context.channel, context.topic, number, role)

    settings = None
    settings_note = None
    if desk:
        # Pinned before anything else is written, so every file of this
        # serving is of one revision.
        context.step = "settings"
        try:
            settings = pin()
        except SettingsUnavailable as error:
            settings_note = str(error)
            log(f"character settings unavailable: {error}")
        else:
            (front_dir / "characters.md").write_text(characters_markdown(settings), encoding="utf-8")
            (front_dir / "settings.json").write_text(
                json.dumps({"schema": "ag.frontdesk-settings.v1", "revision": settings.revision,
                            "root": str(settings.root),
                            "characters": {c.id: {"name": c.name, "nickname": c.nickname,
                                                  "agents": list(c.agents)}
                                           for c in settings.characters.values()}},
                           ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )

    context.step = "chatlog"
    # Rendered once. The same bytes are the file and the prompt's copy, so a
    # run can never be shown two versions of one conversation.
    if evidence:
        chatlog = format_evidence(context.history, context.self_id, channel=context.channel,
                                  topic=context.topic, drop=is_ack,
                                  bounded=len(context.history) >= HISTORY_MESSAGES,
                                  history_messages=HISTORY_MESSAGES)
    else:
        chatlog = format_chatlog(context.history, context.self_id, drop=is_ack)
    chatlog_path(front_dir).write_text(chatlog, encoding="utf-8")

    context.step = "threads"
    remotes = [
        conversation.as_pair()
        for conversation in remotes_for_home(context.client, context.channel, context.topic)
    ]
    # A callback reached through the `replaces` relation comes from a topic
    # Front has never posted in, so no root note of ours names it and
    # `remotes_for_home` cannot find it — while its text is the whole reason
    # this serving is happening (`routine_tests` p2 ex1, problem A).
    for pair in getattr(context, "extra_threads", ()):
        if pair not in remotes:
            remotes.append(pair)
    if evidence:
        threads = write_evidence_threads(context.client, front_dir, remotes, context.self_id, drop=is_ack)
    else:
        threads = write_threads(context.client, front_dir, remotes, context.self_id, drop=is_ack)

    context.step = "harvest"
    write_agents_md(context.client, front_dir)
    if run:
        # The observation a run judges its conditions against, at the start
        # of this serving; `agbudget` re-reads it. A failed read is written
        # as a failed read (`agfront.budget`), never skipped.
        context.step = "budget"
        write_budget_doc(front_dir)

    context.step = role
    output = run_front(
        front_prompt(
            context.bot_name, threads, front_dir, role,
            characters=(characters_placement(settings.revision if settings else None, settings_note)
                        if desk else None),
            conversation=conversation_context(chatlog),
        ),
        front_dir,
        (context.channel, context.topic),
        role,
        extra_meta={"settings_revision": settings.revision} if settings else None,
        # Front's own execution option, frozen for this serving. Asking
        # another agent to run *its* work a certain way is a different
        # decision, made in that agent's own topic (`agentchat use`).
        selection=context.selection,
    )
    if run:
        return finish_run(context, output)
    if not desk:
        return TopicResult([output])
    # The Front Desk's post is the reply plus, when the run wrote one, its
    # dialogue block — validated against the pinned revision and
    # re-serialized here, so the screen never parses what a run improvised.
    context.step = "dialogue"
    text, dialogue, error = finish_reply(output, settings, workspace=front_dir, log=log)
    if dialogue is not None:
        log(f"dialogue: {len(dialogue.turns)} turns by {', '.join(dialogue.characters)} "
            f"at settings {dialogue.settings_revision[:12]}")
    return TopicResult([text])


def finish_run(context, output: str) -> TopicResult:
    """A run serving's post, and — when the run said it ends — its delivery.

    The reply is the run's own record and is posted at home whatever else
    happens. A usable `ag-routinerun` block ends the run: the report goes to
    the conversation that opened the run (the origin the topic's root note
    names; the run topic itself when it was opened by hand), naming the run,
    and the run topic is resolved after the record. An unusable block is
    recorded as such and the run stays open.
    """
    context.step = "finish"
    reply, finish, error = split_finish(output)
    if error is not None:
        log(f"finish block unusable: {error}")
    if finish is None:
        return TopicResult([record_text(reply, None, error)])
    run = Conversation(context.channel, context.topic)
    origin = origin_of(context.history, context.self_id)
    if origin is not None and origin != run:
        context.step = "delivery"
        # Directly, never through `agentchat`: a root note pointing at the
        # run must not be written into the requester's conversation.
        name = live_topic_name(context.client, origin.channel, origin.topic)
        context.client.send_to_channel(origin.channel, name, delivery_text(finish, run))
        # …and, right after it, the note that says a report is sitting there
        # unread. The delivery is Front's own speech, so the sweeps will never
        # serve that conversation again on their own; `continue_deliveries`
        # is what does, and this note is the whole of its memory. Written
        # after the report so a crash between the two leaves the requester
        # with the report and no handoff, rather than a handoff and no report.
        context.client.send_to_channel(origin.channel, name, delivered_note(run))
        log(f"delivered the run's report to {origin}")
        record = record_text(reply, finish, None)
    else:
        record = "\n\n".join(s for s in (record_text(reply, finish, None), delivery_text(finish, run)) if s)
    return TopicResult([record], resolve_after=True)


def handle_topic(client: ZulipClient, channel: str, topic: str, *, depth: int = 0) -> None:
    """Serve one awaiting front topic through the shared skeleton.

    A run topic is served without the empty-topic guard: its opening post is
    Front's own, and a topic holding nothing but Front's speech is exactly a
    run waiting to start, not a topic with nothing in it. Afterwards, any
    run this conversation opened is started — and, when this *was* a run, any
    requester it has just reported into is served.
    """
    log(f"front topic {channel!r}/{topic!r}")
    serve_topic(
        client, channel, topic, serve, ack_text=ACK_TEXT,
        empty_reply=None if is_run_topic(topic) else EMPTY_REPLY,
        exec_options=exec_options_for(SPEC, client),
    )
    start_opened_runs(client, (channel, topic), depth=depth)
    if is_run_topic(topic):
        continue_deliveries(client, depth=depth)


def start_opened_runs(
    client: ZulipClient, home: tuple[str, str], *, depth: int = 0
) -> list[tuple[str, str]]:
    """Start every run the conversation `home` opened and nobody served yet.

    The owner sweep never serves a topic whose last speaker is Front, so the
    serving that opened a run is the one that starts it — here, right after
    its own reply went out. Serial, like everything in this listener: the
    run's first serving happens before the next poll.
    """
    runs = opened_runs(client, home)
    if not runs:
        return []
    self_id = int(client.whoami()["user_id"])
    started: list[tuple[str, str]] = []
    for run in runs:
        history = client.topic_history(run.channel, run.topic, num_before=LAST_SPEAKER_LOOKBACK)
        if not unstarted(history, self_id):
            continue
        log(f"starting run {run} opened from {home[0]!r}/{home[1]!r}")
        handle_topic(client, run.channel, run.topic, depth=depth)
        started.append(run.as_pair())
    return started


def continue_deliveries(client: ZulipClient, *, depth: int = 0) -> list[tuple[str, str]]:
    """Serve every conversation holding a run report nothing has answered.

    The counterpart of `start_opened_runs`, at the other end of a run. A run
    reports into the conversation that asked for it, and that post is Front's
    own, so no sweep will ever look at that conversation again: a request that
    wanted something *after* the routine would stop there with nobody to
    notice. The delivered note (`agfront.routine`) is what makes the pending
    handoff readable, and this serves it.

    It is deliberately **generic**: it says only that a report has landed and
    nobody has looked. Whether that means "open the next run", "tell the
    developer we are done" or "the report says the work failed, so stop" is
    decided by Front, in the conversation, out of the request and the report
    it can both read there. Nothing about any particular request is here.

    So an ordinary one-routine request does not loop: this buys exactly one
    serving, Front's reply to the developer is speech, and speech disarms the
    note. A second pass finds nothing to do.
    """
    if depth >= CONTINUATION_DEPTH:
        log(f"continuation depth {depth} reached; leaving the rest to the next event")
        return []
    self_id = int(client.whoami()["user_id"])
    continued: list[tuple[str, str]] = []
    for home in pending_continuations(client, self_id):
        log(f"continuing {home}: a run reported there and nothing has served it")
        handle_topic(client, home.channel, home.topic, depth=depth + 1)
        continued.append(home.as_pair())
    return continued


def recover_runs(client: ZulipClient) -> list[tuple[str, str]]:
    """At startup: start the runs Front opened before it went down, and serve
    the requesters a run reported into while nobody was listening.

    Two halves of the same gap. A crash between opening a run and starting it
    leaves a run nobody will start; a crash between delivering a report and
    serving its requester leaves a request nobody will continue. Neither is
    reachable by a sweep, because Front is the last speaker in both.
    """
    self_id = int(client.whoami()["user_id"])
    started: list[tuple[str, str]] = []
    for run in recover_unstarted_runs(client, self_id):
        log(f"recovering unstarted run {run}")
        handle_topic(client, run.channel, run.topic)
        started.append(run.as_pair())
    started.extend(continue_deliveries(client))
    return started


def relay_late_answer(
    client: ZulipClient, home: Conversation, live: str,
    channel: str, topic: str, self_id: int,
) -> None:
    """A callback for one of our conversations that has already finished.

    Nothing is reopened. A resolved conversation is finished for everybody —
    `agentchat send` refuses it and every sweep skips it — and the one route
    that did not respect that was this one, which forked a twin instead.

    The answer is not dropped either. When the finished conversation is a
    routine run, the request that opened it may still be live, so the run's
    origin is told that an answer arrived after the end, and the delivered
    note puts that conversation in front of Front again. What to do about it
    — another run, or nothing — is decided there, as everything else is.

    Either way the callback is marked served, so it is not reconsidered on
    every restart.
    """
    log(f"mention in {channel!r}/{topic!r} belongs to {home}, which is finished; not reopening it")
    resting = Conversation(home.channel, live)
    if is_run_topic(home.topic):
        history = client.topic_history(home.channel, live, num_before=HISTORY_MESSAGES)
        origin = origin_of(history, self_id)
        if origin is not None and origin != home:
            name = live_topic_name(client, origin.channel, origin.topic)
            if name.startswith(RESOLVED_TOPIC_PREFIX):
                log(f"{origin} is finished too; the late answer is recorded nowhere")
            else:
                client.send_to_channel(
                    origin.channel, name,
                    late_answer_text(home, Conversation(channel, topic)),
                )
                client.send_to_channel(origin.channel, name, delivered_note(home))
                log(f"told {origin} that {home} was answered after it ended")
    served = note_served(client, resting, channel, topic)
    if served is not None:
        log(f"marked {channel!r}/{topic!r} served up to {served} in {resting}")


def handle_mention(client: ZulipClient, channel: str, topic: str) -> None:
    """Front was named somewhere it does not own: serve the request it came from.

    The topic itself says which `front-*` conversation Front is speaking there
    on behalf of — the root note `agentchat send` wrote before Front's first
    post. That conversation is the whole serving: its chatlog, its workspace,
    its generation, **and its reply**. The topic that named Front is placed
    beside the chatlog as a thread, so the run reads what was said there
    without answering into it.

    Answering at home is the p8 change. p7 replied into the calling topic,
    which made every progress report a post in another agent's conversation —
    and a post in somebody's topic serves them, which is the loop p7 could
    not end. Now nothing goes outward unless Front decides to send it.

    A mention in a topic Front never anchored is not Front's business: it is
    logged and dropped, as in p7. Front's own entrance is `#front`, and
    nothing else opens a request to it.

    **Since `routine_tests` p2 ex1 "never anchored" is asked twice.** Retiring
    a plan renames its whole topic, which moves every message in it — Front's
    root note included — and the replacement then takes the freed display
    name. `rootchat_home` therefore follows the replacement's
    `[selfnote][replaces]` pointer, one hop, into the conversation this one
    replaced and looks for Front's own note there. The topic that called is
    passed as an `extra_thread` whether or not a note of Front's names it:
    in the inherited case nothing else can discover that the answer Front is
    being served for is in that conversation.

    Afterwards the serving is marked in home with `note_served`
    (`agent_standardize` p9). Because the reply went home, Front is never the
    last poster in the topic that called it, so without the mark a listener
    restart would find that topic still "naming Front, answered by somebody
    else" and serve the whole exchange again.
    """
    self_id = int(client.whoami()["user_id"])
    home = rootchat_home(client, channel, topic, self_id)
    if home is None:
        log(f"mention in {channel!r}/{topic!r} carries no root note of ours; ignoring")
        return
    # A resolved home is a finished conversation, and serving it would not
    # even reach it: resolving *renames* a topic, so reading the name the root
    # note recorded finds nothing, and the reply opens a second topic beside
    # the real one — a twin, without the origin note, whose report can never be
    # delivered. `routine_tests` p1 step 3 watched that happen three times.
    live = live_topic_name(client, home.channel, home.topic)
    if live.startswith(RESOLVED_TOPIC_PREFIX):
        relay_late_answer(client, home, live, channel, topic, self_id)
        return
    log(f"mention in {channel!r}/{topic!r} serves {home}")
    serve_topic(
        client, home.channel, home.topic, serve,
        ack_text=ACK_TEXT,
        # The topic that called is placed beside the chatlog whether or not a
        # root note of ours names it. It always does in the ordinary case;
        # it does not when the anchor was inherited through `replaces`, and
        # then this is the only way the answer reaches the run that asked.
        extra_threads=((channel, topic),),
        # A run topic holds nothing but Front's own record: that is the
        # conversation to serve, not an empty one (found by the p1 tests).
        empty_reply=None if is_run_topic(home.topic) else EMPTY_REPLY,
        # The selection is read from home, never from the topic that called
        # Front back: a callback's remote topic is somebody else's
        # conversation and may carry a command addressed to somebody else.
        exec_options=exec_options_for(SPEC, client),
    )
    served = note_served(client, home, channel, topic)
    if served is None:
        log(f"nothing to mark served in {channel!r}/{topic!r}")
    else:
        log(f"marked {channel!r}/{topic!r} served up to {served} in {home}")
    start_opened_runs(client, home.as_pair())
    # The common way a run ends: an agent answered, Front was named, and the
    # serving that read the answer wrote the finish block. Without this the
    # handoff would only ever fire for a run that finished on its very first
    # serving, which is the rare one.
    if is_run_topic(home.topic):
        continue_deliveries(client)
