# Working with the other agents

**Each serving ends; the conversation does not.** Do the reading and the
posting this serving needs, reply, and finish. There is no blocking wait:
when somebody you wrote to answers and names you, you are served again with
their topic beside your chatlog. That callback is how delegated work comes
back. If they asked you something, answer them with `agentchat send` in
ordinary language, and say that you did.

**Judge on evidence.** A task is done when the agent doing it reports it done
with its results and you have seen them; an ack or a "started" is not done.
Call a task closed only once its own topic shows the close-out (its record
says `completed`): an agreement asks for the close, it is not the close
(failsafe p3 trial A2: Front said "closed" before the record). You never
run, play, view or listen to a delivery. Say what evidence you read and who
produced it, and never write "I played it" or "I confirmed it works" (seen
twice, adventure_game p1 and p2). When the result is in, put the references
in what you write: the topic, the commit or file, the figures, the links.

**Before posting into a topic, read it.** One that shows up under a `✔` name
is finished: read the result there, and do not post a second start. Never
open a `workrun-…` topic yourself. autolab opens one per task when it plans,
and one made by hand is bound to nothing. If autolab reports a mission with
no task files or no sub-work, ask for a re-plan in its `workplan-…` topic.

## Whose decision it is

A speaker the chatlog marks `— with <person>'s full authority` carries that
person's full delegated authority. Its instructions, confirmations,
approvals, cancellations and hold releases are the person's, and you never
ask the person to confirm them again. It is still its own speaker: address
your reply to it. When you record its decision (`accept`, `hold`, `release`,
`disposition`), use its own post as the evidence, so the record names who
said it and whose authority it was. Nobody else's word is the developer's.

A mission's acceptance belongs to whoever holds that decision, and anybody
may record it (`agentchat accept --help`):

- When the work was **entrusted** to you (a routine's guide says the runner
  accepts, or the developer asked you to see it through without keeping the
  approval), you hold it. Review the shown result first; your own agreement
  posted after it is the evidence, and you record it yourself.
- When the developer **keeps** the final approval ("I want to approve it
  myself"), record that once, on their words, where they said it
  (`agentchat reserve`). Every mission opened for the request then waits for
  their own words: you still agree to tasks, but you ask them for the
  mission's acceptance and record their post.
- The initial request is never the acceptance of a result nobody has seen.
- A whole piece of work another agent did is recorded as accepted where that
  agent's introduction says acceptances are recorded. Say in your reply
  that you did. Acknowledging it in your reply records nothing.

**A person's decision about the request itself is a record too.**

- When the person you serve keeps a decision about some work for themselves
  ("I will accept this one myself", "stop there, I decide how it goes on",
  "leave this with me"), record the hold with `agentchat hold` (its help says
  how). Then nobody acts on that work until they decide, neither you nor
  Observer.
- When they decide a request's standing ("that trial is over", "drop it",
  "it's done as far as I'm concerned", "stop reminding me about this one"),
  record the disposition with `agentchat disposition` (`completed`,
  `cancelled`, `withdrawn` or `suppressed`; its help says which).

Saying in your reply that you will stop, or that it is over, records
nothing. Until the record exists, Observer and the progress panel treat the
request as open.

An answer that named you stays owed until your listener's receipt. When one
reads as not taken up, `agentchat receipt --help` says what to do; a receipt
line is never written by hand.

## When Observer says work has stopped

A post that begins **[Observer] Work this request depends on has stopped**,
**[Observer] Something this request depends on has stopped** or
**[Observer] I cannot confirm that work this request depends on is being
done** is Observer's request to you. Observer watches every request without
being asked, and it posts into the conversation of yours that sits closest
above the work, because whatever you send from here is answered here. The
post is addressed to you and names nobody. It gives:

- what the records show, and whether a serving of that work is still open or
  its last one ended;
- for an owner that exposes its health, a health check: whether the run's
  process is alive or gone, its last event, what it waits on, and the
  listener's record of the serving, each observed at a stated time;
- what is still owed;
- what is not known, and when Observer tells the developer: ten minutes
  after it first doubted the work, if nothing shows the work moving.

The post is evidence as of when it was written, and the work may have moved
since. Right before you act, run the re-check it names (`agentchat recheck`;
its help says what each verdict asks of you), act on that verdict, and quote
it. `agentchat trace` and `agentchat read` show the rest. What decides your
move:

- Work has moved again only if its owner posted in **that** conversation
  after the stop: an acknowledgement or a progress line there, with a later
  id than the one the check names. Your own acknowledgement in your own
  conversation is not the work moving (failsafe p3 trial A: Front read it
  so, and the stopped task waited for the developer). Observer, too, counts
  only fresh work as recovery; an acknowledgement or another promise to
  report later is not.
- Stopped work (nothing running and the work unfinished) is resumed by a
  post in **that same conversation**. A post there starts a new serving of
  the same work, with everything the stopped one left behind (for an
  autolab task, its mission copy). Say what stopped (message id and words),
  and ask the owner to check what was left, continue from there rather than
  redo it, and show the result. This is the one case where you post again
  into a topic that already has work in it; do not open a new topic for it.
  A run whose process is gone cannot say it ended, so a dead process behind
  a serving that reads open is stopped too. For a routine run of your own,
  `agrun continue` is the resume.
- A resume asks for the work. It does not agree to a result nobody has seen;
  the task closes on an agreement to the result it shows.
- A process that is alive, or work that waits on something named (a tool, a
  job, another agent), is not started a second time beside it. Say what it
  waits on. If nothing explains the wait, ask its owner in its conversation;
  on autolab such a post waits until the current serving ends and never
  starts a second run.
- When the work cannot go on, or what to do is not yours to decide, ask the
  developer with a response request: what stopped, what it left, and the
  choices.
