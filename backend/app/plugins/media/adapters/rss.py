"""RSS-dialect media adapter — drives fqdj0512.json and other RSS/form feeds.

For sources that only carry legacy ``ruleContent`` HTML (like 番茄短剧) it
returns an empty structured unit list and exposes the raw document through
``render_legacy_document`` for the isolated sandbox. Sources may add structured
``mediaRules`` (plan §6.3) and get fully native comic/audio/video playback.
"""
from __future__ import annotations

import json
import re
from typing import Any

from ....legado_rule.analyze_rule import AnalyzeRule
from ....legado_rule.analyze_url import AnalyzeUrl
from ....legado_rule.rss_source import (
    fetch_articles,
    fetch_content_raw,
    parse_sorts,
    runtime_source,
)
from ....legado_rule.web_book import fetch_str
from .. import schemas
from .base import LegacyDocument, MediaCatalogPage, MediaSourceAdapter, ResolvedMedia


_VERIFICATION_INPUT_RE = re.compile(
    r"\{\{\s*java\.getVerificationCode\([^{}]*\)\s*\}\}", re.IGNORECASE
)


def _as_dict(value: Any) -> dict:
    if value is None:
        return {}
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            obj = json.loads(value)
            return obj if isinstance(obj, dict) else {}
        except Exception:  # noqa: BLE001
            return {}
    return {}


def search_template(sort: dict) -> str | None:
    """兼容搜索模板：sort 名为「搜索」且 URL 含查询模板时把它当搜索地址。"""
    if str(sort.get("name") or "").strip() != "搜索":
        return None
    url = str(sort.get("url") or "")
    if any(tok in url for tok in ("{{searchKey}}", "{{key}}")) \
            or _VERIFICATION_INPUT_RE.search(url):
        return url
    return None


def _resolve_search_input(url: str) -> str:
    """Map Legado's interactive input prompt to the media-page search box.

    Some RSS sources use ``getVerificationCode(source.sourceIcon)`` as a
    makeshift text prompt.  The server has no Android dialog, but the media
    discover page already collected the keyword, exposed to AnalyzeUrl as
    ``key``.  Rewriting only that template expression preserves normal captcha
    calls in all other rule contexts.
    """
    return _VERIFICATION_INPUT_RE.sub("{{key}}", url)


def _catalog_item(source: dict, art: dict, idx: int, media_kind: str) -> schemas.MediaCatalogItem:
    link = str(art.get("link") or "").strip()
    item_key = link
    if not item_key:
        item_key = f"{source.get('sourceUrl')}::{str(art.get('title') or '')}"[:1024]
    return schemas.MediaCatalogItem(
        item_key=item_key,
        item_url=link,
        title=str(art.get("title") or ""),
        creator="",
        cover_url=str(art.get("image") or ""),
        intro=str(art.get("pubDate") or art.get("description") or ""),
        latest_unit="",
        total_units=0,
        tags=[],
        source_id=0,
        source_name=str(source.get("sourceName") or ""),
        media_kind=media_kind,
    )


class RssMediaAdapter(MediaSourceAdapter):
    kind = "rss"

    def list_sorts(self) -> list[schemas.MediaSort]:
        return [
            schemas.MediaSort(s["name"], s["url"])
            for s in parse_sorts(self.source)
            if search_template(s) is None
        ]

    def get_detail(self, item: dict) -> schemas.MediaCatalogItem:
        return schemas.MediaCatalogItem(
            item_key=str(item.get("item_key") or item.get("itemKey") or ""),
            item_url=str(item.get("item_url") or item.get("itemUrl") or ""),
            title=str(item.get("title") or ""),
            creator=str(item.get("creator") or ""),
            cover_url=str(item.get("cover_url") or item.get("coverUrl") or ""),
            intro=str(item.get("intro") or ""),
            latest_unit=str(item.get("latest_unit") or ""),
            total_units=int(item.get("total_units") or 0),
            tags=item.get("tags") or item.get("tags_json") or [],
            source_id=int(item.get("source_id") or 0),
            source_name=str(self.source.get("sourceName") or ""),
            media_kind=self.media_kind,
        )

    async def list_items(self, sort: dict, page: int) -> MediaCatalogPage:
        try:
            arts, next_url = await fetch_articles(
                self.source, s_name(sort), s_url(sort), page, None
            )
        except Exception as exc:  # noqa: BLE001 - per-source failure must not blank the page
            return MediaCatalogPage([], None, str(exc))
        items = [_catalog_item(self.source, a, i, self.media_kind) for i, a in enumerate(arts)]
        return MediaCatalogPage(items, next_url)

    async def search_items(self, keyword: str, page: int) -> MediaCatalogPage:
        template = None
        # Search entries are intentionally hidden from the browse chips, so
        # inspect the raw source sorts here instead of ``list_sorts()``.
        for sort in parse_sorts(self.source):
            template = search_template(sort)
            if template:
                break
        url = (str(self.source.get("searchUrl") or "").strip()) or template
        if not url:
            return MediaCatalogPage([], None, "该媒体源未配置搜索功能")
        url = _resolve_search_input(url)
        try:
            arts, next_url = await fetch_articles(
                self.source, "搜索", url, page, keyword or None
            )
        except Exception as exc:  # noqa: BLE001
            return MediaCatalogPage([], None, str(exc))
        items = [_catalog_item(self.source, a, i, self.media_kind) for i, a in enumerate(arts)]
        return MediaCatalogPage(items, next_url)

    async def get_units(self, item: dict, refresh: bool = True) -> list[schemas.MediaUnitDto]:
        rules = _as_dict(self.source.get("mediaRules"))
        unit_rules = _as_dict(rules.get("units"))
        if not (unit_rules.get("list") and unit_rules.get("url")):
            return []
        return await self._structured_units(unit_rules, item)

    async def _structured_units(self, unit_rules: dict, item: dict) -> list[schemas.MediaUnitDto]:
        request_url = str(unit_rules.get("requestUrl") or "").strip()
        item_url = str(item.get("item_url") or item.get("itemUrl") or "")
        target = request_url or item_url
        if not target:
            return []
        body = await self._fetch(target)
        ar = AnalyzeRule(rule_data=item, source=runtime_source(self.source), base_url=target)
        ar.set_content(body, base_url=target)
        units: list[schemas.MediaUnitDto] = []
        key_rule = unit_rules.get("key") or ""
        title_rule = unit_rules.get("title") or ""
        url_rule = unit_rules.get("url") or ""
        for idx, el in enumerate(ar.get_elements(unit_rules.get("list") or "")):
            ar.set_content(el)
            ukey = ar.get_string(key_rule) if key_rule else str(idx)
            title = ar.get_string(title_rule) if title_rule else str(idx)
            locator = ar.get_string(url_rule, is_url=True) if url_rule else ukey
            units.append(schemas.MediaUnitDto(ukey, idx, title, locator))
        return units

    async def resolve_unit(self, item: dict, unit: dict) -> ResolvedMedia:
        rules = _as_dict(self.source.get("mediaRules"))
        content_rules = _as_dict(rules.get("content"))
        if content_rules and (content_rules.get("streamUrl") or content_rules.get("imageUrl")):
            return await self._resolve_structured_content(content_rules, item, unit)
        raise MediaNotResolvable(self.media_kind)

    async def _resolve_structured_content(
        self, content_rules: dict, item: dict, unit: dict
    ) -> ResolvedMedia:
        request_url = str(content_rules.get("requestUrl") or "").strip()
        url = str(unit.get("url") or unit.get("locator") or "") or request_url
        if not url:
            raise MediaNotResolvable(self.media_kind)
        body = await self._fetch(url)
        ar = AnalyzeRule(
            rule_data={**item, **unit},
            source=runtime_source(self.source),
            base_url=url,
        )
        ar.set_content(body, base_url=url)
        kind = schemas.valid_kind(str(self.source.get("mediaKind") or "")) or "video"
        if kind == "comic":
            images: list[dict] = []
            list_rule = content_rules.get("imageList") or ""
            url_rule = content_rules.get("imageUrl") or ""
            if list_rule:
                for el in ar.get_elements(list_rule):
                    ar.set_content(el)
                    src = ar.get_string(url_rule, is_url=True) if url_rule else ""
                    if src:
                        images.append(
                            {"url": src, "headers": _parse_headers(content_rules.get("headers"))})
            return ResolvedMedia(kind="comic", images=images,
                                 reading_direction=content_rules.get("readingDirection") or "ltr")
        streams: list[dict] = []
        list_rule = content_rules.get("streamList") or ""
        stream_url_rule = content_rules.get("streamUrl") or ""
        if list_rule:
            for el in ar.get_elements(list_rule):
                ar.set_content(el)
                su = ar.get_string(stream_url_rule, is_url=True) if stream_url_rule else ""
                if su:
                    streams.append(self._stream(su, content_rules, ar))
        elif stream_url_rule:
            su = ar.get_string(stream_url_rule, is_url=True)
            if su:
                streams.append(self._stream(su, content_rules, ar))
        subtitles: list[dict] = []
        if content_rules.get("subtitleUrl"):
            su = ar.get_string(content_rules["subtitleUrl"], is_url=True)
            if su:
                subtitles.append({"url": su, "lang": "unknown"})
        return ResolvedMedia(
            kind=kind, streams=streams, subtitles=subtitles,
            poster_url=ar.get_string(content_rules.get("poster") or "", is_url=True),
            lyrics=ar.get_string(content_rules.get("lyrics") or ""),
            warning="" if streams else "未解析到可播放地址",
        )

    @staticmethod
    def _stream(su: str, content_rules: dict, ar: AnalyzeRule) -> dict:
        return {
            "url": su,
            "quality": ar.get_string(content_rules.get("quality") or "") or "auto",
            "mime": ar.get_string(content_rules.get("mime") or "") or "",
            "headers": _parse_headers(content_rules.get("headers")),
        }

    async def _fetch(self, url: str) -> str:
        resp = await fetch_str(AnalyzeUrl(
            url, base_url=str(self.source.get("sourceUrl") or ""),
            source=runtime_source(self.source),
        ))
        if resp.error or not resp.ok:
            raise MediaNotResolvable("抓取失败")
        return resp.body or ""

    async def render_legacy_document(self, item: dict) -> LegacyDocument | None:
        if not str(self.source.get("ruleContent") or "").strip():
            return None
        art = {
            "origin": str(self.source.get("sourceUrl") or ""),
            "sort": str(item.get("sort") or ""),
            "title": str(item.get("title") or item.get("item_title") or ""),
            "link": str(item.get("item_url") or item.get("itemUrl") or ""),
            "pubDate": str(item.get("intro") or item.get("pubDate") or ""),
            "description": str(item.get("intro") or ""),
            "content": "",
            "image": str(item.get("cover_url") or item.get("coverUrl") or ""),
        }
        try:
            html = await fetch_content_raw(self.source, art)
        except Exception as exc:  # noqa: BLE001
            return LegacyDocument(html="", iframe_key="", warning=str(exc))
        if not html:
            return None
        return LegacyDocument(
            html=html,
            iframe_key=f"{str(item.get('source_id') or 0)}::{str(item.get('id') or '')}",
            warning="兼容播放器：该源使用旧式 HTML 播放器，已隔离在沙箱运行",
        )


class MediaNotResolvable(Exception):
    """源条目没有结构化可播放内容规则，需要走 legacy 兼容层。"""

    def __init__(self, kind: str = "video"):
        super().__init__(kind)
        self.kind = kind


def s_name(sort: dict) -> str:
    return str(sort.get("name") or "")


def s_url(sort: dict) -> str:
    return str(sort.get("url") or str(sort.get("sortUrl") or ""))


def fingerprint_legacy_document(source: dict, item: dict, html: str) -> str:
    """旧式源更新检测：对「规则求值业务结果」计算稳定指纹。

    优先提取 item_ids 数组求 hash；否则去掉明显易变片段后 hash。
    """
    import hashlib

    ids = _extract_item_ids(html)
    if ids:
        base = json.dumps(ids, ensure_ascii=False, sort_keys=True)
    else:
        base = re.sub(r"timestamp\s*[:=]\s*[\d\"']+", "", html, flags=re.I)
        base = re.sub(r"\b\d{10,13}\b", "", base)
    return hashlib.sha256(base.encode("utf-8")).hexdigest()


def _extract_item_ids(html: str) -> list[str]:
    """从 ruleContent 首段 JS 的 reduce 结果里提取 item_id 数组（番茄短剧）。"""
    m = re.search(r"```js\s*([\s\S]*?)\s*```", html)
    if m:
        html = m.group(1)
    item_ids = re.findall(r"item_id[\"']?\s*[:=]\s*[\"']?(\d+)", html)
    return item_ids


def _parse_headers(value: Any) -> dict:
    if isinstance(value, dict):
        return {str(k): str(v) for k, v in value.items()}
    out: dict = {}
    for line in str(value or "").split("\n"):
        if ":" in line:
            k, v = line.split(":", 1)
            out[k.strip()] = v.strip()
    return out
