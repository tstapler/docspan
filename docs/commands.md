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

For `google_docs`: prints step-by-step service account setup instructions. If credentials are already configured, tests the connection.

For `confluence`: prompts interactively for base URL, username, and API token, then prints a YAML snippet to add to `markgate.yaml`.

**Example:**

```bash
docspan auth setup google_docs
docspan auth setup confluence
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
