import pathlib
import sys

sys.path.insert(0, sys.argv[1])
from google.auth.exceptions import RefreshError
from test_mcp_query import FakeClient, make_doc

from docspan.mcp.index import DocspanIndex
from docspan.mcp.server import build_server
from docspan.mcp.service import DocService


class Expiring(FakeClient):
    def get_document(self, doc_id, include_tabs_content=True):
        if doc_id == "EXPIRED":
            raise RefreshError("token revoked")
        return super().get_document(doc_id)

svc = DocService(lambda: Expiring({"DOC1": make_doc()}), pathlib.Path(sys.argv[2]), index=DocspanIndex(":memory:"))
build_server(svc).run(transport="stdio")
