"""Legacy HTML player sandbox — bootstrap injection + bounded state handling.

The upstream ``ruleContent`` HTML runs inside an ``<iframe srcdoc>`` with
``sandbox="allow-scripts allow-presentation"`` (no same-origin, no top
navigation). We inject a controlled bootstrap *before* the source markup that:

- fronts a capacity-limited in-memory ``localStorage`` shim and reports
  get/set/remove to the parent via ``postMessage``;
- listens to the source's ``video``/``audio`` for play/pause/timeupdate/
  ended/durationchange and reports position/duration + the source's own
  "current episode" key;
- accepts a parent-pushed ``{__dsh_restore__}`` message to resume progress.

The parent page only accepts messages whose ``event.source`` matches the
iframe's ``contentWindow`` and whose payload matches ``is_inbound_message``.
"""
from __future__ import annotations

import json

# 旧式状态安全上限（plan §7.2）
MAX_LEGACY_STATE_BYTES = 16 * 1024   # 16 KiB
MAX_LEGACY_KEYS = 64
MAX_LEGACY_VALUE_CHARS = 2048

_BOOTSTRAP_JS = r"""
(function () {
  'use strict';
  function send(msg) {
    try { parent.postMessage(msg, '*'); } catch (e) {}
  }
  function uid() { return '__dsh_' + Math.random().toString(36).slice(2); }

  // ---- capacity-limited localStorage shim ----
  var store = {};
  var LIMIT = %(LIMIT)s, MAX_KEYS = %(MAX_KEYS)s, MAX_VAL = %(MAX_VAL)s;
  function okKey(k) { return typeof k === 'string' && k.length < 1024; }
  function okVal(v) { return typeof v === 'string' && v.length <= MAX_VAL; }
  function size() {
    var n = 0;
    for (var k in store) { if (Object.prototype.hasOwnProperty.call(store, k)) n += store[k].length; }
    return n;
  }
  var shim = {
    getItem: function (k) {
      if (!okKey(k)) return null;
      var v = Object.prototype.hasOwnProperty.call(store, k) ? store[k] : null;
      send({ __dsh__: 'kv', t: 'get', k: k, v: v });
      return v;
    },
    setItem: function (k, v) {
      if (!okKey(k) || !okVal(v)) { send({ __dsh__: 'kv', t: 'set', k: k, err: 'value_too_large' }); return; }
      var prev = size();
      if (Object.prototype.hasOwnProperty.call(store, k)) {
        prev -= store[k].length;
      } else if (Object.keys(store).length >= MAX_KEYS) {
        send({ __dsh__: 'kv', t: 'set', k: k, err: 'too_many_keys' });
        return;
      }
      if (prev + v.length > LIMIT) { send({ __dsh__: 'kv', t: 'set', k: k, err: 'over_capacity' }); return; }
      store[k] = String(v);
      send({ __dsh__: 'kv', t: 'set', k: k, v: String(v) });
    },
    removeItem: function (k) {
      if (!okKey(k)) return;
      var had = Object.prototype.hasOwnProperty.call(store, k);
      delete store[k];
      send({ __dsh__: 'kv', t: 'remove', k: k });
    },
    key: function (i) { return Object.keys(store)[i] ?? null; },
    get length() { return Object.keys(store).length; },
    clear: function () { store = {}; send({ __dsh__: 'kv', t: 'clear' }); }
  };
  Object.defineProperty(shim, 'length', { get: function () { return Object.keys(store).length; } });

  try {
    // 若沙箱内真 localStorage 存在，仅用作读回；跨消息仍走 shim 上报
    if (window.localStorage) {
      var saved = window.localStorage.getItem('__dsh_media_cache');
      try { if (saved) store = JSON.parse(saved) || {}; } catch (e) {}
    }
    // 不 replace 原生 localStorage（部分脚本用它的引用），只在写路径上报；
    // 生产隔离场景 keep shim as primary.
    if (typeof window.localStorage !== 'undefined') {
      var _origSet = window.localStorage.setItem.bind(window.localStorage);
      var _origGet = window.localStorage.getItem.bind(window.localStorage);
      var _origDel = window.localStorage.removeItem.bind(window.localStorage);
      window.localStorage.setItem = function (k, v) { try { _origGet(k); } catch (e) {} shim.setItem(k, v); };
      window.localStorage.getItem = function (k) { return _origGet(k); };
      window.localStorage.removeItem = function (k) { _origDel(k); shim.removeItem(k); };
    }
  } catch (e) {}

  // ---- media event reporting ----
  function mediaMeta() {
    var v = document.querySelector('video') || document.querySelector('audio');
    if (!v) return null;
    return { u: (typeof v.currentSrc === 'string' ? v.currentSrc : (v.src || '')),
             s: Math.floor(v.currentTime || 0), d: Math.floor(v.duration || 0) };
  }
  function report(kind) {
    var m = mediaMeta();
    if (!m) return;
    // 额外抓源自己写的「当前集」键值
    var ep = '';
    var common = ['current_episode', 'currentEp', 'ep', 'episode', 'currentItem', 'index'];
    for (var i = 0; i < common.length; i++) {
      try { var raw = shim.getItem(common[i]); if (raw !== null && raw !== undefined) { ep = common[i] + '=' + raw; break; } } catch (e) {}
    }
    send({ __dsh__: 'media', kind: kind, s: m.s, d: m.d, url: m.u, ep: ep });
  }

  function bindMedia() {
    var v = document.querySelector('video') || document.querySelector('audio');
    if (!v) return;
    v.addEventListener('play', function () { report('play'); });
    v.addEventListener('pause', function () { report('pause'); });
    v.addEventListener('ended', function () { report('ended'); });
    v.addEventListener('durationchange', function () { report('duration'); });
    var last = 0;
    v.addEventListener('timeupdate', function () {
      var now = v.currentTime || 0;
      if (now - last >= 5) { last = now; report('timeupdate'); }
    });
  }
  // 源脚本可能异步创建 video，观察 DOM
  if (window.MutationObserver) {
    try {
      new MutationObserver(function () { bindMedia(); }).observe(document.documentElement, { childList: true, subtree: true });
    } catch (e) {}
  }
  setTimeout(bindMedia, 0);

  // ---- receive restore from parent ----
  window.addEventListener('message', function (ev) {
    if (!ev.data || typeof ev.data !== 'object') return;
    if (ev.data.__dsh_restore__) {
      var st = ev.data.__dsh_restore__;
      try {
        if (st.kv) { store = st.kv; }
        var v = document.querySelector('video') || document.querySelector('audio');
        if (v && st.seconds) { try { v.currentTime = st.seconds; } catch (e) {} }
        if (st.episode) { shim.setItem('current_episode', String(st.episode)); shim.setItem('currentEp', String(st.episode)); }
      } catch (e) {}
      send({ __dsh__: 'ready' });
    }
  });

  send({ __dsh__: 'boot' });
})();
"""


def build_srcdoc(source_html: str) -> str:
    """Prepend the injected bootstrap to the upstream legacy HTML."""
    js = _BOOTSTRAP_JS % {
        "LIMIT": MAX_LEGACY_STATE_BYTES,
        "MAX_KEYS": MAX_LEGACY_KEYS,
        "MAX_VAL": MAX_LEGACY_VALUE_CHARS,
    }
    return "<script>\n%s\n</script>\n%s" % (js, source_html or "")


def is_inbound_message(data, event_source, iframe_window) -> bool:
    """Only accept postMessages from our iframe with the fixed schema."""
    if data is None or not isinstance(data, dict):
        return False
    if not isinstance(data.get("__dsh__"), str):
        return False
    if event_source is not None and iframe_window is not None and event_source is not iframe_window:
        return False
    kind = data.get("__dsh__")
    if kind == "media":
        return set(data.keys()) >= {"__dsh__", "kind", "s", "d"}
    if kind == "kv":
        return isinstance(data.get("k"), str)
    return kind in ("boot", "ready", "clear")


def sanitize_legacy_state(state: dict) -> dict:
    """Bounded copy of legacy state before persisting (size/key/value limits)."""
    if state is None:
        return {}
    if not isinstance(state, dict):
        return {}
    out: dict = {}
    for k, v in state.items():
        if not isinstance(k, str) or len(k) > 256:
            continue
        if isinstance(v, str):
            v = v[:MAX_LEGACY_VALUE_CHARS]
        elif isinstance(v, (int, float, bool)) or v is None:
            v = v
        else:
            v = v if isinstance(v, list) and len(v) <= 16 else json.dumps(v, ensure_ascii=False)[:MAX_LEGACY_VALUE_CHARS]
        out[k] = v
        if len(out) >= MAX_LEGACY_KEYS:
            break
    total = len(json.dumps(out, ensure_ascii=False))
    if total > MAX_LEGACY_STATE_BYTES:
        # 截断最长的值直到满足大小上限
        keys = sorted(out, key=lambda kk: len(str(out[kk])), reverse=True)
        for kk in keys:
            val = out[kk]
            if isinstance(val, str) and len(val) > 32:
                out[kk] = val[:32]
            else:
                del out[kk]
            if len(json.dumps(out, ensure_ascii=False)) <= MAX_LEGACY_STATE_BYTES:
                break
    return out


def merge_legacy_state(existing: dict, incoming: dict) -> dict:
    base = dict(existing or {})
    for k, v in (incoming or {}).items():
        if isinstance(v, str) and not v:
            base.pop(k, None)
        else:
            base[k] = v
    return sanitize_legacy_state(base)