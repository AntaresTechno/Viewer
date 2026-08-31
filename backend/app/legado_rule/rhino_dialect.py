"""Rhino dialect helpers for book-source scripts.

legado runs book-source JS on Rhino, which differs from ES6 engines
(quickjs / dukpy / V8) in two ways that real sources depend on:

1. ``const``/``let`` inside a ``with (...) { ... }`` block keep **script
   scope** on Rhino, so the names stay visible after the block ends. On ES6
   they are block-scoped and vanish. The 番茄 (fanqie) source declares
   ``md5``/``gzip``/``Base64`` helpers that way and calls them from other
   rules.

2. A direct ``eval(code)`` on Rhino leaks ``let``/``const`` declarations into
   the enclosing scope. Sources use this as a state-injection mechanism:
   nearly every rule starts with ``eval(String(source.loginUrl))``, and
   ``loginUrl`` declares ``let ck`` / ``var $$$`` / ``function test()``
   that the rule then reads. On ES6 the ``let`` bindings stay trapped inside
   the eval, so ``ck`` is ``undefined`` and the rules fail.

:func:`normalize_js_lib` fixes (1) for the source ``jsLib``;
:func:`normalize_eval_leak` fixes (2) at run time.
"""
from __future__ import annotations

import json
import re

# A `with (` header at the start of a line (leading whitespace allowed).
_WITH_RE = re.compile(r"^([ \t]*)with[ \t]*\(", re.MULTILINE)
# A top-level `const`/`let` declaration inside the block, matched by its
# indentation. Anything deeper (a nested block/function body) is left alone
# because Rhino's block scoping genuinely differs there and rewriting could
# change behaviour.
_DECL_RE = re.compile(
    r"^([ \t]*)(const|let)([ \t]+)([A-Za-z_$][\w$]*)([ \t]*=)"
)
# `eval(...)` call with a non-trivial argument.
_EVAL_RE = re.compile(r"(?<![.\w$])eval\s*\(")
# `eval("...")` / `eval('...')` — string-literal argument, rewritten statically.
_EVAL_LITERAL_RE = re.compile(
    r"eval\s*\(\s*(?:\"((?:[^\"\\]|\\.)*)\"|'((?:[^'\\]|\\.)*)')\s*([)])"
)


def _unescape_single(s: str) -> str:
    """Decode a JS single-quoted string body."""
    out: list[str] = []
    i = 0
    n = len(s)
    while i < n:
        ch = s[i]
        if ch == "\\" and i + 1 < n:
            nxt = s[i + 1]
            out.append({
                "n": "\n", "t": "\t", "r": "\r", "b": "\b", "f": "\f",
                "0": "\0", "\\": "\\", "'": "'", '"': '"',
            }.get(nxt, nxt))
            i += 2
            continue
        out.append(ch)
        i += 1
    return "".join(out)


def _match_brace(text: str, open_idx: int) -> int:
    """Index just past the ``}`` matching the ``{`` at ``open_idx``, or -1.

    Skips braces inside string / template / regex / comment regions so that a
    stray ``}`` in a source string does not end the block early.
    """
    depth = 0
    i = open_idx
    n = len(text)
    while i < n:
        ch = text[i]
        if ch in "\"'`":
            quote = ch
            i += 1
            while i < n:
                if text[i] == "\\":
                    i += 2
                    continue
                if text[i] == quote:
                    i += 1
                    break
                i += 1
            continue
        if ch == "/" and i + 1 < n and text[i + 1] == "/":
            nl = text.find("\n", i)
            i = n if nl == -1 else nl + 1
            continue
        if ch == "/" and i + 1 < n and text[i + 1] == "*":
            end = text.find("*/", i + 2)
            i = n if end == -1 else end + 2
            continue
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return i + 1
        i += 1
    return -1


def _find_block_end(text: str, header_end: int) -> tuple[int, int]:
    """Return (body_start, body_end) for the ``with`` block.

    ``header_end`` is the index just past the closing ``)`` of ``with (...)``.
    Handles both ``with (x) {`` and ``with (x) stmt;``.
    """
    i = header_end
    n = len(text)
    while i < n and text[i] in " \t\r\n":
        i += 1
    if i >= n:
        return i, i
    if text[i] != "{":
        # `with (x) stmt;` — Rhino leaks nothing useful here; leave as-is.
        return i, i
    close = _match_brace(text, i)
    if close == -1:
        return i, i
    return i + 1, close - 1


def _find_paren_end(text: str, open_idx: int) -> int:
    """Index just past the ``)`` matching the ``(`` at ``open_idx``, or -1."""
    depth = 0
    i = open_idx
    n = len(text)
    while i < n:
        ch = text[i]
        if ch in "\"'`":
            quote = ch
            i += 1
            while i < n:
                if text[i] == "\\":
                    i += 2
                    continue
                if text[i] == quote:
                    i += 1
                    break
                i += 1
            continue
        if ch == "/" and i + 1 < n and text[i + 1] == "/":
            nl = text.find("\n", i)
            i = n if nl == -1 else nl + 1
            continue
        if ch == "/" and i + 1 < n and text[i + 1] == "*":
            end = text.find("*/", i + 2)
            i = n if end == -1 else end + 2
            continue
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
            if depth == 0:
                return i + 1
        i += 1
    return -1


def normalize_js_lib(code: str) -> str:
    """Rewrite top-level ``const``/``let`` inside ``with`` blocks into ``var``.

    Only declarations at the *body* indentation level of the ``with`` block are
    rewritten; nested declarations keep their original keyword. Returns the
    code unchanged when there is nothing to do or the braces do not balance.
    """
    if not code or " with" not in code and "with(" not in code:
        return _globalize_this(code)
    if "const " not in code and "let " not in code:
        return _globalize_this(code)

    out: list[str] = []
    pos = 0
    n = len(code)
    changed = False

    for m in _WITH_RE.finditer(code):
        header_end = _find_paren_end(code, m.end() - 1)
        if header_end == -1:
            continue
        body_start, body_end = _find_block_end(code, header_end)
        if body_end <= body_start:
            continue

        block = code[body_start:body_end]
        if "const " not in block and "let " not in block:
            continue

        # Indentation of the first non-empty line defines "top level" here.
        lines = block.split("\n")
        base_indent = None
        for ln in lines:
            if ln.strip():
                base_indent = ln[: len(ln) - len(ln.lstrip())]
                break
        if base_indent is None:
            continue

        rebuilt: list[str] = []
        for ln in lines:
            dm = _DECL_RE.match(ln)
            if dm and dm.group(1) == base_indent:
                rebuilt.append(
                    f"{dm.group(1)}var{dm.group(3)}{dm.group(4)}{dm.group(5)}"
                    + ln[dm.end():]
                )
                changed = True
            else:
                rebuilt.append(ln)
        new_block = "\n".join(rebuilt)

        out.append(code[pos:body_start])
        out.append(new_block)
        pos = body_end

    if not changed:
        return _globalize_this(code)
    out.append(code[pos:n])
    return _globalize_this("".join(out))


def _globalize_this(code: str) -> str:
    """Rewrite Rhino's top-level ``this`` into ``globalThis``.

    Rhino binds top-level ``this`` to the global scope, which is where the
    bridges live; ES6 leaves it ``undefined``. Book sources destructure it to
    reach the bridges::

        function Map(e) {
          const { java, source, cookie, cache } = this;   // 番茄 jsLib
          ...
        }

    Only the exact ``= this;`` statement form is rewritten, so object methods
    and event handlers keep their own ``this``.
    """
    if not code or "this" not in code:
        return code
    return _TOPLEVEL_THIS_RE.sub(_this_sub, code)


# `= this;` / `= this` inside a destructuring assignment (possibly on one line).
_TOPLEVEL_THIS_RE = re.compile(r"=\s*this\s*;")


def _this_sub(_m: "re.Match[str]") -> str:
    return '= (typeof globalThis !== "undefined" ? globalThis : this);'


# --------------------------------------------------------------- eval leak
# Rhino's direct eval declares `let`/`const` into the *enclosing* scope; ES6
# keeps them local to the eval. Sources lean on the Rhino behaviour to inject
# state (番茄: `let ck` inside loginUrl, read by every rule afterwards).
#
# We cannot retrofit that into the engine, so `normalize_eval_leak` rewrites
# `eval(X)` into `__rhinoEval(X)`: a helper that runs the code and then
# re-exports any name it declared that was not already defined, using an
# assignment on the global object (works identically on quickjs/dukpy/V8).
_LEGAL_EXPORT = re.compile(r"^[A-Za-z_$][\w$]*$")

# `let`/`const` at the very start of a declaration, for the top level of an
# eval'd string. `var` and `function` already leak on ES engines (they are
# function/script scoped), so only these two need rewriting.
_TOPLEVEL_DECL_RE = re.compile(
    r"(^|[;{}\n])([ \t]*)(let|const)([ \t\n]+)([A-Za-z_$][\w$]*)",
)


def _leak_decl_sub(m: "re.Match[str]") -> str:
    return f"{m.group(1)}{m.group(2)}var{m.group(4)}{m.group(5)}"


def _leak_declarations(src: str) -> str:
    """Turn top-level ``let``/``const`` in ``src`` into ``var``.

    Only replaces when the keyword starts a declaration, so occurrences inside
    strings, comments, or as property/argument names are left alone.
    """
    if "let " not in src and "let\n" not in src \
            and "const " not in src and "const\n" not in src:
        return src
    return _TOPLEVEL_DECL_RE.sub(_leak_decl_sub, src)


def _toplevel_let_const(code: str) -> set[str]:
    """Names this code declares with a top-level ``let``/``const``.

    Only the outermost indentation level is considered, which is where the
    collision with an eval'd script's declarations actually happens.
    """
    names: set[str] = set()
    if not code or ("let " not in code and "const " not in code):
        return names
    for m in _TOPLEVEL_DECL_RE.finditer(code):
        names.add(m.group(5))
    return names


def normalize_eval_leak(code: str, declared: set[str] | None = None) -> str:
    """Make ``eval(X)`` leak top-level ``let``/``const`` like Rhino does.

    Rhino's direct eval declares ``let``/``const`` into the enclosing scope;
    ES6 keeps them trapped. Sources lean on this for state injection: nearly
    every 番茄 rule begins with ``eval(String(source.loginUrl))``, and
    ``loginUrl`` declares ``let ck`` that the rule then reads.

    A JS wrapper cannot do this — an ``eval`` nested inside another function is
    an *indirect* eval that leaks nothing, so routing the call through a helper
    would break even the ``var`` declarations that already work. Instead the
    argument expression becomes ``__rhinoLeak(X)``, which normalizes the
    *text* before the (still direct) ``eval`` consumes it. The wrapping call
    needs a closing paren, so the matching ``)`` of each rewritten ``eval`` is
    located and annotated.

    Returns the code unchanged when it contains no ``eval`` call.
    """
    if not code or "eval" not in code:
        return code
    if not _EVAL_RE.search(code):
        return code

    skip = set(declared or ()) | _toplevel_let_const(code)

    out: list[str] = []
    pos = 0
    pending: list[int] = []             # end offsets of evals awaiting rewrite
    for m in _EVAL_RE.finditer(code):
        # Skip evals nested inside an outer eval's argument: those are ordinary
        # dynamic statements (番茄: eval('i=$$$.' + e)), and rewriting them
        # changes their meaning.
        if any(m.start() < e for e in pending):
            continue
        open_idx = m.end() - 1          # index of '('
        close = _find_paren_end(code, open_idx)
        if close == -1:
            continue
        pending.append(close)
        # close-1 is the ')' that belongs to this eval call.
        arg = code[open_idx + 1: close - 1]
        out.append(code[pos:m.start()])
        # 关键：必须是「直接 eval」—— 包进函数会变成 indirect eval，
        # 连 var/function 都不再泄漏（Rhino 之所以能注入状态正是靠直接
        # eval）。所以只改写**实参**：__rhinoLeak 把顶层 let/const 换成
        # globalThis 赋值。skip 里的名字是外层自己也声明了的（番茄 intro
        # 规则 eval(loginUrl) 后紧接 `let ck`），改它们要么撞 redeclaration，
        # 要么被外层 let 的 TDZ 拦成 "ck is not initialized"。
        out.append(
            f"eval(__rhinoLeak({arg}, {json.dumps(sorted(skip))}))"
        )
        pos = close
    if not out:
        return code
    out.append(code[pos:])
    return "".join(out)


LEAK_HELPER_JS = r"""
if (typeof __rhinoLeak === 'undefined') {
  // Rhino 的直接 eval 让顶层 let/const 泄漏到外层作用域，ES 引擎不会；
  // 书源靠这个注入状态（番茄：loginUrl 的 `let ck`，后续规则直接读）。
  //
  // 做法是改写成 globalThis 赋值：只替换「关键字 + 名字」，语句剩余部分
  // （= 初值、跨行对象字面量、多声明符）原样保留。
  //
  // 不能直接改成 var —— 规则自己也在顶层声明了同名变量时（番茄 intro
  // 规则：eval(loginUrl) 之后又写 `let ck`），eval 里的 `var ck` 会撞上
  // 外层的词法声明，抛 SyntaxError: redeclaration。globalThis 赋值不受
  // 词法绑定影响，且规则自带声明时会正常遮蔽它，语义与 Rhino 一致。
  var __rhinoLeak = function (src, skip) {
    var text = String(src == null ? '' : src);
    if (text.indexOf('let') < 0 && text.indexOf('const') < 0) {
      return text;
    }
    var skipMap = {};
    if (skip) {
      for (var i = 0; i < skip.length; i++) { skipMap[skip[i]] = 1; }
    }
    return text.replace(
      /(^|[;{}\n])([ \t]*)(let|const)([ \t\n]+)([A-Za-z_$][\w$]*)/g,
      function (m, a, b, kw, d, name) {
        if (skipMap[name]) { return m; }
        return a + b + 'globalThis.' + name;
      }
    );
  };
}
"""


__all__ = ["normalize_js_lib", "normalize_eval_leak", "LEAK_HELPER_JS"]
