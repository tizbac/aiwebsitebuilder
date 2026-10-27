"""Public DuckDuckGo web search (HTML endpoint with API fallback)."""

from __future__ import annotations

import html
import re
from html.parser import HTMLParser
from urllib.parse import parse_qs, unquote, urlparse

import requests

from .files import fail, ok


USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"
)
HEADERS = {
    "User-Agent": USER_AGENT,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.8",
}


class _DDGParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.links: list[tuple[str, str]] = []
        self.snippets: list[str] = []
        self._capture_link = False
        self._capture_snippet = False
        self._href = ""
        self._text: list[str] = []

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        classes = (attrs.get("class") or "").split()
        if tag == "a" and ("result__a" in classes or "result-link" in classes):
            self._capture_link = True
            self._href = attrs.get("href") or ""
            self._text = []
        elif (tag == "a" and "result__snippet" in classes) or (
            tag == "td" and "result-snippet" in classes
        ):
            self._capture_snippet = True
            self._text = []

    def handle_endtag(self, tag):
        if self._capture_link and tag == "a":
            self._capture_link = False
            text = re.sub(r"\s+", " ", "".join(self._text)).strip()
            if self._href and text:
                self.links.append((text, self._href))
            self._text = []
        elif self._capture_snippet and tag in ("a", "td"):
            self._capture_snippet = False
            text = re.sub(r"\s+", " ", "".join(self._text)).strip()
            if text:
                self.snippets.append(text)
            self._text = []

    def handle_data(self, data):
        if self._capture_link or self._capture_snippet:
            self._text.append(data)


def _clean_url(href: str) -> str:
    href = html.unescape(href or "").strip()
    if not href:
        return ""
    if href.startswith("//"):
        href = "https:" + href
    try:
        parsed = urlparse(href)
    except ValueError:
        return href
    if parsed.netloc.endswith("duckduckgo.com") and parsed.path.startswith("/l/"):
        query = parse_qs(parsed.query)
        target = query.get("uddg")
        if target:
            return unquote(target[0])
    return href


def _is_ad(href: str) -> bool:
    return (
        "duckduckgo.com/y.js" in href
        or "ad_provider=" in href
        or "ad_domain=" in href
    )


def _results_from_html(text: str, max_results: int) -> list[dict]:
    parser = _DDGParser()
    try:
        parser.feed(text)
    except Exception:
        pass
    results: list[dict] = []
    seen: set[str] = set()
    for index, (title, href) in enumerate(parser.links):
        url = _clean_url(href)
        if not url or not url.startswith("http") or _is_ad(href) or url in seen:
            continue
        seen.add(url)
        snippet = parser.snippets[index] if index < len(parser.snippets) else ""
        results.append({"title": title, "url": url, "snippet": snippet})
        if len(results) >= max_results:
            break
    return results


def _instant_answers(query: str, max_results: int) -> list[dict]:
    resp = requests.get(
        "https://api.duckduckgo.com/",
        params={"q": query, "format": "json", "no_html": 1, "skip_disambig": 1},
        headers=HEADERS,
        timeout=15,
    )
    resp.raise_for_status()
    data = resp.json()
    results: list[dict] = []

    def add(title: str, url: str, snippet: str) -> None:
        if not url or not url.startswith("http"):
            return
        if any(r["url"] == url for r in results):
            return
        results.append({"title": title or url, "url": url, "snippet": snippet})

    if data.get("AbstractURL"):
        add(
            data.get("Heading") or query,
            data["AbstractURL"],
            data.get("AbstractText") or "",
        )
    for topic in data.get("Results") or []:
        if isinstance(topic, dict):
            add(topic.get("Text") or "", topic.get("FirstURL") or "", topic.get("Text") or "")
    for group in data.get("RelatedTopics") or []:
        if isinstance(group, dict) and "Topics" in group:
            for topic in group.get("Topics") or []:
                if isinstance(topic, dict):
                    add(
                        topic.get("Text") or "",
                        topic.get("FirstURL") or "",
                        topic.get("Text") or "",
                    )
        elif isinstance(group, dict):
            add(group.get("Text") or "", group.get("FirstURL") or "", group.get("Text") or "")
        if len(results) >= max_results:
            break
    return results[:max_results]


def web_search(project, args: dict) -> dict:
    query = str(args.get("query") or "").strip()
    if not query:
        return fail("query must not be empty.")
    try:
        max_results = max(1, min(15, int(args.get("max_results") or 8)))
    except (TypeError, ValueError):
        max_results = 8

    errors: list[str] = []
    results: list[dict] = []
    for url in ("https://html.duckduckgo.com/html/", "https://lite.duckduckgo.com/lite/"):
        try:
            resp = requests.get(
                url,
                params={"q": query, "kl": "wt-wt"},
                headers=HEADERS,
                timeout=20,
            )
            if resp.status_code in (200, 202):
                results = _results_from_html(resp.text, max_results)
                if results:
                    break
            errors.append(f"{url}: HTTP {resp.status_code}")
        except requests.exceptions.RequestException as exc:
            errors.append(f"{url}: {exc}")
    if not results:
        try:
            results = _instant_answers(query, max_results)
        except requests.exceptions.RequestException as exc:
            errors.append(f"api.duckduckgo.com: {exc}")

    if not results:
        detail = "; ".join(errors[-3:])
        return fail(
            f"No DuckDuckGo results for {query!r}." + (f" ({detail})" if detail else "")
        )

    lines = [f"Web search results for {query!r} (DuckDuckGo):"]
    for index, item in enumerate(results, 1):
        lines.append(f"\n{index}. {item['title']}\n   {item['url']}")
        if item.get("snippet"):
            lines.append(f"   {item['snippet'][:500]}")
    return ok("\n".join(lines), results=len(results), query=query)
