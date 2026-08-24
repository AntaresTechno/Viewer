"""XPath analyzer backed by lxml (JsoupXpath dialect subset)."""
from __future__ import annotations

from typing import Any

from lxml import etree

from .rule_analyzer import RuleAnalyzer


def parse_html(text: str) -> etree._Element:
    parser = etree.HTMLParser(recover=True, huge_tree=True)
    return etree.HTML(text, parser)


class AnalyzeByXPath:
    def __init__(self, doc: Any):
        if isinstance(doc, etree._Element):  # noqa: SLF001
            self.tree = doc
        else:
            text = doc if isinstance(doc, str) else str(doc)
            stripped = text.lstrip().lower()
            try:
                if stripped.startswith("<?xml"):
                    parser = etree.XMLParser(recover=True)
                    self.tree = etree.fromstring(text, parser)
                else:
                    self.tree = parse_html(text)
            except Exception:  # noqa: BLE001
                self.tree = parse_html(text or "<html></html>")

    def _select(self, xpath: str) -> list[Any]:
        try:
            return list(self.tree.xpath(xpath))
        except Exception:  # noqa: BLE001
            return []

    @staticmethod
    def _node_string(node: Any) -> str:
        if isinstance(node, str):
            return node
        if hasattr(node, "text") and node.text is not None:
            return node.text
        try:
            return etree.tostring(node, encoding="unicode", method="html")
        except Exception:  # noqa: BLE001
            return str(node)

    def get_elements(self, xpath_rule: str) -> list[Any]:
        if not xpath_rule:
            return []
        ra = RuleAnalyzer(xpath_rule)
        rules = ra.split_rule("&&", "||", "%%")
        if len(rules) == 1:
            return self._select(rules[0])

        merged: list[Any] = []
        groups: list[list[Any]] = []
        for rl in rules:
            temp = self.get_elements(rl)
            if temp:
                groups.append(temp)
                if ra.elements_type == "||":
                    break
        if groups:
            if ra.elements_type == "%%":
                for i in range(len(groups[0])):
                    for g in groups:
                        if i < len(g):
                            merged.append(g[i])
            else:
                for g in groups:
                    merged.extend(g)
        return merged

    def get_string_list(self, xpath_rule: str) -> list[str]:
        result: list[str] = []
        ra = RuleAnalyzer(xpath_rule)
        rules = ra.split_rule("&&", "||", "%%")
        if len(rules) == 1:
            for node in self._select(xpath_rule):
                s = self._node_string(node)
                if s:
                    result.append(s)
            return result

        groups: list[list[str]] = []
        for rl in rules:
            temp = self.get_string_list(rl)
            if temp:
                groups.append(temp)
                if ra.elements_type == "||":
                    break
        if groups:
            if ra.elements_type == "%%":
                for i in range(len(groups[0])):
                    for g in groups:
                        if i < len(g):
                            result.append(g[i])
            else:
                for g in groups:
                    result.extend(g)
        return result

    def get_string(self, rule: str) -> str | None:
        ra = RuleAnalyzer(rule)
        rules = ra.split_rule("&&", "||")
        if len(rules) == 1:
            nodes = self._select(rule)
            if nodes:
                return "\n".join(s for s in (self._node_string(n) for n in nodes) if s)
            return None
        parts: list[str] = []
        for rl in rules:
            temp = self.get_string(rl)
            if temp:
                parts.append(temp)
                if ra.elements_type == "||":
                    break
        return "\n".join(parts) if parts else None
