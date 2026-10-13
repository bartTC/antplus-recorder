"""The changelog that ships with the app, for the Changelog entry in the menu."""

from importlib.resources import files
from pathlib import Path

NAME = "CHANGELOG.md"


def read() -> str:
    """The changelog as Markdown.

    The wheel carries a copy inside the package (see `pyproject.toml`); a
    source checkout, where the package is installed as a link to `src`, has it
    at the top of the repository.
    """
    for candidate in (files(__package__) / NAME, Path(__file__).parents[2] / NAME):
        if candidate.is_file():
            return candidate.read_text(encoding="utf-8")
    return "# Changelog\n\nThis installation has no changelog, see the project page."
