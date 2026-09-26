"""Pytest configuration is provided by the installed src-layout package."""

import pytest


@pytest.fixture(autouse=True)
def _no_host_gitea(monkeypatch):
    """`agag.project` never looks at the developer's Gitea from a test."""
    monkeypatch.setenv("AGAG_GITEA_URL", "")
