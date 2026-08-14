"""ConfluenceBackend.push() reports internal-anchor links instead of silently
writing them as dead `#fragment` hrefs.

Runs the real push pipeline with the HTTP transport intercepted, so this covers
the wiring in backend.py (not just the anchors.py module in isolation) and
confirms the href sent to Confluence is unchanged — only the PushResult differs.
"""

from __future__ import annotations

import json
from unittest.mock import patch

from docspan.backends.confluence.backend import ConfluenceBackend
from docspan.config import ConfluenceConfig

ANCHOR_MARKDOWN = """# Page

See [A1](#a1-current-state) for details.
"""

NO_ANCHOR_MARKDOWN = """# Page

See [the docs](https://example.com/docs) for details.
"""


def _fake_response(status_code: int, payload: dict):
    resp = __import__("requests").Response()
    resp.status_code = status_code
    resp._content = json.dumps(payload).encode("utf-8")
    resp.headers["Content-Type"] = "application/json"
    return resp


def _push(tmp_path, markdown: str):
    md_path = tmp_path / "page.md"
    md_path.write_text(markdown)

    get_page_response = _fake_response(
        200,
        {
            "id": "123456",
            "title": "Page",
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

    return result, captured_put_calls


def test_a_dead_anchor_link_is_reported_as_a_warning(tmp_path) -> None:
    result, put_calls = _push(tmp_path, ANCHOR_MARKDOWN)

    assert result.status == "warning"
    assert result.message is not None
    assert "#a1-current-state" in result.message

    # The literal `#fragment` href still reaches Confluence unchanged — only the
    # PushResult status/message changes, no anchor resolution is attempted.
    put_body = put_calls[0]["json"]
    adf_doc = json.loads(put_body["body"]["editor"]["value"])
    assert "#a1-current-state" in json.dumps(adf_doc)


def test_a_push_with_no_anchor_links_stays_ok(tmp_path) -> None:
    result, _ = _push(tmp_path, NO_ANCHOR_MARKDOWN)

    assert result.status == "ok"
    assert result.message is None
