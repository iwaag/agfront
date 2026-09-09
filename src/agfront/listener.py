"""Front's chat entrance: the agag skeleton with Front's one route.

`listener_main` sweeps `front-` topics wherever Front is subscribed and
serves each through `handle_topic`. The mention route (`on_mention`) is what
makes a supervision several short runs instead of one long one: Front posts
into another agent's topic and ends; when that agent's reply names Front,
`handle_mention` serves the `front-*` conversation the topic's root note
says it belongs to, and Front answers at home.

The filter is `front-` and `routinerun-` (`refine_routine` p1): the second
is a topic Front opens itself, in a routine's channel, and then owns — a run
of that routine, served by the `routine_run` role. Nothing else Front opens
elsewhere is swept: no bot loop, by filter and not by luck. The other side of
the same asymmetry is agforge, which sweeps its own `assetplan-` topics and
never `front-`.
"""

from __future__ import annotations

from agag.agent import listener_main
from agag.zulip import ZulipClient, log

from .instance import FRONT_TOPIC_PREFIX, ROUTINE_RUN_PREFIX, SPEC
from .zulip_listener import handle_mention, handle_topic, recover_runs

ROUTES = {FRONT_TOPIC_PREFIX: handle_topic, ROUTINE_RUN_PREFIX: handle_topic}


def main() -> None:
    # A run Front opened just before going down has nobody else to start it
    # (`agfront.routine`): look once, before the sweep loop takes over.
    try:
        recover_runs(ZulipClient.from_env(SPEC.zulip_env))
    except Exception as error:  # noqa: BLE001 - recovery must not stop the listener
        log(f"run recovery failed: {error!r}")
    listener_main(SPEC, ROUTES, on_mention=handle_mention)


if __name__ == "__main__":
    main()
