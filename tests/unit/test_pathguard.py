import os
import sys
from pathlib import Path

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from inkforge.policy.pathguard import PathEscapeError, resolve_inside

ESCAPES = [
    "../outside",
    "..\\outside",
    "a/../../outside",
    "a\\..\\..\\outside",
    "a/b/../../../c",
    "/etc/passwd",
    "\\Windows\\System32",
    "C:\\Windows",
    "C:outside",
    "c:/x",
    "\\\\server\\share\\x",
    "//server/share",
    "",
    ".",
    "./",
    "a/./..",
    "nul",
    "CON",
    "con.txt",
    "a/aux/b",
    "COM1",
    "lpt9.log",
    "file.txt:stream",
    "a/b:$DATA",
    "trailing.",
    "trailing ",
    "a/b /c",
    "x\x00y",
    "a?b",
    "a*b",
    'a"b',
    "a<b",
    "a|b",
]


@pytest.mark.parametrize("rel", ESCAPES)
def test_rejects_escapes(tmp_path: Path, rel: str):
    with pytest.raises(PathEscapeError):
        resolve_inside(tmp_path, rel)


@pytest.mark.parametrize(
    "rel",
    [
        "a.txt",
        "scenes/ch01-sc01/card.v1.json",
        "a\\b\\c.md",
        "./a/b",
        "a//b",
        "codex/characters/char-mira.json",
    ],
)
def test_allows_normal_paths(tmp_path: Path, rel: str):
    p = resolve_inside(tmp_path, rel)
    assert tmp_path.resolve() in p.resolve().parents


def _make_junction(link: Path, target: Path) -> None:
    import _winapi  # type: ignore[import-not-found]

    _winapi.CreateJunction(str(target), str(link))


@pytest.mark.skipif(sys.platform != "win32", reason="NTFS junctions are Windows-only")
def test_rejects_junction_escape(tmp_path: Path):
    root, outside = tmp_path / "root", tmp_path / "outside"
    root.mkdir()
    outside.mkdir()
    _make_junction(root / "j", outside)
    assert (root / "j").is_junction()
    with pytest.raises(PathEscapeError):
        resolve_inside(root, "j/secret.txt")
    with pytest.raises(PathEscapeError):
        resolve_inside(root, "j")


@pytest.mark.skipif(sys.platform != "win32", reason="NTFS junctions are Windows-only")
def test_rejects_internal_junction_too(tmp_path: Path):
    """Even a link that stays inside the root is refused: no links at all inside projects."""
    root = tmp_path / "root"
    (root / "real").mkdir(parents=True)
    _make_junction(root / "j", root / "real")
    with pytest.raises(PathEscapeError):
        resolve_inside(root, "j/x")


def test_rejects_symlink(tmp_path: Path):
    root, outside = tmp_path / "root", tmp_path / "outside"
    root.mkdir()
    outside.mkdir()
    try:
        os.symlink(outside, root / "s", target_is_directory=True)
    except OSError:
        pytest.skip("symlink creation needs Developer Mode/admin on Windows")
    with pytest.raises(PathEscapeError):
        resolve_inside(root, "s/x")


def test_rejects_root_that_is_a_link(tmp_path: Path):
    real = tmp_path / "real"
    real.mkdir()
    if sys.platform == "win32":
        _make_junction(tmp_path / "linkroot", real)
    else:
        os.symlink(real, tmp_path / "linkroot")
    with pytest.raises(PathEscapeError):
        resolve_inside(tmp_path / "linkroot", "a")


@settings(max_examples=400, deadline=None)
@given(
    st.lists(st.sampled_from([*"ab./\\:~$ C\x00?*", "..", "con", "C:", "nul", "x"]), max_size=12).map("".join)
)
def test_property_never_escapes(tmp_path_factory, rel):
    root = tmp_path_factory.getbasetemp() / "prop-root"
    root.mkdir(exist_ok=True)
    try:
        p = resolve_inside(root, rel)
    except PathEscapeError:
        return
    real_root = Path(os.path.realpath(root))
    real = Path(os.path.realpath(p))
    assert real == real_root or real_root in real.parents
