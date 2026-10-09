from pathlib import Path

from typer.testing import CliRunner

from inkforge.cli import app
from inkforge.storage.workspace import Workspace

runner = CliRunner()


def run(*args: str):
    result = runner.invoke(app, list(args))
    assert result.exit_code == 0, result.output
    return result.output


def test_init_backup_restore_reindex_doctor(tmp_path: Path):
    w = str(tmp_path / "ws")
    assert "the-glass-orchard" in run("init", "The Glass Orchard", "-w", w)
    assert "The Glass Orchard" in run("projects", "-w", w)
    run("backup", "-p", "the-glass-orchard", "-w", w)
    zips = list((tmp_path / "ws" / "projects" / "the-glass-orchard" / "backups").glob("*.zip"))
    assert len(zips) == 1
    assert "copy" in run("restore", str(zips[0]), "--as", "copy", "-w", w)
    assert "Rebuilt index for copy" in run("reindex", "-p", "copy", "-w", w)
    assert "FAIL" not in run("doctor", "-w", w)
    assert Workspace(tmp_path / "ws").project_ids() == ["copy", "the-glass-orchard"]


def test_errors_exit_nonzero(tmp_path: Path):
    w = str(tmp_path / "ws")
    assert runner.invoke(app, ["init", "X", "--form", "screenplay", "-w", w]).exit_code == 1
    assert runner.invoke(app, ["reindex", "-p", "missing", "-w", w]).exit_code == 1


def test_workspace_from_env(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("INKFORGE_WORKSPACE", str(tmp_path / "envws"))
    run("init", "Env Project")
    assert (tmp_path / "envws" / "projects" / "env-project" / "project.json").is_file()
