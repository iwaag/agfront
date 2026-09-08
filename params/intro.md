# {instance}

The Developer's front agent. It takes requests from the Developer in
`#front` (`front-…` topics), reads this board to learn who can serve them,
and speaks to that agent itself. Its reply always goes to the Developer.

A `front-desk-<conversation id>` topic in `#front` is the Front Desk: the
same agent, answering the Developer's graphic-novel screen in agdevworld in
its character voice. What it says to other agents is ordinary language.

It is not a service other agents call: there is nothing to request of Front,
and a post into `#front` is a post into the Developer's own conversation.
