"""Confluence REST API client — stub, to be ported from markdown-confluence."""

import requests
from requests.auth import HTTPBasicAuth


class ConfluenceClient:
    def __init__(self, base_url: str, username: str, api_token: str):
        self.base_url = base_url.rstrip("/")
        self.auth = HTTPBasicAuth(username, api_token)
        self.session = requests.Session()
        self.session.auth = self.auth
        self.session.headers.update({"Content-Type": "application/json"})

    def get_page(self, page_id: str) -> dict:
        url = f"{self.base_url}/wiki/rest/api/content/{page_id}?expand=body.storage,version"
        resp = self.session.get(url)
        resp.raise_for_status()
        return resp.json()

    def get_page_as_markdown(self, page_id: str) -> str:
        """Fetch page storage format and convert to markdown."""
        page = self.get_page(page_id)
        storage_html = page["body"]["storage"]["value"]
        # TODO: port ADF/storage → markdown conversion from markdown-confluence
        return storage_html  # placeholder

    def update_page(self, page_id: str, markdown_content: str) -> dict:
        """Convert markdown to storage format and update the page."""
        page = self.get_page(page_id)
        current_version = page["version"]["number"]
        title = page["title"]
        # TODO: port markdown → ADF conversion from markdown-confluence
        storage_value = markdown_content  # placeholder
        payload = {
            "version": {"number": current_version + 1},
            "title": title,
            "type": "page",
            "body": {"storage": {"value": storage_value, "representation": "storage"}},
        }
        url = f"{self.base_url}/wiki/rest/api/content/{page_id}"
        resp = self.session.put(url, json=payload)
        resp.raise_for_status()
        return resp.json()
