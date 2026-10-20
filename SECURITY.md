# Security Policy

## Reporting a Vulnerability

docspan handles credentials (Google service account/OAuth tokens, Confluence
API tokens) and syncs document content to external services, so please
report security issues privately rather than opening a public issue.

- Preferred: open a [GitHub private security advisory](https://github.com/tstapler/docspan/security/advisories/new)
- Alternative: email tystapler@gmail.com

Please include:

- A description of the vulnerability and its potential impact
- Steps to reproduce (or a proof of concept)
- The docspan version and backend (Google Docs / Confluence) affected

You should expect an initial response within a few days. Please don't
disclose the issue publicly until a fix has been released.

## Supported Versions

docspan is pre-1.0 (`0.x`). Security fixes are made against the latest
released version; there is no separate long-term-support branch.
