"""`python -m agfront.trial <probe> --out <dir>` — one Front Desk serving of a
fixture probe (`agent_guide` p2 step 6; the shared kit since p2 ex1).

The serving is agfront's own `serve`: the evidence chatlog, `tools/agents.md`
harvested from the board, `tools/runs.md`, the prompt, the run. Only two
things differ from the listener: the client is the fixture board
(`agag.fixture.run.client`), and the run's `agentchat` reads it too. Nothing
is posted anywhere.

`--guides <tree>` or `--guides-rev <commit>` (this checkout's `agent/guides`
at that commit) serves with another guide tree, and `--no-shared` leaves out
pyagag's shared sections: p1's composition is `--guides-rev 447bb03
--no-shared`, the one before p1 `--guides-rev ba28e90^ --no-shared`. The
reply, its verdict under the probe's rule, the run record's cost and the
transcript's tool calls are written to `--out` (`outcome.json`, `reply.md`).

`--replies <file>…` runs no model: each serving's run answers the next
file's text (failsafe p7 reproduces run-0183's reply this way).

A probe with a responder script (`delegate-answer`, `delegate-decision`) is
a conversation: Front's `agentchat send` is recorded in the trial's own copy
of the board and answered by the script, and the desk conversation is served
again for each scripted callback (`Trial.converse`), until none comes.
"""

from __future__ import annotations

import sys
from pathlib import Path

from agag.fixture.run import Trial, client, newest, probe_history, session_log, tool_calls, trial_parser
from agag.topics import TopicContext

#: This checkout, where `--guides-rev` is looked up.
ROOT = Path(__file__).resolve().parents[2]


def main(argv: list[str] | None = None) -> int:
    args = trial_parser("python -m agfront.trial", __doc__, "agfront").parse_args(argv)
    trial = Trial.start(args, ROOT)
    from . import zulip_listener

    # The listener keeps both roots as constants taken at import.
    zulip_listener.TOPICS_ROOT = zulip_listener.SPEC.topics_root
    zulip_listener.RECORDS_ROOT = zulip_listener.SPEC.records_root

    if trial.guides is not None:
        zulip_listener.GUIDES = trial.guides
        if not (trial.guides / "shared").is_dir():
            # A tree from before agent_guide p1 has no shared files.
            zulip_listener.SHARED_GUIDES = {}
    if not trial.shared:
        zulip_listener.PYAGAG_SECTIONS = {}
    probe = trial.probe
    role = zulip_listener.role_for(probe.channel, probe.topic)

    def calls() -> list[str]:
        workspace = newest(zulip_listener.TOPICS_ROOT / probe.channel / probe.topic, "*")
        return tool_calls(session_log(workspace / role)) if workspace is not None else []

    if probe.script or probe.claims:
        # Delegation probes: served again on each scripted callback; claim
        # probes (failsafe p7): checked as the listener checks, and served
        # again when a mismatch is written.
        return trial.converse(zulip_listener.serve, calls)
    board = client(trial.store)
    me = board.whoami()
    context = TopicContext(board, probe.channel, probe.topic, int(me["user_id"]), str(me["full_name"]),
                           history=probe_history(probe, board))
    with trial.session():
        result = zulip_listener.serve(context)
    return trial.finish(result.output or "", records=zulip_listener.RECORDS_ROOT / role, role=role, calls=calls())


if __name__ == "__main__":
    sys.exit(main())
