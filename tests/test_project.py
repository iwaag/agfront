"""`agproject` (`argue` p1 step 3): the artifacts an argue ends in.

Pinned: `open` creates the channel with the humans, autolab and Front in
it, files it, posts the document under the right topic, and asks autolab
for the workspace in a `workplan-setup-` topic anchored to the argue —
and opens no `workrun-`; `plan` posts one research plan into an existing
study and nothing else; a slug in use is refused; every artifact names the
argue it grew out of.
"""

from __future__ import annotations

import io

from agfront import project

FRONT_ID, DEV_ID, AUTOLAB_ID = 15, 8, 11
INTRO = """# autolab

```agag-roster
schema: ag.agent-roster.v1
instance: autolab-agstudio1
agent: agautolab
bot: autolab-agstudio1
bot_id: 11
channel: autolab-agstudio1
prefixes: workplan-, workrun-, bmining-
```
"""


class Admin:
    base_url = "https://zulip.example"

    def __init__(self, existing=()):
        self.rows = [{"name": n, "stream_id": 100 + i, "folder_id": None} for i, n in enumerate(existing)]
        self.calls = []
        self.folders = {}

    def channels(self, include_archived=False):
        return list(self.rows)

    def realm_owners(self):
        return [DEV_ID]

    def channel_folder_by_name(self, name):
        return {"id": self.folders[name]} if name in self.folders else None

    def create_channel_folder(self, name, description=""):
        self.folders[name] = 50 + len(self.folders)
        self.calls.append(("folder", name))
        return self.folders[name]

    def create_channel(self, name, description, principals, announce=False, folder_id=None):
        self.calls.append(("create", name, description, tuple(principals), folder_id))
        self.rows.append({"name": name, "stream_id": 200, "folder_id": folder_id})
        return {"result": "success"}


class Client:
    base_url = "https://zulip.example"

    def __init__(self, channels=()):
        self.posts = []
        self.names = list(channels)
        self.subscribed = []

    def whoami(self, refresh=False):
        return {"user_id": FRONT_ID, "full_name": "Front"}

    def stream_id(self, name):
        return 35

    def channel_topics(self, stream_id):
        return ["intro-autolab-agstudio1"]

    def topic_history(self, channel, topic, num_before=50):
        if channel == "agents":
            return [{"id": 1, "sender_id": AUTOLAB_ID, "sender_full_name": "autolab-agstudio1", "content": INTRO}]
        return []

    def channels(self, include_archived=False):
        return [{"name": n, "stream_id": 300 + i} for i, n in enumerate(self.names)]

    def topic_last_id(self, channel, topic):
        return 0

    def ensure_subscribed(self, channel):
        self.subscribed.append(channel)
        return True

    def send_to_channel(self, channel, topic, content):
        self.posts.append((channel, topic, content))
        return 700 + len(self.posts)


def run(argv, client, admin, tmp_path, monkeypatch, home="argue/argue-aquarium"):
    if home:
        monkeypatch.setenv("AGENTCHAT_HOME", home)
    else:
        monkeypatch.delenv("AGENTCHAT_HOME", raising=False)
    out, err = io.StringIO(), io.StringIO()
    code = project.run(argv, client=client, admin=admin, out=out, err=err)
    return code, out.getvalue(), err.getvalue()


def test_open_creates_the_channel_posts_the_goal_and_asks_for_the_workspace(tmp_path, monkeypatch):
    doc = tmp_path / "GOAL.md"
    doc.write_text("# Aquarium factory\n\nFinal goal: …\n", encoding="utf-8")
    client, admin = Client(), Admin(existing=("pj-other",))
    code, out, err = run(["open", "aquafactory", "--kind", "project", "--doc", str(doc)], client, admin, tmp_path, monkeypatch)
    assert code == 0, err
    (create,) = [c for c in admin.calls if c[0] == "create"]
    _, name, description, principals, folder_id = create
    assert name == "pj-aquafactory" and principals == (DEV_ID, AUTOLAB_ID, FRONT_ID) and folder_id == 50
    assert "[AUTO] project: aquafactory" in description and "argue/argue-aquarium" in description
    assert ("folder", "pj-aquafactory") in admin.calls
    goal, anchor, setup = client.posts
    assert goal[:2] == ("pj-aquafactory", "goal") and goal[2].startswith("# Aquarium factory") and "argue-aquarium" in goal[2]
    assert anchor == ("pj-aquafactory", "workplan-setup-aquafactory", "[selfnote][rootchat] argue/argue-aquarium")
    assert setup[:2] == ("pj-aquafactory", "workplan-setup-aquafactory")
    assert "setup only" in setup[2] and "GOAL.md" in setup[2] and "workrun-" not in setup[2]
    assert not any(topic.startswith("workrun-") for _, topic, _ in client.posts)
    assert "opened #pj-aquafactory" in out and "nothing has been started" in out


def test_open_as_a_study_uses_the_study_pattern_and_the_plan_topic(tmp_path, monkeypatch):
    doc = tmp_path / "RESEARCHPLAN.md"
    doc.write_text("# Aquaculture study\n", encoding="utf-8")
    client, admin = Client(), Admin()
    code, out, _ = run(["open", "aquaculture", "--kind", "study", "--doc", str(doc)], client, admin, tmp_path, monkeypatch)
    assert code == 0
    plan, _, setup = client.posts
    assert plan[:2] == ("pj-aquaculture", "researchplan-aquaculture")
    assert "**study** pattern" in setup[2] and "RESEARCHPLAN.md" in setup[2] and "no `publish/`" in setup[2]


def test_open_refuses_a_channel_in_use_and_a_bad_slug(tmp_path, monkeypatch):
    doc = tmp_path / "GOAL.md"
    doc.write_text("goal", encoding="utf-8")
    client, admin = Client(), Admin(existing=("pj-taken",))
    code, _, err = run(["open", "taken", "--kind", "project", "--doc", str(doc)], client, admin, tmp_path, monkeypatch)
    assert code == 1 and "already exists" in err and client.posts == []
    code, _, err = run(["open", "Bad Slug", "--kind", "project", "--doc", str(doc)], client, admin, tmp_path, monkeypatch)
    assert code == 1 and "not a project slug" in err


def test_plan_posts_into_an_existing_study_and_nothing_else(tmp_path, monkeypatch):
    doc = tmp_path / "RESEARCHPLAN.md"
    doc.write_text("# Explore closed-loop food systems\n", encoding="utf-8")
    client = Client(channels=("pj-studyrealworld",))
    code, out, _ = run(["plan", "studyrealworld", "--doc", str(doc), "--stem", "closed-loop-food"], client, Admin(), tmp_path, monkeypatch)
    assert code == 0
    assert client.posts == [("pj-studyrealworld", "researchplan-closed-loop-food",
                             "# Explore closed-loop food systems\n\n---\nOpened from argue **#argue › argue-aquarium**.")]
    assert client.subscribed == ["pj-studyrealworld"]
    assert "not run by this post" in out
    code, _, err = run(["plan", "studynone", "--doc", str(doc)], Client(), Admin(), tmp_path, monkeypatch)
    assert code == 1 and "does not exist" in err


def test_without_an_argue_the_artifacts_say_so(tmp_path, monkeypatch):
    doc = tmp_path / "GOAL.md"
    doc.write_text("goal", encoding="utf-8")
    client = Client()
    code, _, _ = run(["open", "solo", "--kind", "project", "--doc", str(doc)], client, Admin(), tmp_path, monkeypatch, home=None)
    assert code == 0
    assert "Opened by hand" in client.posts[0][2]
    assert [t for _, t, _ in client.posts] == ["goal", "workplan-setup-solo"]  # no root note without a home
