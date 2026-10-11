from hypothesis import given, settings
from hypothesis import strategies as st

from inkforge.domain.models import Character, Location
from inkforge.pipeline.text import (
    clean_scene_text,
    diff_stats,
    ends_mid_sentence,
    join_without_overlap,
    meta_leaks,
    word_diff,
)
from inkforge.quality.names import missing_participants, name_inconsistencies, unknown_names

ASTRID = Character(id="char-astrid-larsen", name="Astrid Larsen", aliases=["Asta"])
ERIK = Character(id="char-erik-hansen", name="Erik Hansen")
HARBOR = Location(id="loc-saltmere", name="Saltmere")


def test_join_strips_repeated_anchor():
    a = "The storm came in. She climbed the stairs and opened the"
    assert join_without_overlap(a, "climbed the stairs and opened the door. Light spilled out.") == (
        "The storm came in. She climbed the stairs and opened the door. Light spilled out."
    )
    assert join_without_overlap("She ran.", "He followed.") == "She ran. He followed."
    assert join_without_overlap("She ran.\n\n", "He followed.") == "She ran.\n\nHe followed."


def test_mid_sentence_detection():
    assert not ends_mid_sentence('She said, "Go."')
    assert not ends_mid_sentence("Was it over?")
    assert not ends_mid_sentence("The end…")
    assert ends_mid_sentence("and then the")
    assert ends_mid_sentence('"Wait," she')


def test_clean_scene_text_removes_meta_only_at_edges():
    raw = "Here is the scene:\n\n## Scene 1\n\nAstrid climbed.\n\nChapter 3 was the name of the inn.\n\n---\nNote: 450 words"
    c = clean_scene_text(raw)
    assert c.text == "Astrid climbed.\n\nChapter 3 was the name of the inn."
    assert [k for k, _ in c.removed] == ["preamble", "heading", "note", "rule"]
    assert meta_leaks("She paused. As an AI, I cannot continue.") != []
    assert meta_leaks("She paused at the window.") == []


@settings(max_examples=150)
@given(st.text(alphabet="ab cd\n.", max_size=60), st.text(alphabet="ab cd\n.", max_size=60))
def test_word_diff_reconstructs_both_texts(a, b):
    ops = word_diff(a, b)
    assert "".join(o.text for o in ops if o.op != "insert") == a
    assert "".join(o.text for o in ops if o.op != "delete") == b


def test_diff_stats():
    assert diff_stats(word_diff("the old grey sea", "the grey sea rose")) == {
        "equal": 3,
        "insert": 1,
        "delete": 1,
    }


def test_name_typos_found_with_spans():
    text = "Astird climbed. Erik waited for Astrid. Larson laughed. Eric nodded in Saltmore."
    hits = name_inconsistencies(text, [ASTRID, ERIK, HARBOR])
    assert [(h.found, h.expected) for h in hits] == [
        ("Astird", "Astrid"),
        ("Larson", "Larsen"),
        ("Eric", "Erik"),
        ("Saltmore", "Saltmere"),
    ]
    for h in hits:
        assert text[h.span[0] : h.span[1]] == h.found


def test_no_false_positives_on_common_words_and_aliases():
    text = "After the storm, Asta and Erik walked. Later they ate. Every Tuesday they waited. Astrid's coat dripped."
    assert name_inconsistencies(text, [ASTRID, ERIK]) == []


def test_unknown_names_and_missing_participants():
    text = "Astrid saw Maren at dawn. Later, with Maren gone, she wrote to Maren's mother."
    assert unknown_names(text, [ASTRID, ERIK]) == ["Maren"]
    assert [c.id for c in missing_participants(text, [ASTRID, ERIK])] == ["char-erik-hansen"]


def test_possessives_are_not_misspellings():
    """Real 1B run (2026-10-10): rstrip("'s") turned "James's" into "Jame" → 150 false findings."""
    james = Character(id="char-captain-james", name="Captain James")
    text = "She'd grown used to Captain James's moods. James's eyes narrowed. Ross' boat. Jame waited."
    hits = name_inconsistencies(text, [james])
    assert [h.found for h in hits] == ["Jame"]  # only the real typo
    assert unknown_names("Jess's coat. Later Jess's boots.", [james]) == ["Jess"]


def test_timestamp_lines_removed_anywhere():
    raw = "2023-02-20 00:05:00\nAva typed.\n\n2023-02-20 00:10:00\nShe turned.\n[14:05]\nHe slept at 2023-02-20 in the log."
    c = clean_scene_text(raw)
    assert c.text == "Ava typed.\n\nShe turned.\nHe slept at 2023-02-20 in the log."
    assert [k for k, _ in c.removed] == ["timestamp", "timestamp", "timestamp"]
