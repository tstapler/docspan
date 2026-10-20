# docspan v0.1.0 Release Checklist

Run these commands in order after all PRs are merged to main.

## Pre-release
- [ ] All 134+ tests pass: `pytest`
- [ ] Ruff clean: `ruff check src/`
- [ ] MkDocs builds: `mkdocs build --strict`
- [ ] Verify version in pyproject.toml is "0.1.0" (or hatch-vcs resolves from tag)

## Tag
```bash
git tag -a v0.1.0 -m "v0.1.0 — Initial release"
git push origin v0.1.0
```

## Build and verify
```bash
uv build
twine check dist/*
# Verify wheel version (not Python import):
unzip -p dist/docspan-0.1.0-py3-none-any.whl docspan-0.1.0.dist-info/METADATA | grep "^Version:"
```

## TestPyPI (optional but recommended)
```bash
twine upload --repository testpypi dist/*
pip install --index-url https://test.pypi.org/simple/ --extra-index-url https://pypi.org/simple/ docspan==0.1.0
docspan --help
```

## PyPI
```bash
uv publish
```

## GitHub Release
```bash
gh release create v0.1.0 dist/* --title "v0.1.0 — Initial release" --notes-file CHANGELOG.md
```

## Docs
```bash
mkdocs gh-deploy --force
```
