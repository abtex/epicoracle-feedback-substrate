"""Executable checks for the package release-version contract."""

from __future__ import annotations

import tomllib
from importlib.metadata import version as distribution_version
from pathlib import Path

import epicoracle_feedback

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def test_project_metadata_and_runtime_versions_match() -> None:
    """Fail before release when any independently exposed version drifts."""
    project = tomllib.loads((PROJECT_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    versions = {
        "pyproject.toml": project["project"]["version"],
        "installed metadata": distribution_version("epicoracle-feedback"),
        "epicoracle_feedback.__version__": epicoracle_feedback.__version__,
    }

    assert len(set(versions.values())) == 1, f"release version drift: {versions}"
