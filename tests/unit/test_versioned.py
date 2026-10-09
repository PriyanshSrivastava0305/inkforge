from pathlib import Path

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from inkforge.storage.versioned import ArtifactSlot


@pytest.fixture
def slot(tmp_path: Path) -> ArtifactSlot:
    return ArtifactSlot(tmp_path / "scenes" / "ch01-sc01", "draft", "md")


def test_write_read_versions(slot: ArtifactSlot):
    r1 = slot.write(b"one")
    r2 = slot.write(b"two", input_hashes={"card:ch01-sc01": "abc"})
    assert (r1.version, r2.version, r2.parent) == (1, 2, 1)
    assert slot.read_text() == "two" and slot.read_text(1) == "one"
    assert slot.meta().get(2).input_hashes == {"card:ch01-sc01": "abc"}
    assert slot.path_for(1).read_bytes() == b"one"  # old versions untouched


def test_approve_and_set_active(slot: ArtifactSlot):
    slot.write(b"a")
    slot.write(b"b")
    assert slot.approve(1) == 1
    meta = slot.meta()
    assert (meta.active, meta.approved) == (2, 1)
    slot.set_active(1)
    assert slot.read_text() == "a"
    with pytest.raises(KeyError):
        slot.approve(7)
    slot.unapprove()
    assert slot.meta().approved is None


def test_versions_are_immutable(slot: ArtifactSlot):
    slot.write(b"a")
    from inkforge.storage.atomic import write_new_file

    with pytest.raises(FileExistsError):
        write_new_file(slot.path_for(1), b"overwrite")


def test_tampering_detected(slot: ArtifactSlot):
    slot.write(b"original")
    slot.path_for(1).write_bytes(b"edited behind our back")
    with pytest.raises(ValueError, match="hash"):
        slot.read_bytes()


def test_orphan_from_crash_is_skipped_not_overwritten(slot: ArtifactSlot):
    slot.write(b"v1")
    slot.path_for(2).write_bytes(b"orphan: content written, meta never updated")
    assert slot.orphans() == [slot.path_for(2)]
    r = slot.write(b"v3")
    assert r.version == 3
    assert slot.path_for(2).read_bytes().startswith(b"orphan")


def test_empty_slot(slot: ArtifactSlot):
    assert not slot.exists() and slot.active_sha() is None
    with pytest.raises(FileNotFoundError):
        slot.read_bytes()
    with pytest.raises(ValueError):
        slot.approve()


ops = st.lists(
    st.one_of(
        st.tuples(st.just("write"), st.binary(max_size=20)),
        st.tuples(st.just("approve"), st.integers(1, 8)),
        st.tuples(st.just("activate"), st.integers(1, 8)),
    ),
    max_size=25,
)


@settings(max_examples=60, deadline=None, suppress_health_check=[HealthCheck.function_scoped_fixture])
@given(ops)
def test_property_pointer_invariants(tmp_path_factory, sequence):
    slot = ArtifactSlot(tmp_path_factory.mktemp("p"), "card", "json")
    written: list[bytes] = []
    for op, arg in sequence:
        if op == "write":
            slot.write(arg)
            written.append(arg)
        else:
            try:
                slot.approve(arg) if op == "approve" else slot.set_active(arg)
            except (KeyError, ValueError):
                assert arg > len(written)
    meta = slot.meta()
    versions = [v.version for v in meta.versions]
    assert versions == list(range(1, len(written) + 1))
    assert meta.active is None if not written else meta.active in versions
    assert meta.approved is None or meta.approved in versions
    for i, data in enumerate(written, 1):
        assert slot.read_bytes(i) == data
