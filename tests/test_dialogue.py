"""The dialogue block (`front_desk` p2 step 3): what a run writes is
validated against the pinned revision and re-serialized before the post;
an unusable block costs the scene, never the reply and never a re-run.
"""

import json
from pathlib import Path

import pytest

from agfront import dialogue
from agfront.dialogue import DialogueError, finish_reply, parse_block, render, split_reply
from agfront.settings import Character, CharacterSettings

REV = "4f3b55f654c8e137694718c1b74faa0c7cb2766f"
SETTINGS = CharacterSettings(revision=REV, root=Path("/nowhere"), characters={
    "front": Character("front", "Front", "姐さん", "lore", ("front",), (), "characters/front/face.jpg"),
    "autolab": Character("autolab", "Autolab", "親方", "lore", ("autolab",), (), "characters/autolab/face.jpg"),
})
REPLY = "できたよ〜🎉 親方が microsoft/markitdown をまとめてくれた✨ コミットは a99625f だよ📝"


def block(turns, schema=dialogue.SCHEMA, extra=None):
    data = {"schema": schema, "turns": turns, **(extra or {})}
    return f"```{dialogue.FENCE}\n{json.dumps(data, ensure_ascii=False)}\n```"


TURNS = [
    {"character": "front", "text": "親方、どうなった？✨"},
    {"character": "autolab", "text": "終わった。microsoft/markitdown、コミット a99625f。",
     "sources": [{"channel": "work-g-13", "topic": "workrun-task1-g-13", "message_id": 5203}]},
]


def test_the_block_is_the_last_fence_and_the_reply_is_what_remains():
    reply, body = split_reply(f"{REPLY}\n\n{block(TURNS)}\n")
    assert reply == REPLY and json.loads(body)["turns"] == TURNS
    assert split_reply("やっほー✨") == ("やっほー✨", None)
    # A block quoted earlier in the reply is prose; the last one is the block.
    quoted = f"前に書いたのはこれ：\n{block([{'character': 'front', 'text': 'old'}])}\n\n{REPLY}\n\n{block(TURNS)}"
    reply, body = split_reply(quoted)
    assert "old" in reply and json.loads(body)["turns"] == TURNS


def test_a_usable_block_is_re_serialized_with_the_pinned_revision():
    found = parse_block(json.dumps({"schema": dialogue.SCHEMA, "settings_revision": "somethingelse",
                                    "turns": TURNS}), SETTINGS)
    assert found.settings_revision == REV and found.characters == ("front", "autolab")
    assert found.turns[1].sources[0].message_id == 5203
    payload = found.payload()
    assert payload["schema"] == dialogue.SCHEMA and payload["settings_revision"] == REV
    assert payload["turns"][0] == {"character": "front", "text": "親方、どうなった？✨"}
    assert payload["turns"][1]["sources"] == [{"channel": "work-g-13", "topic": "workrun-task1-g-13", "message_id": 5203}]


def test_a_mention_inside_a_turn_is_reduced_to_the_name():
    found = parse_block(json.dumps({"schema": dialogue.SCHEMA, "turns": [
        {"character": "front", "text": "@**autolab-agstudio1** に聞いたよ"}]}), SETTINGS)
    assert found.turns[0].text == "autolab-agstudio1 に聞いたよ"
    assert "@**" not in found.serialize()


@pytest.mark.parametrize("body, reason", [
    ("not json", "not valid JSON"),
    ("[]", "JSON object"),
    (json.dumps({"schema": "other", "turns": TURNS}), "declares schema"),
    (json.dumps({"schema": dialogue.SCHEMA, "turns": []}), "non-empty"),
    (json.dumps({"schema": dialogue.SCHEMA, "turns": [{"character": "forge", "text": "x"}]}), "not in settings revision"),
    (json.dumps({"schema": dialogue.SCHEMA, "turns": [{"character": "front", "text": "  "}]}), "text is empty"),
    (json.dumps({"schema": dialogue.SCHEMA, "turns": [{"character": "front", "text": "x", "sources": [{"channel": "a"}]}]}), "needs channel and topic"),
    (json.dumps({"schema": dialogue.SCHEMA, "turns": [{"character": "front", "text": "x", "sources": [{"channel": "a", "topic": "b", "message_id": "n"}]}]}), "not an integer"),
    (json.dumps({"schema": dialogue.SCHEMA, "turns": [{"character": "front", "text": "x"}] * (dialogue.MAX_TURNS + 1)}), "more than"),
])
def test_an_unusable_block_names_what_is_wrong(body, reason):
    with pytest.raises(DialogueError, match=reason):
        parse_block(body, SETTINGS)


def test_without_settings_no_character_can_be_named():
    with pytest.raises(DialogueError, match="no character settings"):
        parse_block(json.dumps({"schema": dialogue.SCHEMA, "turns": TURNS}), None)


def test_the_post_is_the_reply_then_the_canonical_block(tmp_path):
    text, found, error = finish_reply(f"{REPLY}\n\n{block(TURNS)}", SETTINGS, workspace=tmp_path)
    assert error is None and found is not None
    assert text.startswith(REPLY + "\n\n```ag-dialogue\n")
    assert text.endswith("\n```")
    body = text.split("```ag-dialogue\n", 1)[1].rsplit("\n```", 1)[0]
    assert json.loads(body) == found.payload()
    assert not (tmp_path / "dialogue-error.txt").exists()


def test_a_reply_without_a_block_is_posted_as_it_is(tmp_path):
    assert finish_reply("やっほー✨ 今日もよろしくね💕", SETTINGS, workspace=tmp_path) == ("やっほー✨ 今日もよろしくね💕", None, None)


def test_an_unusable_block_keeps_the_reply_and_records_the_issue(tmp_path):
    logged = []
    text, found, error = finish_reply(f"{REPLY}\n\n```ag-dialogue\n{{broken\n```", SETTINGS,
                                      workspace=tmp_path, log=logged.append)
    assert found is None and "not valid JSON" in error
    assert text.startswith(REPLY + "\n\n```ag-dialogue-error\n")
    record = json.loads(text.split("```ag-dialogue-error\n", 1)[1].rsplit("\n```", 1)[0])
    assert record["schema"] == "ag.frontdesk-dialogue.v1-error" and "not valid JSON" in record["error"]
    assert "not valid JSON" in (tmp_path / "dialogue-error.txt").read_text()
    assert logged and "unusable" in logged[0]


def test_a_block_with_no_reply_around_it_still_gives_the_developer_front_s_words(tmp_path):
    text, found, _ = finish_reply(block(TURNS), SETTINGS, workspace=tmp_path)
    assert text.startswith("親方、どうなった？✨\n\n```ag-dialogue")
    assert found is not None


def test_an_over_long_scene_is_dropped_rather_than_truncated_by_the_realm(tmp_path, monkeypatch):
    monkeypatch.setattr(dialogue, "MAX_POST_CHARS", 200)
    text, found, error = finish_reply(f"{REPLY}\n\n{block(TURNS)}", SETTINGS, workspace=tmp_path)
    assert found is None and "longer than 200" in error and text.startswith(REPLY)


def test_render_is_reply_then_block_or_error():
    found = parse_block(json.dumps({"schema": dialogue.SCHEMA, "turns": TURNS}), SETTINGS)
    assert render("r", found).startswith("r\n\n```ag-dialogue\n{")
    assert render("r", None, "why") == 'r\n\n```ag-dialogue-error\n{"schema": "ag.frontdesk-dialogue.v1-error", "error": "why"}\n```'
    assert render("r", None) == "r"


def test_an_english_preface_before_the_japanese_reply_is_dropped_and_logged(tmp_path):
    """Seen live three times in front_desk p2 step 5."""
    logged = []
    text, _, _ = finish_reply("This is just small talk, so I'll answer in character.\n\nやっほー✨ 元気だよ〜💕",
                              SETTINGS, workspace=tmp_path, log=logged.append)
    assert text == "やっほー✨ 元気だよ〜💕"
    assert logged and "preface" in logged[0]
    # A reply that is Japanese from the first line, or English throughout, is untouched.
    assert finish_reply("やっほー✨\n\nWhat is next?", SETTINGS, workspace=tmp_path)[0] == "やっほー✨\n\nWhat is next?"
    assert finish_reply("All done.\n\nSee the topic.", SETTINGS, workspace=tmp_path)[0] == "All done.\n\nSee the topic."
    # The preface goes even when a dialogue block follows.
    text, found, _ = finish_reply(f"Now it reads correctly.\n\n{REPLY}\n\n{block(TURNS)}", SETTINGS, workspace=tmp_path)
    assert text.startswith(REPLY) and found is not None
