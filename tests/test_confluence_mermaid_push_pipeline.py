"""
Executes the real Confluence push pipeline (markdown -> ADF -> HTTP PUT) with the
`requests.Session` transport intercepted, so the ADF node type actually sent for a
mermaid fence can be asserted without a live Confluence space or credentials.

This exists because AC3 of backlog item 956b8bee (push a real mermaid-fence doc to a
test Confluence space and verify via API what ADF node type appears) could not be
executed literally: no test Confluence credentials are available in this sandbox, and
credential lookup (1Password/keychain) was blocked by the environment. This test is the
closest available substitute — it runs the unmodified production code path end to end
and inspects the JSON body that would be PUT to the real Confluence REST API, rather
than only reading the code.
"""

from __future__ import annotations

import json
from unittest.mock import patch

from docspan.backends.confluence.backend import ConfluenceBackend
from docspan.config import ConfluenceConfig

MERMAID_MARKDOWN = """# Diagram page

```mermaid
graph TD
    A --> B
```
"""


def _fake_response(status_code: int, payload: dict):
    resp = __import__("requests").Response()
    resp.status_code = status_code
    resp._content = json.dumps(payload).encode("utf-8")
    resp.headers["Content-Type"] = "application/json"
    return resp


def test_mermaid_fence_pushes_as_plain_code_block_not_a_macro(tmp_path) -> None:
    """
    Runs ConfluenceBackend.push() against a mermaid-fence doc with the HTTP transport
    mocked, and asserts the ADF node the real pipeline sends for the fence is a plain
    `codeBlock` (language=mermaid) rather than a native Confluence mermaid macro node
    (`extension`/`bodiedExtension` with extensionKey containing "mermaid") or an image
    (`media`) node.
    """
    md_path = tmp_path / "diagram.md"
    md_path.write_text(MERMAID_MARKDOWN)

    get_page_response = _fake_response(
        200,
        {
            "id": "123456",
            "title": "Diagram page",
            "version": {"number": 1},
            "space": {"key": "TEST"},
        },
    )
    captured_put_calls = []

    def fake_request(self, method, url, **kwargs):
        if method == "GET":
            return get_page_response
        if method == "PUT":
            captured_put_calls.append(kwargs)
            return _fake_response(200, {"id": "123456", "version": {"number": 2}})
        raise AssertionError(f"Unexpected HTTP method in test: {method} {url}")

    config = ConfluenceConfig(
        base_url="https://example.atlassian.net",
        username="test@example.com",
        api_token="test-token",
    )
    backend = ConfluenceBackend(config)

    with patch("requests.Session.request", fake_request):
        result = backend.push(str(md_path), "123456")

    assert result.status == "ok", result.message
    assert len(captured_put_calls) == 1, "expected exactly one PUT (page update) call"

    put_body = captured_put_calls[0]["json"]
    editor_value = put_body["body"]["editor"]["value"]
    adf_doc = json.loads(editor_value)

    mermaid_related_nodes = [
        node
        for node in adf_doc.get("content", [])
        if "mermaid" in json.dumps(node).lower()
    ]
    assert mermaid_related_nodes, "expected at least one ADF node referencing the mermaid fence"

    node = mermaid_related_nodes[0]
    assert node["type"] == "codeBlock", (
        f"expected the mermaid fence to degrade to a plain codeBlock, got node type "
        f"{node['type']!r} — if this changes, MermaidNodeConverter has started emitting "
        f"something other than the fallback path (see converters.py fallback at the end "
        f"of MermaidNodeConverter.convert_typed)"
    )
    assert node.get("attrs", {}).get("language") == "mermaid"
