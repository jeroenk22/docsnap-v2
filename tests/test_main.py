"""Tests voor de CLI entry point (src/main.py)."""
from click.testing import CliRunner

from src.main import cli


def test_cli_help():
    """CLI --help werkt correct."""
    runner = CliRunner()
    result = runner.invoke(cli, ["--help"])
    assert result.exit_code == 0
    assert "docsnap" in result.output.lower()


def test_cli_missing_url():
    """URL argument is verplicht."""
    runner = CliRunner()
    result = runner.invoke(cli, [])
    assert result.exit_code != 0


def test_cli_invalid_login_option():
    """Ongeldige login optie geeft foutmelding."""
    runner = CliRunner()
    result = runner.invoke(cli, ["https://example.com", "--login", "ssh"])
    assert result.exit_code != 0


def test_cli_invalid_output_option():
    """Ongeldig output formaat geeft foutmelding."""
    runner = CliRunner()
    result = runner.invoke(cli, ["https://example.com", "--output", "xml"])
    assert result.exit_code != 0


def test_cli_valid_options():
    """Geldige opties worden geaccepteerd (help werkt met alle opties)."""
    runner = CliRunner()
    result = runner.invoke(cli, ["--help"])
    assert "--login" in result.output
    assert "--output" in result.output
    assert "--out-dir" in result.output
