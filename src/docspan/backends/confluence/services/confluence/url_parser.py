"""
Utilities for parsing Confluence URLs.

This module provides functions to extract page IDs and space keys from Confluence URLs.
"""

import re
from typing import Optional, Tuple


class ConfluenceUrlParser:
    """Parser for Confluence URLs."""

    # Pattern for Confluence page URLs:
    # https://{domain}/wiki/spaces/{space_key}/pages/{page_id}/{title}
    PAGE_URL_PATTERN = re.compile(
        r'/wiki/spaces/(?P<space_key>[^/]+)/pages/(?P<page_id>\d+)(?:/|$)'
    )

    @classmethod
    def parse_page_url(cls, url: str) -> Tuple[Optional[str], Optional[str]]:
        """
        Parse a Confluence page URL to extract page ID and space key.

        Args:
            url: Confluence page URL

        Returns:
            Tuple of (page_id, space_key), or (None, None) if parsing fails

        Examples:
            >>> parser = ConfluenceUrlParser()
            >>> parser.parse_page_url('https://example.atlassian.net/wiki/spaces/MYSPACE/pages/123456/Page+Title')
            ('123456', 'MYSPACE')
            >>> parser.parse_page_url('https://example.atlassian.net/wiki/spaces/~630044b443e43992b9a3e6f2/pages/973308410/Reliability+Manifesto')
            ('973308410', '~630044b443e43992b9a3e6f2')
        """
        match = cls.PAGE_URL_PATTERN.search(url)
        if match:
            return match.group('page_id'), match.group('space_key')
        return None, None

    @classmethod
    def extract_page_id(cls, url_or_id: str) -> Optional[str]:
        """
        Extract page ID from a URL or return the ID if already provided.

        Args:
            url_or_id: Either a Confluence URL or a page ID

        Returns:
            Page ID or None if not found

        Examples:
            >>> parser = ConfluenceUrlParser()
            >>> parser.extract_page_id('123456')
            '123456'
            >>> parser.extract_page_id('https://example.atlassian.net/wiki/spaces/MYSPACE/pages/123456/Title')
            '123456'
        """
        # If it's already just a number, return it
        if url_or_id.isdigit():
            return url_or_id

        # Try to parse as URL
        page_id, _ = cls.parse_page_url(url_or_id)
        return page_id

    @classmethod
    def extract_space_key(cls, url: str) -> Optional[str]:
        """
        Extract space key from a Confluence URL.

        Args:
            url: Confluence page URL

        Returns:
            Space key or None if not found

        Examples:
            >>> parser = ConfluenceUrlParser()
            >>> parser.extract_space_key('https://example.atlassian.net/wiki/spaces/MYSPACE/pages/123456/Title')
            'MYSPACE'
        """
        _, space_key = cls.parse_page_url(url)
        return space_key

    @classmethod
    def is_confluence_url(cls, text: str) -> bool:
        """
        Check if the text looks like a Confluence URL.

        Args:
            text: Text to check

        Returns:
            True if it looks like a Confluence URL

        Examples:
            >>> parser = ConfluenceUrlParser()
            >>> parser.is_confluence_url('https://example.atlassian.net/wiki/spaces/MYSPACE/pages/123456')
            True
            >>> parser.is_confluence_url('123456')
            False
        """
        return cls.PAGE_URL_PATTERN.search(text) is not None
