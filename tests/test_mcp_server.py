"""End-to-end stdio test: spawn the real server and speak JSON-RPC to it."""
from __future__ import annotations

import json
import pathlib
import subprocess
import sys

import pytest

pytest.importorskip("mcp", reason="needs the 'mcp' extra (Python >= 3.10)")
pytest.importorskip("mcp.server.mcpserver")

from google.auth.exceptions import RefreshError, TransportError  # noqa: E402

from docspan.mcp.auth import translate_exception  # noqa: E402

TOOLS = {"list_mappings", "fetch_doc", "outline", "read_section", "search_docs",
         "list_comments", "resolve_links", "diff_since"}


def _rpc(tmp_path, calls):
    msgs = [
        {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
            "protocolVersion": "2025-06-18", "capabilities": {},
            "clientInfo": {"name": "t", "version": "0"}}},
        {"jsonrpc": "2.0", "method": "notifications/initialized"},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
    ] + [
        {"jsonrpc": "2.0", "id": 10 + i, "method": "tools/call", "params": {"name": n, "arguments": a}}
        for i, (n, a) in enumerate(calls)
    ]
    here = pathlib.Path(__file__).parent
    proc = subprocess.Popen(
        [sys.executable, str(here / "mcp_stdio_server.py"), str(here), str(tmp_path)],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True)
    replies = {}
    try:
        # One request at a time: closing stdin early makes the server exit mid-call.
        for m in msgs:
            proc.stdin.write(json.dumps(m) + "\n")
            proc.stdin.flush()
            if "id" in m:
                reply = json.loads(proc.stdout.readline())
                replies[reply["id"]] = reply
    finally:
        proc.stdin.close()
        proc.wait(timeout=10)
    return replies


def test_stdio_server_registers_tools_and_survives_auth_expiry(tmp_path):
    r = _rpc(tmp_path, [
        ("outline", {"remote_id_or_url": "DOC1"}),
        ("outline", {"remote_id_or_url": "EXPIRED"}),
        ("read_section", {"remote_id_or_url": "DOC1", "heading_path": "Plan > Overview > Details"}),
        ("outline", {"remote_id_or_url": "DOC1"}),  # after the failure: server still alive
    ])
    assert {t["name"] for t in r[2]["result"]["tools"]} == TOOLS
    ok = json.loads(r[10]["result"]["content"][0]["text"])
    assert ok["url"].startswith("https://docs.google.com/document/d/DOC1")
    assert all("#heading=" in h["url"] and "?tab=" in h["url"] for h in ok["headings"])
    err = r[11]["result"]
    assert err["isError"] is True
    assert json.loads(err["content"][0]["text"].split(": ", 1)[1])["error"]["code"] == "auth_expired"
    section = json.loads(r[12]["result"]["content"][0]["text"])
    assert "gadgets" in section["text"] and "widgets" not in section["text"]
    assert r[13]["result"].get("isError") in (None, False)


def test_translate_exception_maps_credential_failures():
    assert translate_exception(RefreshError("x")).code == "auth_expired"
    assert translate_exception(TransportError("x")).code == "auth_expired"
    assert translate_exception(KeyError("bug")) is None
