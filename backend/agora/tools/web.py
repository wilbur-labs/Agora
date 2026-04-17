"""Web tools — search and fetch web pages."""
from __future__ import annotations

import httpx
from lxml import html as lxml_html

from .base import Tool, ToolResult

_TIMEOUT = 30
_MAX_CONTENT = 8_000


class WebSearch(Tool):
    name = "web_search"
    description = "Search the web using DuckDuckGo. Returns a list of results with title, URL, and snippet."
    parameters = {
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "Search query"},
            "max_results": {"type": "integer", "description": "Max results (default 5)"},
        },
        "required": ["query"],
    }

    async def execute(self, *, query: str, max_results: int = 5, **_) -> ToolResult:
        try:
            async with httpx.AsyncClient(timeout=_TIMEOUT, follow_redirects=True) as client:
                resp = await client.get(
                    "https://html.duckduckgo.com/html/",
                    params={"q": query},
                    headers={"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"},
                )
                resp.raise_for_status()
            tree = lxml_html.fromstring(resp.content)
            results = tree.xpath('//div[contains(@class,"web-result")]')
            lines = []
            for r in results[:max_results]:
                title_el = r.xpath('.//a[contains(@class,"result__a")]')
                snippet_el = r.xpath('.//*[contains(@class,"result__snippet")]')
                title = title_el[0].text_content().strip() if title_el else ""
                href_raw = title_el[0].get("href", "") if title_el else ""
                # Extract actual URL from DDG redirect
                import urllib.parse
                parsed = urllib.parse.parse_qs(urllib.parse.urlparse(href_raw).query)
                href = parsed.get("uddg", [href_raw])[0]
                snippet = snippet_el[0].text_content().strip() if snippet_el else ""
                if title:
                    lines.append(f"**{title}**")
                    lines.append(f"  URL: {href}")
                    if snippet:
                        lines.append(f"  {snippet}")
                    lines.append("")
            return ToolResult(True, "\n".join(lines) if lines else "No results found.")
        except Exception as e:
            return ToolResult(False, "", str(e))


class WebFetch(Tool):
    name = "web_fetch"
    description = "Fetch a web page and extract its text content. Use this to read articles, documentation, etc."
    parameters = {
        "type": "object",
        "properties": {
            "url": {"type": "string", "description": "URL to fetch"},
        },
        "required": ["url"],
    }

    async def execute(self, *, url: str, **_) -> ToolResult:
        try:
            async with httpx.AsyncClient(timeout=_TIMEOUT, follow_redirects=True) as client:
                resp = await client.get(url, headers={"User-Agent": "Mozilla/5.0 (compatible; AgoraBot/1.0)"})
                resp.raise_for_status()
            ct = resp.headers.get("content-type", "")
            if "html" not in ct and "text" not in ct:
                return ToolResult(False, "", f"Not a text page: {ct}")
            tree = lxml_html.fromstring(resp.content)
            # Remove script/style
            for el in tree.xpath("//script|//style|//nav|//footer|//header"):
                el.getparent().remove(el)
            text = tree.text_content()
            # Clean up whitespace
            lines = [l.strip() for l in text.splitlines() if l.strip()]
            text = "\n".join(lines)
            if len(text) > _MAX_CONTENT:
                text = text[:_MAX_CONTENT] + f"\n... [truncated, {len(text)} chars total]"
            return ToolResult(True, text)
        except Exception as e:
            return ToolResult(False, "", str(e))
