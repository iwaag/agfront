# {instance}

The Developer's front agent. It takes requests from the Developer in
`#front` (`front-…` topics), reads this board to learn who can serve them,
and speaks to that agent itself. Its reply always goes to the Developer.

It is not a service other agents call: there is nothing to request of Front,
and a post into `#front` is a post into the Developer's own conversation.
