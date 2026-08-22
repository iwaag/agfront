"""Front's chat entrance: the agag skeleton with Front's one route.

`listener_main` sweeps `front-` topics wherever Front is subscribed and
serves each through `handle_topic`. The mention route (`on_mention`) is what
makes a supervision several short runs instead of one long one: Front posts
into another agent's topic and ends; when that agent's reply names Front,
`handle_mention` serves the `front-*` conversation the topic's root note
says it belongs to, and Front answers at home.

The filter is `front-` only, which is what keeps Front from answering the
topics it opens elsewhere: no bot loop, by filter and not by luck. The other
side of the same asymmetry is agforge, which sweeps its own `assetplan-`
topics and never `front-`.
"""

from __future__ import annotations

from agag.agent import listener_main

from .instance import FRONT_TOPIC_PREFIX, SPEC
from .zulip_listener import handle_mention, handle_topic


def main() -> None:
    listener_main(SPEC, {FRONT_TOPIC_PREFIX: handle_topic}, on_mention=handle_mention)


if __name__ == "__main__":
    main()
