"""
Document Converter Module

Handles conversion between Google Docs and Markdown formats
"""

import logging
import re
import sys
from markdownify import markdownify as md

logger = logging.getLogger(__name__)

# Increase recursion limit for deeply nested lists
sys.setrecursionlimit(10000)

# Configuration for large documents
MAX_CONTENT_LENGTH = 10 * 1024 * 1024  # 10MB


class DocumentConverter:
    """Converter for Google Docs <-> Markdown"""

    @staticmethod
    def html_to_markdown(html_content: str) -> str:
        """
        Convert Google Docs HTML to Markdown

        Args:
            html_content: HTML content from Google Docs

        Returns:
            str: Markdown formatted content
        """
        try:
            content_size = len(html_content)
            logger.info(f"Converting HTML to Markdown (size: {content_size:,} bytes)")

            # Check if content is too large
            if content_size > MAX_CONTENT_LENGTH:
                logger.warning(f"Large document detected: {content_size:,} bytes")

            # Pre-process: Remove style and script tags with content
            # Use more efficient non-greedy matching for large documents
            html_content = re.sub(r'<style[^>]*>.*?</style>', '', html_content, flags=re.DOTALL | re.IGNORECASE)
            html_content = re.sub(r'<script[^>]*>.*?</script>', '', html_content, flags=re.DOTALL | re.IGNORECASE)

            # Remove head section entirely
            html_content = re.sub(r'<head[^>]*>.*?</head>', '', html_content, flags=re.DOTALL | re.IGNORECASE)

            logger.info("Reconstructing nested lists...")
            # Reconstruct nested lists based on margin-left
            html_content = DocumentConverter._reconstruct_nested_lists(html_content)

            # Convert HTML to Markdown
            markdown = md(
                html_content,
                heading_style="ATX",  # Use # for headings
                bullets="-",  # Use - for unordered lists
                strong_em_symbol="**",  # Use ** for bold
                strip=['style', 'script']  # Remove style and script tags
            )

            # Keep tabs for nested lists (Obsidian uses tabs for list indentation)
            # Do NOT convert tabs to spaces

            # Clean up extra whitespace
            markdown = re.sub(r'\n{3,}', '\n\n', markdown)  # Max 2 consecutive newlines
            markdown = markdown.strip()

            # Remove trailing spaces from each line (important for proper list rendering)
            markdown = re.sub(r' +$', '', markdown, flags=re.MULTILINE)

            # Remove Google Docs specific artifacts
            markdown = DocumentConverter._clean_google_docs_artifacts(markdown)

            logger.info("Converted HTML to Markdown")
            return markdown

        except Exception as e:
            logger.error(f"Error converting HTML to Markdown: {e}")
            raise

    @staticmethod
    def _reconstruct_nested_lists(html: str) -> str:
        """
        Reconstruct nested list structure based on class names and margin-left

        Google Docs exports lists as separate <ul> blocks with class names
        indicating list groups and nesting levels. This function groups
        consecutive related <ul> blocks and rebuilds proper nested structure.

        Args:
            html: HTML content with Google Docs list structure

        Returns:
            str: HTML with reconstructed nested lists
        """
        try:
            from html.parser import HTMLParser
        except Exception as e:
            logger.warning(f"Error importing HTMLParser, skipping list reconstruction: {e}")
            return html

        class ListItem:
            def __init__(self, level, content, class_level=None):
                self.level = level
                self.content = content
                self.class_level = class_level  # Level from class name (-0, -1, -2)
                self.children = []

        class GoogleListParser(HTMLParser):
            def __init__(self):
                super().__init__()
                self.items = []
                self.current_item = None
                self.in_li = False
                self.li_content = []

            def handle_starttag(self, tag, attrs):
                if tag == 'li':
                    self.in_li = True
                    self.li_content = []
                    # Extract margin-left from style attribute
                    style = dict(attrs).get('style', '')
                    margin_match = re.search(r'margin-left:\s*(\d+)pt', style)
                    if margin_match:
                        margin = int(margin_match.group(1))
                        # Calculate level: each 36pt is one level
                        level = max(0, (margin // 36) - 1)
                    else:
                        level = 0
                    self.current_item = ListItem(level, '')
                elif self.in_li:
                    # Reconstruct tag for content
                    attrs_str = ' '.join([f'{k}="{v}"' for k, v in attrs])
                    self.li_content.append(f'<{tag} {attrs_str}>' if attrs_str else f'<{tag}>')

            def handle_endtag(self, tag):
                if tag == 'li' and self.in_li:
                    self.current_item.content = ''.join(self.li_content)
                    self.items.append(self.current_item)
                    self.in_li = False
                    self.current_item = None
                elif self.in_li:
                    self.li_content.append(f'</{tag}>')

            def handle_data(self, data):
                if self.in_li:
                    self.li_content.append(data)

        # Extract class name and level from <ul> tag
        def extract_list_info(ul_tag):
            """Extract list prefix and level from <ul> class attribute"""
            class_match = re.search(r'class="([^"]*)"', ul_tag)
            if not class_match:
                return None, None

            class_name = class_match.group(1)
            # Pattern: lst-kix_XXXXX-N where N is the level
            level_match = re.search(r'(lst-kix_[a-z0-9]+)-(\d+)', class_name)
            if level_match:
                prefix = level_match.group(1)  # e.g., "lst-kix_dnvzcw1w4rp0"
                level = int(level_match.group(2))  # e.g., 0, 1, 2
                return prefix, level
            return None, None

        # Group consecutive <ul> blocks that belong together
        def group_consecutive_lists(html_content):
            """Find groups of consecutive <ul> blocks with same prefix"""
            groups = []
            current_group = []
            current_prefix = None
            last_end = 0

            # Find all <ul>...</ul> blocks
            for match in re.finditer(r'<ul[^>]*>.*?</ul>', html_content, re.DOTALL):
                ul_block = match.group(0)
                prefix, level = extract_list_info(ul_block)

                # Get content before this <ul> (non-list content)
                before_content = html_content[last_end:match.start()]

                # Check if this continues the current group
                if prefix and prefix == current_prefix:
                    # Same group, add to current
                    current_group.append((ul_block, level, match.start(), match.end()))
                else:
                    # Different group or first group
                    if current_group:
                        # Save previous group
                        groups.append({
                            'prefix': current_prefix,
                            'blocks': current_group,
                            'before': groups[-1]['after'] if groups else html_content[:current_group[0][2]],
                            'start': current_group[0][2],
                            'end': current_group[-1][3]
                        })

                    # Start new group
                    if prefix:
                        current_prefix = prefix
                        current_group = [(ul_block, level, match.start(), match.end())]
                    else:
                        # No prefix, treat as standalone
                        if before_content.strip():
                            groups.append({
                                'prefix': None,
                                'blocks': [(ul_block, None, match.start(), match.end())],
                                'before': before_content,
                                'start': match.start(),
                                'end': match.end()
                            })
                        else:
                            current_group = [(ul_block, None, match.start(), match.end())]
                            current_prefix = None

                last_end = match.end()

            # Don't forget the last group
            if current_group:
                groups.append({
                    'prefix': current_prefix,
                    'blocks': current_group,
                    'before': groups[-1]['after'] if groups else html_content[:current_group[0][2]],
                    'start': current_group[0][2],
                    'end': current_group[-1][3]
                })
                groups[-1]['after'] = html_content[last_end:]

            return groups

        # Build nested HTML from grouped items
        def build_nested_html(items):
            if not items:
                return ''

            html_parts = []
            i = 0
            while i < len(items):
                item = items[i]
                html_parts.append(f'<li>{item.content}')

                # Check if next items are children (higher level)
                j = i + 1
                while j < len(items) and items[j].level > item.level:
                    j += 1

                if j > i + 1:
                    # Has children
                    children = items[i+1:j]
                    html_parts.append('<ul>')
                    html_parts.append(build_nested_html(children))
                    html_parts.append('</ul>')
                    i = j
                else:
                    i += 1

                html_parts.append('</li>')

            return ''.join(html_parts)

        # Process groups and merge consecutive blocks
        def process_list_group(group):
            """Process a group of related <ul> blocks"""
            if not group or not group['blocks']:
                return group['blocks'][0][0] if group['blocks'] else ''

            # Parse all blocks in this group
            all_items = []
            for ul_block, class_level, _, _ in group['blocks']:
                parser = GoogleListParser()
                try:
                    parser.feed(ul_block)
                    # Update class_level if available
                    if class_level is not None:
                        for item in parser.items:
                            item.class_level = class_level
                            # Use class level if it's more reliable
                            if class_level > 0:
                                item.level = class_level
                    all_items.extend(parser.items)
                except:
                    pass

            if not all_items:
                return ''.join([block[0] for block in group['blocks']])

            # Rebuild with proper nesting
            return f'<ul>{build_nested_html(all_items)}</ul>'

        # Main processing: group consecutive lists and rebuild with proper nesting
        # This approach merges <ul> blocks that belong together while preserving non-list content

        try:
            # Find all <ul> positions and group them
            ul_positions = []
            for match in re.finditer(r'<ul[^>]*>.*?</ul>', html, re.DOTALL):
                ul_block = match.group(0)
                prefix, level = extract_list_info(ul_block)
                ul_positions.append({
                    'start': match.start(),
                    'end': match.end(),
                    'block': ul_block,
                    'prefix': prefix,
                    'level': level
                })

            if not ul_positions:
                return html
        except Exception as e:
            logger.warning(f"Error finding list positions, returning original HTML: {e}")
            return html

        try:
            # Group consecutive <ul> blocks with same prefix
            result_parts = []
            last_pos = 0
            i = 0

            while i < len(ul_positions):
                # Add non-list content before this block
                result_parts.append(html[last_pos:ul_positions[i]['start']])

                # Check if next blocks belong to the same group (same prefix)
                current_prefix = ul_positions[i]['prefix']
                group_blocks = [ul_positions[i]]
                j = i + 1

                # Look ahead to find consecutive blocks with same prefix
                while j < len(ul_positions):
                    # Check if there's only whitespace between blocks
                    between_content = html[ul_positions[j-1]['end']:ul_positions[j]['start']]
                    is_consecutive = between_content.strip() == ''

                    if is_consecutive and ul_positions[j]['prefix'] == current_prefix:
                        group_blocks.append(ul_positions[j])
                        j += 1
                    else:
                        break

                # Process this group
                if len(group_blocks) > 1 and current_prefix:
                    # Multiple blocks with same prefix - merge them
                    all_items = []
                    for block_info in group_blocks:
                        parser = GoogleListParser()
                        try:
                            parser.feed(block_info['block'])
                            # Set level from class if available
                            if block_info['level'] is not None:
                                for item in parser.items:
                                    item.class_level = block_info['level']
                                    # Use class level as the authoritative level
                                    item.level = block_info['level']
                            all_items.extend(parser.items)
                        except:
                            pass

                    if all_items:
                        result_parts.append(f'<ul>{build_nested_html(all_items)}</ul>')
                    else:
                        # Fallback: keep original blocks
                        for block_info in group_blocks:
                            result_parts.append(block_info['block'])

                    last_pos = group_blocks[-1]['end']
                    i = j
                else:
                    # Single block or no prefix - process normally
                    parser = GoogleListParser()
                    try:
                        parser.feed(group_blocks[0]['block'])
                        if parser.items:
                            result_parts.append(f'<ul>{build_nested_html(parser.items)}</ul>')
                        else:
                            result_parts.append(group_blocks[0]['block'])
                    except:
                        result_parts.append(group_blocks[0]['block'])

                    last_pos = group_blocks[0]['end']
                    i += 1

            # Add remaining content
            result_parts.append(html[last_pos:])

            reconstructed_html = ''.join(result_parts)
            logger.info(f"List reconstruction completed (original: {len(html):,} -> result: {len(reconstructed_html):,} bytes)")
            return reconstructed_html

        except Exception as e:
            logger.error(f"Error during list reconstruction: {e}", exc_info=True)
            logger.warning("Returning original HTML without list reconstruction")
            return html

    @staticmethod
    def _clean_google_docs_artifacts(markdown: str) -> str:
        """
        Clean Google Docs specific artifacts from markdown

        Args:
            markdown: Markdown content

        Returns:
            str: Cleaned markdown
        """
        # Remove empty links
        markdown = re.sub(r'\[\]\(.*?\)', '', markdown)

        # Clean up excessive spacing in lists
        markdown = re.sub(r'(\n-\s+)\n+', r'\1', markdown)

        # Remove zero-width spaces and other invisible characters
        markdown = markdown.replace('\u200b', '')  # Zero-width space
        markdown = markdown.replace('\ufeff', '')  # Zero-width no-break space

        return markdown

    @staticmethod
    def markdown_to_plain_text(markdown_content: str) -> str:
        """
        Convert Markdown to plain text (for updating Google Docs)

        This is a simple conversion that removes markdown formatting
        but preserves the text structure.

        Args:
            markdown_content: Markdown formatted content

        Returns:
            str: Plain text content
        """
        try:
            text = markdown_content

            # Remove code blocks
            text = re.sub(r'```[\s\S]*?```', '', text)
            text = re.sub(r'`([^`]+)`', r'\1', text)

            # Convert headers to plain text (keep the text, add newlines)
            text = re.sub(r'^#{1,6}\s+(.+)$', r'\1\n', text, flags=re.MULTILINE)

            # Convert bold and italic
            text = re.sub(r'\*\*(.+?)\*\*', r'\1', text)
            text = re.sub(r'\*(.+?)\*', r'\1', text)
            text = re.sub(r'__(.+?)__', r'\1', text)
            text = re.sub(r'_(.+?)_', r'\1', text)

            # Convert links [text](url) to just text
            text = re.sub(r'\[([^\]]+)\]\([^\)]+\)', r'\1', text)

            # Convert lists while preserving indentation
            # Convert tabs to 4 spaces for Google Docs compatibility
            text = text.replace('\t', '    ')
            # Remove list markers but keep indentation
            text = re.sub(r'^(\s*)[-*+]\s+', r'\1', text, flags=re.MULTILINE)
            text = re.sub(r'^(\s*)\d+\.\s+', r'\1', text, flags=re.MULTILINE)

            # Clean up extra whitespace
            text = re.sub(r'\n{3,}', '\n\n', text)
            text = text.strip()

            logger.info("Converted Markdown to plain text")
            return text

        except Exception as e:
            logger.error(f"Error converting Markdown to plain text: {e}")
            raise


