# Plan: fix-tab-title-validation

## Summary

Add a pre-flight title-length guard in the `map` CLI command for the `--new-tab-in` path.
No backend changes required. Two files touched: `main.py` (guard + constant) and
`test_cli.py` (three new test cases).

## Implementation Steps

### 1. Add constant and guard in `src/docspan/cli/main.py`

**Location:** near line 910, alongside `_H1_PATTERN` and `_FRONTMATTER_PATTERN`.

```python
_TAB_TITLE_MAX_LEN = 50
```

**Location:** inside the `if new_tab_in is not None:` block (currently lines 985–1010),
after `doc_title` is computed (line 1012). Insert immediately after:

```python
    doc_title = title or _default_title(file)
```

Add:

```python
    if new_tab_in is not None and len(doc_title) > _TAB_TITLE_MAX_LEN:
        if title is not None:
            err_console.print(
                f"Tab title is {len(doc_title)} characters; Google Docs limits tab titles to "
                f"{_TAB_TITLE_MAX_LEN}. Shorten the value passed to --title."
            )
        else:
            err_console.print(
                f"Tab title derived from the file's H1 is {len(doc_title)} characters; "
                f"Google Docs limits tab titles to {_TAB_TITLE_MAX_LEN}. "
                f"Use --title to supply a shorter title."
            )
        raise typer.Exit(1)
```

Note: `doc_title` must be computed before this guard. The current line 1012
(`doc_title = title or _default_title(file)`) is already *after* the `new_tab_in` guard
block. Move the guard insertion to after line 1012, *before* the `try:` block at line 1019.

### 2. Add tests in `tests/test_cli.py`

Inside the `TestMapCommand` class, alongside the existing `--new-tab-in` tests:

```python
def test_new_tab_in_rejects_tab_title_longer_than_50_chars_from_h1(self, tmp_path):
    parent_local, _, cfg, parent_mapping = self._new_tab_fixtures(tmp_path)
    new_local = tmp_path / "long_title.md"
    new_local.write_text("# " + "A" * 51 + "\n", encoding="utf-8")  # 51-char H1
    with patch("docspan.cli.main.load_config", return_value=_config(parent_mapping)), \
         patch("docspan.cli.main._get_backend", return_value=FakeBackend()):
        result = self._invoke_new_tab(new_local, parent_local, cfg)
    assert result.exit_code == 1
    assert "50" in result.output
    assert "--title" in result.output

def test_new_tab_in_accepts_tab_title_of_exactly_50_chars(self, tmp_path):
    parent_local, _, cfg, parent_mapping = self._new_tab_fixtures(tmp_path)
    new_local = tmp_path / "exact50.md"
    new_local.write_text("# " + "B" * 50 + "\n", encoding="utf-8")  # 50-char H1
    with patch("docspan.cli.main.load_config", return_value=_config(parent_mapping)), \
         patch("docspan.cli.main._get_backend", return_value=FakeBackend()):
        result = self._invoke_new_tab(new_local, parent_local, cfg)
    assert result.exit_code == 0

def test_new_tab_in_rejects_explicit_title_longer_than_50_chars(self, tmp_path):
    parent_local, new_local, cfg, parent_mapping = self._new_tab_fixtures(tmp_path)
    long_title = "C" * 51
    with patch("docspan.cli.main.load_config", return_value=_config(parent_mapping)), \
         patch("docspan.cli.main._get_backend", return_value=FakeBackend()):
        result = self._invoke_new_tab(
            new_local, parent_local, cfg, extra_args=["--title", long_title]
        )
    assert result.exit_code == 1
    assert "50" in result.output
    assert "Shorten" in result.output
```

## Adversarial Review

**Q: Should the guard also apply to normal `map` (no `--new-tab-in`)?**
A: No. The Google Docs `documents.create` API does not enforce a 50-char title limit.
Only `addDocumentTab` does. Guarding the wrong path would silently reject valid inputs.

**Q: Should we truncate instead of reject?**
A: The item says "fail with a one-line message OR truncate with a warning." Rejection is
safer: it makes the user aware and preserves the title they intended. Truncation can
silently produce a confusing tab name. Rejection is the preferred path; truncation can
be added later if users request it.

**Q: What if `_default_title` returns a basename that is > 50 chars?**
A: The guard covers this too — `doc_title = title or _default_title(file)` produces
the basename fallback, which then hits the same length check.

**Q: Is `_TAB_TITLE_MAX_LEN` the right scope (module-level constant)?**
A: Yes. It's referenced by both the guard in `map_()` and potentially in tests, and the
backend should not own this constraint (it belongs to the CLI guard layer).
