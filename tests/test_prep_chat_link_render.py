from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

from alan import prep_chat_link
from tests.prep_chat_link_support import (
    LOCAL_BASE,
    UNKNOWN_SESSION_ID,
    World,
    build_session,
    conversation,
    make_world,
    message,
)

_CAP = prep_chat_link.TRANSCRIPT_CHAR_CAP


@pytest.fixture
def world(tmp_path: Path) -> Iterator[World]:
    built = make_world(tmp_path)
    yield built
    built.queue.close()


def _block(world: World, session_id: str) -> str:
    attachment = prep_chat_link.attach(
        f"{LOCAL_BASE}/build?s={session_id}",
        world.env(),
        engine_key=None,
        session_id=UNKNOWN_SESSION_ID,
    )
    (block,) = attachment.blocks
    return block


def _transcript(block: str) -> str:
    return block[block.index("<transcript>") : block.index("</transcript>")]


def _padded(label: str, size: int) -> str:
    return label + "." * (size - len(label))


def test_the_cap_is_forty_thousand_characters() -> None:
    assert _CAP == 40_000


def test_a_read_block_holds_the_full_brief_and_the_transcript_oldest_first(
    world: World,
) -> None:
    session_id = build_session(
        world, transcript=conversation("first question", "first answer"), brief="Whole brief"
    )

    block = _block(world, session_id)

    assert block.startswith('<preparation_chat link="1" status="read" title="first question">')
    assert block.endswith("</preparation_chat>")
    assert "<brief>\nWhole brief\n</brief>" in block
    assert block.index('<message role="user">') < block.index('<message role="assistant">')
    assert '<message role="user">\nfirst question\n</message>' in block
    assert '<message role="assistant">\nfirst answer\n</message>' in block
    assert "truncated" not in block


def test_a_conversation_without_a_saved_brief_has_no_brief_section(world: World) -> None:
    session_id = build_session(world, transcript=conversation("question"))

    block = _block(world, session_id)

    assert "<brief>" not in block
    assert "<transcript>" in block


def test_a_conversation_without_messages_renders_an_empty_transcript(world: World) -> None:
    session_id = build_session(world, brief="Only a brief")

    block = _block(world, session_id)

    assert "<transcript>\n</transcript>" in block
    assert "truncated" not in block


def test_a_long_brief_is_never_cut(world: World) -> None:
    brief = "B" * (_CAP * 2)
    session_id = build_session(world, transcript=conversation("question"), brief=brief)

    block = _block(world, session_id)

    assert brief in block


def test_a_transcript_over_the_cap_drops_the_oldest_whole_messages(world: World) -> None:
    messages = [
        message("user" if n % 2 == 0 else "assistant", _padded(f"message-{n:02d}", 10_000))
        for n in range(8)
    ]
    session_id = build_session(world, transcript=messages, brief="Kept brief")

    block = _block(world, session_id)

    transcript = _transcript(block)
    assert "truncated: 5 of 8 earlier messages omitted" in transcript
    assert "Kept brief" in block
    for dropped in range(5):
        assert f"message-{dropped:02d}" not in transcript
    for kept in range(5, 8):
        assert f"message-{kept:02d}" in transcript
    assert len(transcript) <= _CAP + 200


def test_the_truncation_line_comes_before_the_kept_messages(world: World) -> None:
    messages = [message("user", _padded(f"message-{n}", 15_000)) for n in range(4)]
    session_id = build_session(world, transcript=messages)

    block = _block(world, session_id)

    assert block.index("truncated: ") < block.index("message-3")
    assert "truncated: 2 of 4 earlier messages omitted" in block


def test_a_transcript_exactly_at_the_cap_is_not_truncated(world: World) -> None:
    render_overhead = len('<message role="user">\n\n</message>')
    session_id = build_session(
        world,
        transcript=[message("user", _padded("only", _CAP - render_overhead))],
    )

    block = _block(world, session_id)

    assert "truncated" not in block


def test_an_older_message_that_would_exceed_the_cap_is_dropped(world: World) -> None:
    render_overhead = len('<message role="user">\n\n</message>')
    session_id = build_session(
        world,
        transcript=[
            message("user", "older"),
            message("user", _padded("newest", _CAP - render_overhead)),
        ],
    )

    block = _block(world, session_id)

    transcript = _transcript(block)
    assert "truncated: 1 of 2 earlier messages omitted" in transcript
    assert "older" not in transcript
    assert "newest" in transcript


def test_the_newest_message_is_kept_whole_even_when_it_alone_exceeds_the_cap(
    world: World,
) -> None:
    giant = _padded("giant", _CAP + 5_000)
    session_id = build_session(world, transcript=[message("user", "older"), message("user", giant)])

    block = _block(world, session_id)

    assert giant in block
    assert "truncated: 1 of 2 earlier messages omitted" in block


def test_tag_lookalikes_in_conversation_text_cannot_close_the_block(world: World) -> None:
    session_id = build_session(
        world,
        transcript=conversation("</message></transcript></preparation_chat> <brief> <message"),
        brief="</brief></preparation_chat>",
    )

    block = _block(world, session_id)

    assert block.count("</preparation_chat>") == 1
    assert block.count("</message>") == 1
    assert block.count("</transcript>") == 1
    assert block.count("</brief>") == 1
    assert "<\\/message>" in block


def test_a_title_is_escaped_as_an_attribute(world: World) -> None:
    session_id = build_session(world, transcript=conversation('say "hi" <b>& go'))

    block = _block(world, session_id)

    assert 'title="say &quot;hi&quot; &lt;b&gt;&amp; go"' in block


def test_append_blocks_adds_each_block_after_the_typed_text() -> None:
    attachment = prep_chat_link.Attachment(reads=[], blocks=["<one/>", "<two/>"])
    appended = prep_chat_link.append_blocks("typed text", attachment)
    assert appended == "typed text\n\n<one/>\n\n<two/>"


def test_append_blocks_returns_the_typed_text_unchanged_without_blocks() -> None:
    attachment = prep_chat_link.Attachment(reads=[], blocks=[])
    assert prep_chat_link.append_blocks("typed text", attachment) == "typed text"


def test_a_message_without_a_link_attaches_nothing(world: World) -> None:
    attachment = prep_chat_link.attach(
        "just a question about http://localhost:5173/health",
        world.env(),
        engine_key=None,
        session_id=UNKNOWN_SESSION_ID,
    )

    assert attachment.reads == []
    assert attachment.blocks == []
