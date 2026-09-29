# Command Reference

## `docspan push`

```
docspan push [FILES]... [OPTIONS]
```

Push local markdown files to remote docs.

**Arguments:**

| Argument | Description |
|---|---|
| `FILES` | Optional list of local file paths to push. Defaults to all mappings. |

**Options:**

| Option | Description |
|---|---|
| `--config`, `-c` TEXT | Path to `markgate.yaml` |
| `--dry-run` | Preview changes without writing to the remote |

**Behavior:**

- Skips mappings with `direction = "pull"` (prints a dim "Skipping" message)
- With `--dry-run`: prints what would be pushed without making any remote changes
- On success: prints a green checkmark and the remote URL
- On error: prints a red X and the error message; exits with code 1

**Example:**

```bash
docspan push
docspan push docs/design-doc.md --dry-run
```

---

## `docspan pull`

```
docspan pull [FILES]... [OPTIONS]
```

Pull remote documents into local markdown files.

**Arguments:**

| Argument | Description |
|---|---|
| `FILES` | Optional list of local file paths to pull into. Defaults to all mappings. |

**Options:**

| Option | Description |
|---|---|
| `--config`, `-c` TEXT | Path to `markgate.yaml` |
| `--dry-run` | Fetches the remote and classifies what a real pull would do — up to date, local-only, would fast-forward, would first-sync, or would merge (with a real conflict count) — without writing anything |

**Behavior:**

- Skips mappings with `direction = "push"`
- Detects whether local or remote has changed since last sync
- Outcomes:
  - `up-to-date` — no changes on either side
  - `local-only` — local has changes not yet pushed; pull is skipped with a warning
  - `fast-forward` / `first-sync` — remote changed (or no sync state yet); writes remote content locally
  - `merged` (clean) — both sides changed; three-way merge succeeded
  - `merged` (conflicts) — both sides changed; merge produced conflict markers in the local file
  - `error` — remote fetch or write failed

**Example:**

```bash
docspan pull
docspan pull docs/design-doc.md
docspan pull --dry-run
```

---

## `docspan map`

```
docspan map FILE --backend BACKEND [OPTIONS]
```

Create a new remote Google Doc / Confluence page and map it to a local file.

**Arguments:**

| Argument | Description |
|---|---|
| `FILE` | Local markdown file to map to a new remote doc/page |

**Options:**

| Option | Description |
|---|---|
| `--backend`, `-b` TEXT (required) | `google_docs` or `confluence` |
| `--space` TEXT | Confluence space key (required for `confluence` unless set in `markgate.yaml`) |
| `--title` TEXT | Title for the new doc/page (default: the file's first H1 heading, else its basename) |
| `--direction` TEXT | `push`, `pull`, or `both` (default `both`) |
| `--tab-id` TEXT | Google Docs tab id to target |
| `--new-tab-in` TEXT | Local file of an existing `google_docs` mapping; create a new tab in that doc instead of a new doc, and map this file to it. Requires `--backend google_docs`; mutually exclusive with `--tab-id` |
| `--config`, `-c` TEXT | Path to `markgate.yaml` |
| `--prefix`, `-p` TEXT | Central-config project prefix |

**Behavior:**

- Fails if `FILE` is already mapped, or if the backend is unknown
- A freshly created doc/page is always empty, so if `FILE` already exists locally, its content is pushed immediately after the mapping is created — regardless of `--direction` — so a `pull`-only mapping doesn't get overwritten by the empty remote doc on its first pull
- If saving `markgate.yaml` hits a conflict (someone else edited it concurrently), the remote doc/page was still created — the error prints its id/url so you can add the mapping by hand

**Example:**

```bash
docspan map docs/design-doc.md --backend google_docs
docspan map docs/page.md --backend confluence --space ENG
docspan map docs/appendix.md --backend google_docs --new-tab-in docs/design-doc.md
```

---

## `docspan migrate-sectioned`

```
docspan migrate-sectioned FILE [OPTIONS]
```

Migrate a single-file mapping to a sectioned mapping — splitting the doc into one local file per section on a heading boundary — preserving git history via a git commit.

**Arguments:**

| Argument | Description |
|---|---|
| `FILE` | Local markdown file to migrate to a sectioned mapping |

**Options:**

| Option | Description |
|---|---|
| `--dry-run` | Preview the split without writing |
| `--split-level` TEXT | Heading level to split on, e.g. `HEADING_1`, `HEADING_2` (default `HEADING_2`) |
| `--config`, `-c` TEXT | Path to `markgate.yaml` |
| `--prefix`, `-p` TEXT | Central-config project prefix |

**Behavior:**

- Refuses if the mapping is already sectioned, or if its backend doesn't support sectioned mode
- On success, replaces the single-file mapping with a directory of per-section files and commits the change

**Example:**

```bash
docspan migrate-sectioned docs/design-doc.md --dry-run
docspan migrate-sectioned docs/design-doc.md --split-level HEADING_1
```

---

## `docspan style-guide`

```
docspan style-guide [OPTIONS]
```

Print backend authoring guidance (e.g. "one image per line on google_docs"). Ships inside the installed package, so re-running it after a `docspan` upgrade picks up new guidance without hand-copying anything.

**Options:**

| Option | Description |
|---|---|
| `--backend`, `-b` TEXT | `google_docs`, `confluence`, or omit for all backends |
| `--write`, `-w` TEXT | File to write/update (e.g. a project `CLAUDE.md`). Updates docspan's managed block in place if the file already has one; appends otherwise |

**Example:**

```bash
docspan style-guide --backend google_docs
docspan style-guide --write CLAUDE.md
```

---

## `docspan comments respond`

```
docspan comments respond FILE [OPTIONS]
```

Post `Reply:`/`Resolve:` directives from a `{file}.comments.md` sidecar back to the remote doc.

**Arguments:**

| Argument | Description |
|---|---|
| `FILE` | Local markdown file whose `.comments.md` sidecar to process |

**Options:**

| Option | Description |
|---|---|
| `--config`, `-c` TEXT | Path to `markgate.yaml` |
| `--prefix`, `-p` TEXT | Central-config project prefix |

**Behavior:**

- Edit the sidecar's `Reply:` lines and/or flip `Resolve: no` to `Resolve: yes` under an open comment, then run this to post those replies/resolutions and refresh the sidecar with the result
- Fails if the mapping's backend doesn't support comment replies

**Example:**

```bash
docspan comments respond docs/design-doc.md
```

---

## `docspan sync`

```
docspan sync [FILES]... [OPTIONS]
```

Pull then push each mapping — the safe default order.

**Arguments:**

| Argument | Description |
|---|---|
| `FILES` | Optional list of local file paths to sync. Defaults to all mappings. |

**Options:**

| Option | Description |
|---|---|
| `--config`, `-c` TEXT | Path to `markgate.yaml` |
| `--force` | Proceed with a push even if it flags a comment-risk paragraph |

**Behavior:**

- For each mapping: runs a real `pull`, then, unless the pull left unresolved merge conflicts, runs a real `push`.
- A mapping whose pull produced conflicts is reported and **not** pushed — resolve with `docspan conflicts resolve` and re-run.
- `direction = "pull"` mappings are only pulled; `direction = "push"` mappings are only pushed.
- Exits non-zero if anything still needs `docspan conflicts resolve`, or if any push/pull failed.

**Example:**

```bash
docspan sync
docspan sync docs/design-doc.md
```

---

## `docspan status`

```
docspan status [OPTIONS]
```

Display all configured mappings in a table.

**Options:**

| Option | Description |
|---|---|
| `--config`, `-c` TEXT | Path to `markgate.yaml` |

**Output columns:** Local file, Backend, Remote ID, Direction.

**Example:**

```bash
docspan status
```

---

## `docspan auth setup`

```
docspan auth setup BACKEND [OPTIONS]
```

Interactive authentication setup for a backend.

**Arguments:**

| Argument | Description |
|---|---|
| `BACKEND` | Backend to configure: `google_docs` or `confluence` |

**Options:**

| Option | Description |
|---|---|
| `--config`, `-c` TEXT | Path to `markgate.yaml` |
| `--oauth` | Use per-user OAuth (`google_docs`) instead of a service account |
| `--client-secret` TEXT | Path to an OAuth client secret JSON (`google_docs`, with `--oauth`) |

For `google_docs` with no flags: a guided flow that detects your current state, lets you pick **Personal (OAuth)** or **Service account**, auto-detects a `client_secret.json` (scanning `.`, `.markgate/`, `~/Downloads`) or prompts for the path, runs the browser sign-in, verifies the connection, and offers to persist the choice into `markgate.yaml`. In a non-TTY/CI environment it prints manual instructions instead of prompting. `--oauth --client-secret PATH` selects the OAuth path non-interactively.

For `confluence`: prompts interactively for base URL, username, and API token, then prints a YAML snippet to add to `markgate.yaml`.

**Example:**

```bash
docspan auth setup google_docs
docspan auth setup google_docs --oauth --client-secret ~/Downloads/client_secret.json
docspan auth setup confluence
```

---

## `docspan config show`

```
docspan config show
```

Show the central config (registered projects) and the active resolution — no options.

**Output:** the central config path, `default_prefix`, and a table of registered `prefix → markgate.yaml` pairs.

**Example:**

```bash
docspan config show
```

---

## `docspan config add`

```
docspan config add PREFIX MARKGATE [OPTIONS]
```

Register a project (prefix → `markgate.yaml`) in the central config.

**Arguments:**

| Argument | Description |
|---|---|
| `PREFIX` | Project prefix (name) |
| `MARKGATE` | Path to that project's `markgate.yaml` |

**Options:**

| Option | Description |
|---|---|
| `--default` | Also set as `default_prefix` |

**Example:**

```bash
docspan config add design-docs ~/Documents/design-docs/markgate.yaml --default
```

---

## `docspan migrate-xdg`

```
docspan migrate-xdg --prefix PREFIX [OPTIONS]
```

Move legacy in-repo storage (`.markgate-state.json`, `.markgate-base/`) to XDG state storage and register the project under `PREFIX` in the central config.

**Options:**

| Option | Description |
|---|---|
| `--prefix`, `-p` TEXT (required) | Prefix to migrate this project's storage into |
| `--config`, `-c` TEXT | Path to `markgate.yaml` |

**Behavior:**

- Refuses to overwrite an existing file at the XDG destination
- Registers `prefix → markgate.yaml` in the central config, setting it as `default_prefix` if none is set yet

**Example:**

```bash
docspan migrate-xdg --prefix design-docs
```

---

## `docspan conflicts list`

```
docspan conflicts list [OPTIONS]
```

Scan all tracked files for unresolved merge conflict markers.

**Options:**

| Option | Description |
|---|---|
| `--config`, `-c` TEXT | Path to `markgate.yaml` |

**Output:** A table showing each file with conflict markers and the number of conflict blocks. Prints "No unresolved conflicts." when none are found.

**Example:**

```bash
docspan conflicts list
```

---

## `docspan conflicts resolve`

```
docspan conflicts resolve FILE [OPTIONS]
```

Resolve a merge conflict in a tracked file.

**Arguments:**

| Argument | Description |
|---|---|
| `FILE` | Local file path to resolve |

**Options:**

| Option | Description |
|---|---|
| `--accept` TEXT (required) | Resolution strategy: `remote`, `local`, or `merged` |
| `--config`, `-c` TEXT | Path to `markgate.yaml` |

**Strategies:**

| Value | Behavior |
|---|---|
| `remote` | Re-fetches the remote version and overwrites the local file; updates sync state |
| `local` | Restores pre-merge local content from the `.orig` backup file; updates sync state |
| `merged` | Accepts the current file as the resolved version (all conflict markers must be removed first); updates sync state |

**Example:**

```bash
# Accept the remote version
docspan conflicts resolve docs/design-doc.md --accept remote

# Keep the local pre-merge version
docspan conflicts resolve docs/design-doc.md --accept local

# Accept a manually resolved file
# (edit the file to remove conflict markers first, then run:)
docspan conflicts resolve docs/design-doc.md --accept merged
```
