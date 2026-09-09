"""Parsing helpers for Legado RSS sources and ordinary RSS/Atom feeds.

This is a thin facade over the shared ``app.legado_rule.rss_source`` parser so
the RSS plugin keeps its identical public surface while the ``media`` plugin
reuses the same parsing without depending on this plugin. ``safe_html`` stays
here because it is the RSS boundary's content sanitization policy.
"""
from __future__ import annotations

import re
from html import unescape
from typing import Any
from urllib.parse import urljoin

import xml.etree.ElementTree as ET

from bs4 import BeautifulSoup, Comment

from ...legado_rule.analyze_rule import AnalyzeRule
from ...legado_rule.analyze_url import AnalyzeUrl
from ...legado_rule.exceptions import FetchError
from ...legado_rule.rss_source import (  # noqa: F401  (re-exported public API)
    _next_page,
    _parse_by_rule,
    fetch_articles,
    parse_default_feed,
    parse_sorts,
    runtime_source,
)
from ...legado_rule.web_book import fetch_str

__all__ = [
    "runtime_source",
    "parse_sorts",
    "fetch_articles",
    "parse_default_feed",
    "safe_html",
    "fetch_article_content",
]


_ALLOWED_TAGS = {
    "a", "article", "aside", "blockquote", "br", "code", "div", "em", "figcaption",
    "figure", "h1", "h2", "h3", "h4", "h5", "h6", "hr", "i", "img", "li", "main",
    "ol", "p", "pre", "section", "span", "strong", "sub", "sup", "table", "tbody",
    "td", "th", "thead", "tr", "u", "ul",
}


def safe_html(value: str, base_url: str = "") -> str:
    """Small allow-list sanitizer for content rendered with Vue ``v-html``."""
    soup = BeautifulSoup(value or "", "html.parser")
    for bad in soup(["script", "style", "iframe", "object", "embed", "form", "input", "button"]):
        bad.decompose()
    for comment in soup.find_all(string=lambda s: isinstance(s, Comment)):
        comment.extract()
    for tag in list(soup.find_all(True)):
        if tag.name not in _ALLOWED_TAGS:
            tag.unwrap()
            continue
        attrs: dict[str, str] = {}
        if tag.name == "a" and tag.get("href"):
            href = urljoin(base_url, str(tag["href"]))
            if href.startswith(("http://", "https://")):
                attrs = {"href": href, "target": "_blank", "rel": "noopener noreferrer"}
        elif tag.name == "img" and tag.get("src"):
            src = urljoin(base_url, str(tag["src"]))
            if src.startswith(("http://", "https://", "data:image/")):
                attrs = {"src": src, "alt": str(tag.get("alt") or ""), "loading": "lazy"}
        tag.attrs = attrs
    return str(soup)


async def fetch_article_content(source: dict, article: dict) -> str:
    existing = str(article.get("content") or "").strip()
    if existing:
        return safe_html(existing, str(article.get("link") or ""))
    rule = str(source.get("ruleContent") or "").strip()
    link = str(article.get("link") or "")
    if not link:
        return safe_html(str(article.get("description") or ""))
    response = await fetch_str(AnalyzeUrl(
        link,
        base_url=str(source.get("sourceUrl") or ""),
        source=runtime_source(source),
        rule_data=article,
    ))
    if response.error or not response.ok:
        fallback = str(article.get("description") or "")
        if fallback:
            return safe_html(fallback, link)
        raise FetchError(response.error or f"HTTP {response.status}", response.status)
    if rule:
        ar = AnalyzeRule(rule_data=article, source=runtime_source(source), base_url=response.url)
        ar.set_content(response.body, base_url=response.url)
        return safe_html(ar.get_string(rule, unescape=False), response.url)
    soup = BeautifulSoup(response.body, "html.parser")
    main = soup.find("article") or soup.find("main") or soup.body or soup
    return safe_html(str(main), response.url)