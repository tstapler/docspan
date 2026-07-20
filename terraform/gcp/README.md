# docspan GCP project

Creates a dedicated GCP project for docspan's Google OAuth client — separate from
`wedding-toolkit`/other personal projects, so docspan (a published, general-purpose
tool) isn't tangled up in one-off project infra.

```bash
terraform init
terraform plan
terraform apply
```

Enables the Docs, Drive, and Sheets APIs. State is local (`terraform.tfstate`) —
fine for a single-maintainer personal project; not committed (see `.gitignore`).

**What this does NOT automate** (Google provides no API for either — confirmed
2026-07-20; the one API that used to cover part of this, IAP OAuth Admin API, was
shut down 2026-03-19):
- The OAuth consent screen ("brand")
- The Desktop-app OAuth 2.0 client ID / client secret

Run `terraform apply` first, then follow the `next_steps` output for those two
manual, ~2-minute console steps.
