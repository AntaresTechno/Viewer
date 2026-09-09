"""Shared, pure Legado RssSource parsing used by both the RSS plugin and the
media plugin.

Extracted from ``app.plugins.rss.parser`` so the ``media`` plugin can drive
Legado RSS sources without depending on the ``rss`` plugin being enabled. The
RSS plugin keeps applying ``safe_html()`` at its own boundary; the media plugin
uses the *raw* (un-sanitized) content evaluator and hands results to its
isolated legacy-document handler instead.

Refactoring must not change the RSS API output; ``app.plugins.rss.parser``
re-exports these symbols so existing imports and tests are unaffected.
"""
from __future__ import annotations

import json
import re
from typing import Any
from urllib.parse import urljoin

from .analyze_rule import AnalyzeRule
from .analyze_url import AnalyzeUrl, get_absolute_url
from .exceptions import FetchError, RuleError
from .web_book import fetch_str


def runtime_source(source: dict) -> dict:
    """Give an RssSource the aliases expected by the shared Legado bridges."""
    out = dict(source)
    out.setdefault("bookSourceUrl", str(source.get("sourceUrl") or ""))
    out.setdefault("bookSourceName", str(source.get("sourceName") or ""))
    return out


def parse_sorts(source: dict) -> list[dict[str, str]]:
    raw: Any = source.get("sortUrl")
    if isinstance(raw, dict):
        return [{"name": str(k), "url": str(v)} for k, v in raw.items() if v]
    text = str(raw or "").strip()
    if text.lower().startswith("@js:") or text.lower().startswith("<js>"):
        code = text[4:] if text.lower().startswith("@js:") else text[4:text.lower().rfind("</js>")]
        # AnalyzeRule owns the reusable JS runtime and loads the source's
        # jsLib/bridges, matching RssSource.evalJS in Legado.
        value = AnalyzeRule(source=runtime_source(source)).eval_js(code)
        text = "" if value is None else str(value)
    if text.startswith("{"):
        try:
            obj = json.loads(text)
            if isinstance(obj, dict):
                return [{"name": str(k), "url": str(v)} for k, v in obj.items() if v]
        except Exception:  # noqa: BLE001
            pass
    result: list[dict[str, str]] = []
    for part in re.split(r"(?:&&|\r?\n)+", text):
        part = part.strip()
        if not part:
            continue
        if "::" in part:
            name, url = part.split("::", 1)
        else:
            name, url = "", part
        if url.strip():
            result.append({
                "name": name.strip(),
                "url": get_absolute_url(str(source.get("sourceUrl") or ""), url.strip()),
            })
    if not result:
        result.append({"name": "", "url": str(source.get("sourceUrl") or "")})
    return result


def parse_item_extra(source: dict, item: dict) -> dict:
    """Normalize a raw RSS article dict into the app's canonical article shape.

    Keeps unknown keys so downstream (media) adapters can read additional data
    returned by a source's JS rules.
    """
    return {
        "origin": str(source.get("sourceUrl") or ""),
        "sort": str(item.get("sort") or ""),
        "title": str(item.get("title") or ""),
        "link": str(item.get("link") or ""),
        "pubDate": str(item.get("pubDate") or ""),
        "description": str(item.get("description") or ""),
        "content": str(item.get("content") or ""),
        "image": str(item.get("image") or ""),
    }


async def fetch_articles(
    source: dict, sort_name: str, sort_url: str, page: int = 1,
    search_key: str | None = None,
) -> tuple[list[dict[str, Any]], str | None]:
    src = runtime_source(source)
    variables = {"searchKey": search_key or "", "key": search_key or ""}
    analyze_url = AnalyzeUrl(
        sort_url,
        key=search_key,
        page=max(1, page),
        base_url=str(source.get("sourceUrl") or ""),
        source=src,
        rule_data=variables,
    )
    response = await fetch_str(analyze_url)
    if response.error or not response.ok:
        raise FetchError(response.error or f"HTTP {response.status}", response.status)
    body = response.body or ""
    if not body.strip():
        if str(source.get("ruleArticles") or "").strip():
            raise FetchError(
                "订阅源接口返回空内容（HTTP 200），源接口可能已失效或需要更新"
            )
        return [], None
    if str(source.get("ruleArticles") or "").strip():
        return _parse_by_rule(source, sort_name, sort_url, response.url, body), _next_page(
            source, body, response.url, sort_url
        )
    return parse_default_feed(sort_name, body, str(source.get("sourceUrl") or ""), response.url), None


def _parse_by_rule(
    source: dict, sort_name: str, requested_url: str, final_url: str, body: str
) -> list[dict[str, Any]]:
    src = runtime_source(source)
    rule_articles = str(source.get("ruleArticles") or "")
    reverse = rule_articles.startswith("-")
    if reverse:
        rule_articles = rule_articles[1:]
    ar = AnalyzeRule(rule_data={}, source=src, base_url=final_url)
    ar.set_content(body, base_url=final_url)
    result: list[dict[str, Any]] = []
    for item in ar.get_elements(rule_articles):
        try:
            ar.set_content(item, base_url=final_url)
            title = ar.get_string(str(source.get("ruleTitle") or "")).strip()
            if not title:
                continue
            raw_link = ar.get_string(str(source.get("ruleLink") or "")).strip()
            link = get_absolute_url(final_url or requested_url, raw_link) if raw_link else final_url
            result.append({
                "origin": str(source.get("sourceUrl") or ""),
                "sort": sort_name,
                "title": title,
                "link": link,
                "pubDate": ar.get_string(str(source.get("rulePubDate") or "")).strip(),
                "description": ar.get_string(str(source.get("ruleDescription") or "")).strip(),
                "content": "",
                "image": ar.get_string(str(source.get("ruleImage") or ""), is_url=True).strip(),
            })
        except Exception:  # noqa: BLE001 - one malformed item must not discard the page
            continue
    if reverse:
        result.reverse()
    return result


def _next_page(source: dict, body: str, final_url: str, requested_url: str) -> str | None:
    rule = str(source.get("ruleNextPage") or "").strip()
    if not rule:
        return None
    if rule.upper() == "PAGE":
        return requested_url
    ar = AnalyzeRule(source=runtime_source(source), base_url=final_url)
    ar.set_content(body, base_url=final_url)
    value = ar.get_string(rule).strip()
    return get_absolute_url(final_url, value) if value else None


async def fetch_content_raw(source: dict, item: dict) -> str:
    """Evaluate ``ruleContent`` for an RSS article with NO sanitization.

    This is only safe to hand to the media plugin's isolated legacy-document
    handler. The RSS plugin must never call this — it always applies
    ``safe_html`` after its own content evaluation.
    """
    rule = str(source.get("ruleContent") or "").strip()
    link = str(item.get("link") or "")
    existing = str(item.get("content") or "").strip()
    if existing:
        return existing
    if not rule:
        return ""
    if not link:
        return ""
    response = await fetch_str(AnalyzeUrl(
        link,
        base_url=str(source.get("sourceUrl") or ""),
        source=runtime_source(source),
        rule_data=item,
    ))
    if response.error or not response.ok:
        raise FetchError(response.error or f"HTTP {response.status}", response.status)
    ar = AnalyzeRule(rule_data=item, source=runtime_source(source), base_url=response.url)
    ar.set_content(response.body, base_url=response.url)
    return ar.get_string(rule, unescape=False)


# ---------------------------------------------------------------------------
# XML/RSS feed default parsing (kept here so both plugins can share it).
# ---------------------------------------------------------------------------
import xml.etree.ElementTree as ET  # noqa: E402
from html import unescape  # noqa: E402

from bs4 import BeautifulSoup  # noqa: E402


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1].split(":", 1)[-1].lower()


def _child(node: ET.Element, *names: str) -> ET.Element | None:
    wanted = {n.lower() for n in names}
    return next((c for c in list(node) if _local(c.tag) in wanted), None)


def _text(node: ET.Element | None) -> str:
    if node is None:
        return ""
    return "".join(node.itertext()).strip()


def _inner(node: ET.Element | None) -> str:
    if node is None:
        return ""
    if list(node):
        return "".join(ET.tostring(c, encoding="unicode") for c in list(node)).strip()
    return (node.text or "").strip()


def _first_image(*values: str) -> str:
    for value in values:
        if not value:
            continue
        soup = BeautifulSoup(unescape(value), "html.parser")
        img = soup.find("img")
        if img and img.get("src"):
            return str(img["src"]).strip()
    return ""


def parse_default_feed(
    sort_name: str, xml: str, source_url: str, response_url: str = ""
) -> list[dict[str, Any]]:
    """Parse RSS 2.x, RDF/RSS 1.0 and Atom feeds without extra deps."""
    try:
        root = ET.fromstring(xml.lstrip("\ufeff\n\r\t "))
    except ET.ParseError as exc:
        raise RuleError(f"订阅内容不是有效的 RSS/Atom XML: {exc}") from exc
    item_nodes = [n for n in root.iter() if _local(n.tag) in {"item", "entry"}]
    base = response_url or source_url
    articles: list[dict[str, Any]] = []
    for item in item_nodes:
        title = _text(_child(item, "title"))
        link_node = _child(item, "link")
        link = ""
        if link_node is not None:
            link = (link_node.attrib.get("href") or _text(link_node)).strip()
        if not link:
            link = _text(_child(item, "guid", "id"))
        description_node = _child(item, "description", "summary")
        content_node = _child(item, "encoded", "content")
        description = _inner(description_node)
        content = _inner(content_node)
        pub_date = _text(_child(item, "pubdate", "published", "updated", "date", "time"))
        image = ""
        for c in list(item):
            lname = _local(c.tag)
            if lname == "thumbnail" and c.attrib.get("url"):
                image = str(c.attrib["url"])
                break
            if lname == "enclosure" and str(c.attrib.get("type", "")).startswith("image/"):
                image = str(c.attrib.get("url") or "")
                break
        image = image or _first_image(content, description)
        if not title and not link:
            continue
        articles.append({
            "origin": source_url,
            "sort": sort_name,
            "title": title or link,
            "link": urljoin(base, link),
            "pubDate": pub_date,
            "description": description,
            "content": content,
            "image": urljoin(base, image) if image else "",
        })
    return articles
