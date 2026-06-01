"""Google Docs backend — wraps the auth/client/converter modules from the fork."""

from markgate.backends.base import Backend, PushResult, PullResult


class GoogleDocsBackend(Backend):
    name = "google_docs"

    def __init__(self, config: dict):
        self.config = config
        self._auth = None
        self._client = None

    def _ensure_auth(self):
        if self._client is None:
            from markgate.backends.google_docs.auth import DualAccountAuth
            from markgate.backends.google_docs.client import GoogleDocsClient
            auth = DualAccountAuth()
            if not auth.is_authenticated():
                raise RuntimeError(
                    "Google Docs credentials not found. Run: markgate auth setup google_docs"
                )
            self._client = GoogleDocsClient(auth.get_account_a_credentials())

    def push(self, local_path: str, doc_id: str, **kwargs) -> PushResult:
        """Convert local markdown to Google Docs format and update the document."""
        self._ensure_auth()
        # TODO: implement markdown → Docs API write
        raise NotImplementedError("Google Docs push not yet implemented")

    def pull(self, doc_id: str, local_path: str, **kwargs) -> PullResult:
        """Export Google Doc as HTML, convert to markdown, write locally."""
        self._ensure_auth()
        from markgate.backends.google_docs.converter import MarkdownConverter
        import pathlib

        try:
            html_content = self._client.export_as_html(doc_id)
            converter = MarkdownConverter()
            markdown_content = converter.html_to_markdown(html_content)
            pathlib.Path(local_path).parent.mkdir(parents=True, exist_ok=True)
            pathlib.Path(local_path).write_text(markdown_content)
            return PullResult(status="ok", doc_id=doc_id, local_path=local_path)
        except Exception as e:
            return PullResult(
                status="error", doc_id=doc_id, local_path=local_path, message=str(e)
            )

    def auth_setup(self) -> None:
        """Interactive OAuth setup for Google account(s)."""
        # TODO: interactive wizard
        raise NotImplementedError("Run: markgate auth setup google_docs")

    def validate_config(self, config: dict) -> None:
        required = ["google_docs"]
        for key in required:
            if key not in config.get("backends", {}):
                raise ValueError(f"Missing [backends.{key}] in markgate.yaml")
