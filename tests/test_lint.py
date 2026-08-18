"""Regression test for Epic 5 (gdocs-native-blockquotes): the `>`-blockquote
lint rule is superseded now that push emits native blockquote styling
instead of literal `>`-prefixed text, so `docspan.cli.lint` — which had
exactly one rule — is removed outright rather than narrowed.
"""

from __future__ import annotations

import importlib

import pytest


def test_find_blockquote_issues_should_not_exist_when_module_imported() -> None:
    with pytest.raises(ModuleNotFoundError):
        importlib.import_module("docspan.cli.lint")
