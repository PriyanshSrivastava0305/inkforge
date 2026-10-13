"""Invented ALL-CAPS scene titles (llama3.2:1b, 2026-10-13) are meta text, not prose."""

import pytest

from inkforge.pipeline.text import clean_scene_text
from inkforge.quality.names import unknown_names


@pytest.mark.parametrize(
    "raw,expected,removed",
    [
        ("THE SOUND OF THE WAVES\n\nAva looked up.", "Ava looked up.", ["title"]),
        ("**THE STORM SEES US THROUGH EYES OF BLINDNESS**\n\nAva looked up.", "Ava looked up.", ["title"]),
        ("NO!\n\nShe ran.", "NO!\n\nShe ran.", []),  # shouted dialogue stays
        ("I AM HERE.\n\nShe ran.", "I AM HERE.\n\nShe ran.", []),  # a sentence, not a title
        (
            "Ava looked up.\n\nSTOP, she thought.\n\nShe ran.",
            "Ava looked up.\n\nSTOP, she thought.\n\nShe ran.",
            [],
        ),
    ],
)
def test_all_caps_title_lines_removed_only_at_the_edges(raw, expected, removed):
    c = clean_scene_text(raw)
    assert c.text == expected and [k for k, _ in c.removed] == removed


def test_all_caps_words_are_not_unknown_names():
    text = "She saw THE sign. Later THE sign fell. Maren came and Maren left with Maren."
    assert unknown_names(text, []) == ["Maren"]
