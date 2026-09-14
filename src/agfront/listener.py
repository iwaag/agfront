"""Front's chat entrance: the agag skeleton with Front's one route.

`listener_main` serves `front-` topics in any public channel through
`handle_topic` (`agag.listen`: a mirror of the realm on Front's own
credential feeds a durable queue; nothing is swept). The mention route (`on_mention`) is what
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
from .instance import FRONT_TOPIC_PREFIX, ROUTINE_RUN_PREFIX, SPEC
from .zulip_listener import handle_mention, handle_topic, recover_runs

ROUTES = {FRONT_TOPIC_PREFIX: handle_topic, ROUTINE_RUN_PREFIX: handle_topic}


def main() -> None:
    # A run Front opened just before going down has nobody else to start it,
    # and a request a run reported into has nobody else to continue it
    # (`agfront.routine`). Neither is visible to a last-speaker check, so the
    # listener runs `recover_runs` after every recovery it makes — startup,
    # and every time its mirror re-reads the realm — off the mirror's own
    # index, at no Zulip cost (`better_zulip_call` p1 step 5).
    listener_main(SPEC, ROUTES, on_mention=handle_mention, on_recover=recover_runs)


if __name__ == "__main__":
    main()
