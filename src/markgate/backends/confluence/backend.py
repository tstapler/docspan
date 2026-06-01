"""Confluence backend — ported from markdown-confluence."""

from markgate.backends.base import Backend, PushResult, PullResult


class ConfluenceBackend(Backend):
    name = "confluence"

    def __init__(self, config: dict):
        self.config = config
        self._client = None

    def _ensure_client(self):
        if self._client is None:
            cfg = self.config.get("backends", {}).get("confluence", {})
            base_url = cfg.get("base_url") or self.config.get("CONFLUENCE_BASE_URL")
            username = cfg.get("username") or self.config.get("ATLASSIAN_USER_NAME")
            api_token = cfg.get("api_token") or self.config.get("CONFLUENCE_API_TOKEN")
            if not all([base_url, username, api_token]):
                raise RuntimeError(
                    "Confluence credentials incomplete. Run: markgate auth setup confluence"
                )
            # Lazy import to avoid hard dependency if only using Google Docs backend
            from markgate.backends.confluence.client import ConfluenceClient
            self._client = ConfluenceClient(base_url, username, api_token)

    def push(self, local_path: str, doc_id: str, **kwargs) -> PushResult:
        """Convert local markdown to ADF and update the Confluence page."""
        self._ensure_client()
        import pathlib
        try:
            content = pathlib.Path(local_path).read_text()
            self._client.update_page(doc_id, content)
            url = f"{self.config.get('backends', {}).get('confluence', {}).get('base_url', '')}/pages/{doc_id}"
            return PushResult(status="ok", doc_id=doc_id, url=url)
        except Exception as e:
            return PushResult(status="error", doc_id=doc_id, message=str(e))

    def pull(self, doc_id: str, local_path: str, **kwargs) -> PullResult:
        """Fetch Confluence page content and write as local markdown."""
        self._ensure_client()
        import pathlib
        try:
            markdown = self._client.get_page_as_markdown(doc_id)
            pathlib.Path(local_path).parent.mkdir(parents=True, exist_ok=True)
            pathlib.Path(local_path).write_text(markdown)
            return PullResult(status="ok", doc_id=doc_id, local_path=local_path)
        except Exception as e:
            return PullResult(
                status="error", doc_id=doc_id, local_path=local_path, message=str(e)
            )

    def auth_setup(self) -> None:
        """Prompt for Confluence base URL, username, and API token."""
        # TODO: interactive wizard writing to markgate.yaml
        raise NotImplementedError("Run: markgate auth setup confluence")

    def validate_config(self, config: dict) -> None:
        cfg = config.get("backends", {}).get("confluence", {})
        missing = [k for k in ["base_url", "username", "api_token"] if not cfg.get(k)]
        if missing:
            raise ValueError(
                f"Missing Confluence config keys: {missing}. "
                "Set them in markgate.yaml under [backends.confluence] or as env vars."
            )
