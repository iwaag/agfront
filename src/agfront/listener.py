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
from .argue import handle_argue
from .instance import ARGUE_TOPIC_PREFIX, FRONT_TOPIC_PREFIX, ROUTINE_RUN_PREFIX, SPEC
from .zulip_listener import handle_mention, handle_topic, recover_runs

#: `argue-` (argue p1) is the third thing Front owns: a conversation in
#: `#argue` it facilitates, served without the hand-off mention every other
#: reply carries, so that an agent speaks there only when Front names it.
ROUTES = {FRONT_TOPIC_PREFIX: handle_topic, ROUTINE_RUN_PREFIX: handle_topic, ARGUE_TOPIC_PREFIX: handle_argue}


def main() -> None:
    import os

    from agag.agent import log_only
    from agag.mirror import Mirror
    from agag.zulip import ZulipClient, log

    # One mirror for both readers of the realm in this process: the
    # listener's intake and the rendering worker (`agfront.render`, `argue`
    # p2). The worker has its own checkpoint, store and client, and nothing
    # it does — a slow run, a failure, being switched off with
    # `AGFRONT_RENDER=0` — is visible to the serving queue.
    mirror = Mirror.open(SPEC.zulip_env, SPEC.local / "mirror", log=log)
    if os.environ.get("AGFRONT_RENDER", "1") != "0" and not log_only(SPEC):
        from . import render

        render.start(mirror, ZulipClient.from_env(SPEC.zulip_env))
    # A run Front opened just before going down has nobody else to start it,
    # and a request a run reported into has nobody else to continue it
    # (`agfront.routine`). Neither is visible to a last-speaker check, so the
    # listener runs `recover_runs` after every recovery it makes — startup,
    # and every time its mirror re-reads the realm — off the mirror's own
    # index, at no Zulip cost (`better_zulip_call` p1 step 5).
    listener_main(SPEC, ROUTES, on_mention=handle_mention, on_recover=recover_runs, mirror=mirror)


if __name__ == "__main__":
    main()
