"""`agproject`: the artifacts an argue ends in — a project or a study, set up.

`argue` p1 step 3. When Front judges that a desire is ready to become a
concrete project, or that a study should come first, the artifacts are:

- **a project**: its `pj-<slug>` channel, filed in a folder of its own with
  the humans who own the realm, autolab and Front in it; a `goal` topic
  whose newest post is the final goal and how to proceed; and its
  workspace, prepared by autolab on request (`workplan-setup-<slug>`, a
  setup serving that plans no mission), holding the same document. The
  channel's description, the document and the workspace README name each
  other and the argue.
- **a new study**: the same, on autolab's `study` pattern, with the
  research plan (`researchplan-<stem>`) in place of the goal.
- **a research plan in an existing study**: one `researchplan-<stem>`
  topic in that study's channel, and nothing else.

    agproject open <slug> --kind project|study --doc <file>
    agproject plan <study slug> --doc <file>

Both run under the conversation named by `AGENTCHAT_HOME` (the argue) and
say so in everything they write, so each artifact links back. The channel
is created with the provisioner credential this node holds — the same one
autolab files project channels with — and everything else is posted as
Front. **Nothing here starts work**: a `workplan-setup-` post asks autolab
to prepare folders and repositories and reply; a `workrun-` topic is never
opened, and the study's routine is not run.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from agag.intro import harvest_intros, parse_roster
from agag.selfnote import Conversation, home_from_environment, rootchat_note
from agag.zulip import RESOLVED_TOPIC_PREFIX, ZulipClient, ZulipError

from .instance import AGFRONT_ROOT, SPEC

PROJECT_CHANNEL_PREFIX = "pj-"
GOAL_TOPIC = "goal"
PLAN_TOPIC_PREFIX = "researchplan-"
SETUP_TOPIC_PREFIX = "workplan-setup-"
PROVISIONER_VARIABLE = "AGFRONT_PROVISIONER_ENV"
DEFAULT_PROVISIONER_ENV = AGFRONT_ROOT.parent / ".local" / "zulip" / "provisioner.env"
KINDS = ("project", "study")

__all__ = [
    "GOAL_TOPIC", "KINDS", "PLAN_TOPIC_PREFIX", "PROJECT_CHANNEL_PREFIX", "ProjectError", "SETUP_TOPIC_PREFIX",
    "autolab_bot", "channel_link", "main", "open_project", "plan_topic", "project_channel", "run", "setup_request",
    "write_plan",
]


class ProjectError(RuntimeError):
    pass


def project_channel(slug: str) -> str:
    return f"{PROJECT_CHANNEL_PREFIX}{slug}"


def plan_topic(stem: str) -> str:
    return f"{PLAN_TOPIC_PREFIX}{stem}"


def channel_link(client: ZulipClient, stream_id: int, name: str) -> str:
    base = str(getattr(client, "base_url", "")).rstrip("/")
    return f"{base}/#narrow/channel/{stream_id}-{name}"


def autolab_bot(client: ZulipClient) -> int | None:
    """The user id of the agent that answers `workplan-` topics, read off the
    board — the one that will prepare the workspace."""
    for _, body in harvest_intros(client):
        roster = parse_roster(body)
        if roster is not None and roster.bot_id is not None and "workplan-" in tuple(roster.prefixes or ()):
            return int(roster.bot_id)
    return None


def _admin(path: str | None) -> ZulipClient:
    env = Path(path or os.environ.get(PROVISIONER_VARIABLE) or DEFAULT_PROVISIONER_ENV)
    if not env.is_file():
        raise ProjectError(f"no provisioner credential at {env}: a project channel cannot be created from here")
    return ZulipClient.from_env(env)


def _self(client: ZulipClient | None) -> ZulipClient:
    if client is not None:
        return client
    from agag.chat import client_from_environment

    return client_from_environment()


def _document(path: str) -> str:
    text = Path(path).read_text(encoding="utf-8").strip()
    if not text:
        raise ProjectError(f"{path} is empty")
    return text


def _origin_word(home: Conversation) -> str:
    """The conversation a project grew out of is usually an argue; since
    adventure_game p1 it may be the developer's own `front-` conversation,
    where a decision taken after the argue closed is handed over."""
    return "argue" if home.channel == "argue" else "conversation"


def _origin_line(home: Conversation | None) -> str:
    return f"Opened from {_origin_word(home)} **#{home.channel} › {home.topic}**." if home else "Opened by hand (no argue named)."


def setup_request(slug: str, kind: str, doc_topic: str, home: Conversation | None) -> str:
    """What autolab is asked, in a `workplan-setup-` topic: prepare, never run."""
    where = f"#{project_channel(slug)} › {doc_topic}"
    origin = f" It grew out of {_origin_word(home)} #{home.channel} › {home.topic}." if home else ""
    if kind == "study":
        layout = (
            "Set it up on the **study** pattern (`autolab doc patterns`): `main/` on the standard internal "
            "repository route as the working knowledge repository, with `methods/` and `reports/` and the "
            "`.local/` ignore rule; no `publish/` yet — say in `README_PROJECT.md` that a publication repository "
            "is to be supplied by the developer."
        )
        doc_name = "RESEARCHPLAN.md"
    else:
        layout = (
            "Set it up as a plain project: `main/` on the standard internal repository route, and nothing else "
            "unless the goal below plainly needs another folder."
        )
        doc_name = "GOAL.md"
    return (
        f"Please prepare the workspace for the {kind} `{slug}` — **setup only, no research and no development, "
        f"and no mission to plan**: reply when the folders and repositories exist.{origin}\n\n"
        f"{layout}\n\n"
        f"Put the document posted in {where} into `main/{doc_name}` unchanged, and make `README_PROJECT.md` "
        f"name this channel, that topic and the argue it grew out of, so the folder and the channel point at "
        f"each other. Commit and push `main/`. Reply with what exists and where; nothing is started by this post."
    )


def open_project(
    slug: str, kind: str, document: str, *, home: Conversation | None, client: ZulipClient, admin: ZulipClient,
    out=None,
) -> dict:
    """Create the channel, post the document, ask autolab to prepare the workspace."""
    out = sys.stdout if out is None else out
    if kind not in KINDS:
        raise ProjectError(f"kind must be one of {', '.join(KINDS)}")
    if not slug or not slug.replace("-", "").isalnum() or slug != slug.lower():
        raise ProjectError(f"{slug!r} is not a project slug (lowercase letters, digits and dashes)")
    name = project_channel(slug)
    if any(row.get("name") == name for row in admin.channels(include_archived=True)):
        raise ProjectError(f"#{name} already exists; choose another slug, or add a research plan to it with `agproject plan`")
    self_id = int(client.whoami()["user_id"])
    autolab = autolab_bot(client)
    principals = sorted({*admin.realm_owners(), self_id, *([autolab] if autolab is not None else [])})
    origin = f"{_origin_word(home)} {home}" if home else "an argue"
    description = f"[AUTO] project: {slug}; {kind}; opened from {origin}; its goal is the `{GOAL_TOPIC if kind == 'project' else PLAN_TOPIC_PREFIX + slug}` topic"
    folder = admin.channel_folder_by_name(name)
    folder_id = int(folder["id"]) if folder else admin.create_channel_folder(name, f"{slug} project channel and its work channels")
    admin.create_channel(name, description, principals=principals, folder_id=folder_id)
    stream_id = int(next(row["stream_id"] for row in admin.channels() if row.get("name") == name))
    doc_topic = GOAL_TOPIC if kind == "project" else plan_topic(slug)
    doc_id = client.send_to_channel(name, doc_topic, f"{document}\n\n---\n{_origin_line(home)}")
    setup_topic = f"{SETUP_TOPIC_PREFIX}{slug}"
    if home is not None:
        client.send_to_channel(name, setup_topic, rootchat_note(home))
    setup_id = client.send_to_channel(name, setup_topic, setup_request(slug, kind, doc_topic, home))
    link = channel_link(admin, stream_id, name)
    print(f"opened #{name} (stream {stream_id}, folder {folder_id}, members {principals}) — {link}", file=out)
    print(f"posted the {'goal' if kind == 'project' else 'research plan'} as message {doc_id} in #{name} › {doc_topic}", file=out)
    print(f"asked autolab to prepare the workspace in #{name} › {setup_topic} (message {setup_id}); "
          f"its reply will name you and come back to {home if home else 'nowhere'}", file=out)
    if autolab is None:
        print("warning: no agent on the board answers workplan- topics; the setup request has no reader yet", file=out)
    print("nothing has been started: no workrun- topic was opened and no routine was run", file=out)
    return {"channel": name, "stream_id": stream_id, "doc_topic": doc_topic, "doc_id": doc_id,
            "setup_topic": setup_topic, "setup_id": setup_id, "link": link}


def write_plan(slug: str, stem: str, document: str, *, home: Conversation | None, client: ZulipClient, out=None) -> dict:
    """One research plan into an existing study's channel."""
    out = sys.stdout if out is None else out
    name = project_channel(slug)
    row = next((r for r in client.channels() if r.get("name") == name), None)
    if row is None:
        raise ProjectError(f"#{name} does not exist; a new study is `agproject open {slug} --kind study`")
    topic = plan_topic(stem)
    if client.topic_last_id(name, topic) or client.topic_last_id(name, f"{RESOLVED_TOPIC_PREFIX}{topic}"):
        raise ProjectError(f"#{name} › {topic} already exists; choose another stem")
    client.ensure_subscribed(name)
    doc_id = client.send_to_channel(name, topic, f"{document}\n\n---\n{_origin_line(home)}")
    link = channel_link(client, int(row["stream_id"]), name)
    print(f"posted the research plan as message {doc_id} in #{name} › {topic} — {link}", file=out)
    print("nothing has been started: the study's routine is not run by this post", file=out)
    return {"channel": name, "topic": topic, "doc_id": doc_id, "link": link}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="agproject", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    open_ = sub.add_parser("open", help="create a project or study: channel, document, workspace request")
    open_.add_argument("slug", help="the project's short name; the channel becomes pj-<slug>")
    open_.add_argument("--kind", choices=KINDS, required=True)
    open_.add_argument("--doc", required=True, help="the goal (project) or research plan (study), a Markdown file")
    open_.add_argument("--provisioner-env", default=None, help=f"admin credential (default ${PROVISIONER_VARIABLE} or the node's)")
    plan = sub.add_parser("plan", help="add a research plan to an existing study's channel")
    plan.add_argument("slug", help="the study's slug (its channel is pj-<slug>)")
    plan.add_argument("--doc", required=True, help="the research plan, a Markdown file")
    plan.add_argument("--stem", default=None, help="topic stem; the topic becomes researchplan-<stem> (default: the slug)")
    return parser


def run(argv: list[str], *, client: ZulipClient | None = None, admin: ZulipClient | None = None, out=None, err=None) -> int:
    out = sys.stdout if out is None else out
    err = sys.stderr if err is None else err
    args = build_parser().parse_args(argv)
    try:
        home = home_from_environment()
        if args.command == "open":
            open_project(args.slug, args.kind, _document(args.doc), home=home, client=_self(client),
                         admin=admin or _admin(args.provisioner_env), out=out)
        else:
            write_plan(args.slug, args.stem or args.slug, _document(args.doc), home=home, client=_self(client), out=out)
        return 0
    except (ProjectError, ZulipError, OSError) as error:
        print(f"agproject: {error}", file=err)
        return 1


def main() -> int:
    return run(sys.argv[1:])


if __name__ == "__main__":
    raise SystemExit(main())
