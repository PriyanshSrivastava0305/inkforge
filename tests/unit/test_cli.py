from typer.testing import CliRunner

from inkforge import __version__
from inkforge.cli import app

runner = CliRunner()


def test_version():
    result = runner.invoke(app, ["version"])
    assert result.exit_code == 0
    assert __version__ in result.stdout


def test_profiles_fast():
    result = runner.invoke(app, ["profiles", "--profile-set", "fast"])
    assert result.exit_code == 0
    assert "llama3.2:1b" in result.stdout
