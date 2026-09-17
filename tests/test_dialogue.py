"""The dialogue block: what a run writes is validated against the pinned
revision and re-serialized before anything is saved. Since `argue` p2 the
block is the presentation role's whole output (`tests/test_present.py`); the
reply-plus-block post of the old combined Front Desk run is gone.
"""

import json
from pathlib import Path

import pytest

from agfront import dialogue
from agfront.dialogue import DialogueError, parse_block, split_reply
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


def test_a_source_may_name_only_its_message_when_the_caller_knows_the_conversation():
    """The presentation role renders one conversation and cites by id."""
    body = json.dumps({"schema": dialogue.SCHEMA, "turns": [
        {"character": "autolab", "text": "終わった。", "sources": [{"message_id": 5203}]}]})
    found = parse_block(body, SETTINGS, source_default=("argue", "argue-x"))
    assert found.turns[0].sources[0].payload() == {"channel": "argue", "topic": "argue-x", "message_id": 5203}
    with pytest.raises(DialogueError, match="needs channel and topic"):
        parse_block(body, SETTINGS)
