/*
 * legado_objects.js — JS-side wrappers restoring the *object* shapes legado
 * exposes to rule scripts (StrResponse, login-info Map).
 *
 * Why this file exists: the Python<->JS bridges can only cross the boundary as
 * scalars (quickjs raises "Can not convert Python result to JS" for a Python
 * list/dict). legado book sources, however, expect real objects:
 *
 *   java.ajaxAll(urls)       -> Array<StrResponse>  (item.body())
 *   java.connect(url)        -> StrResponse         (.raw().request().url())
 *   source.getLoginInfoMap() -> java.util.Map       (.get(key))
 *
 * The Python side therefore returns JSON *strings*, and this prelude turns them
 * back into objects. Injected after rhino_compat.js and before the source
 * jsLib, for every engine (quickjs / dukpy / stpyv8).
 */
/* eslint-disable */

(function () {
  var G =
    (typeof globalThis !== "undefined" && globalThis) ||
    (typeof self !== "undefined" && self) ||
    this;

  function toObject(raw) {
    var o = raw;
    if (typeof raw === "string") {
      try {
        o = JSON.parse(raw);
      } catch (e) {
        o = {};
      }
    }
    return o && typeof o === "object" ? o : {};
  }

  function toArray(raw) {
    var arr = raw;
    if (typeof raw === "string") {
      try {
        arr = JSON.parse(raw);
      } catch (e) {
        arr = [];
      }
    }
    if (arr === null || arr === undefined) return [];
    return Array.isArray(arr) ? arr : [arr];
  }

  // Mirrors io.legado.app.help.http.StrResponse: body()/url()/code()/raw()
  // plus the `.raw().request().url()` chain sources use to read a final
  // (post-redirect) URL.
  function mkResponse(raw) {
    var o = toObject(raw);
    var body = o.body !== undefined && o.body !== null ? String(o.body) : "";
    var url = o.url !== undefined && o.url !== null ? String(o.url) : "";
    var code = o.code !== undefined ? parseInt(o.code, 10) : 0;
    if (isNaN(code)) code = 0;
    var message = o.message !== undefined ? String(o.message) : "";
    var headers = o.headers && typeof o.headers === "object" ? o.headers : {};
    var method = o.method !== undefined ? String(o.method) : "GET";

    var request = {
      url: function () {
        return url;
      },
      method: function () {
        return method;
      },
      headers: function () {
        return headers;
      },
    };
    var rawResp = {
      request: function () {
        return request;
      },
      code: function () {
        return code;
      },
      message: function () {
        return message;
      },
      headers: function () {
        return headers;
      },
      isSuccessful: function () {
        return code >= 200 && code < 400;
      },
    };

    return {
      body: function () {
        return body;
      },
      url: function () {
        return url;
      },
      code: function () {
        return code;
      },
      message: function () {
        return message;
      },
      headers: function () {
        return headers;
      },
      raw: function () {
        return rawResp;
      },
      isSuccessful: function () {
        return code >= 200 && code < 400;
      },
      callTime: function () {
        return 0;
      },
      errorBody: function () {
        return null;
      },
      toString: function () {
        return body;
      },
    };
  }

  // A java.util.Map-alike. legado hands rule scripts a MutableMap and sources
  // call `.get(key)` on it; a plain JS object has no `.get`. Deliberately NOT
  // named `Map` — book sources routinely shadow that identifier with their own
  // helper function (e.g. the 番茄 source's `function Map(e) {...}`).
  function mkMap(raw) {
    var data = toObject(raw);
    return {
      get: function (k) {
        var v = data[k];
        return v === undefined || v === null ? null : String(v);
      },
      put: function (k, v) {
        data[k] = v;
        return v;
      },
      // legado 的 InfoMap.set 有两种用法，都要支持：
      //   infoMap.set(newMap)   —— 整体替换（MutableMap.set(map)）
      //   infoMap.set(k, v)     —— 单个键（两参形式）
      set: function (k, v) {
        if (arguments.length === 1) {
          var obj = toObject(k);
          Object.keys(data).forEach(function (key) {
            delete data[key];
          });
          Object.keys(obj).forEach(function (key) {
            data[key] = obj[key];
          });
          return data;
        }
        data[k] = v;
        return v;
      },
      // InfoMap.save(time, need)：写盘。持久化由 bridge 在 put/set 时落盘，
      // 这里只需是可调用对象 —— 书源 saveKeys() 会显式调用它。
      save: function (time, need) {
        return true;
      },
      remove: function (k) {
        delete data[k];
      },
      containsKey: function (k) {
        return Object.prototype.hasOwnProperty.call(data, String(k));
      },
      isEmpty: function () {
        return Object.keys(data).length === 0;
      },
      size: function () {
        return Object.keys(data).length;
      },
      keySet: function () {
        return Object.keys(data);
      },
      values: function () {
        return Object.keys(data).map(function (k) {
          return data[k];
        });
      },
      forEach: function (fn) {
        Object.keys(data).forEach(function (k) {
          fn(data[k], k);
        });
      },
      toJSON: function () {
        return data;
      },
      toString: function () {
        return JSON.stringify(data);
      },
    };
  }

  if (typeof java !== "undefined" && java) {
    var _ajaxAll = java.ajaxAll;
    java.ajaxAll = function (urls) {
      if (!_ajaxAll) return [];
      var raw;
      try {
        raw = _ajaxAll.apply(java, arguments);
      } catch (e) {
        return [];
      }
      return toArray(raw).map(function (item) {
        return mkResponse(item);
      });
    };

    var _connect = java.connect;
    java.connect = function (urlStr) {
      if (!_connect) return mkResponse({});
      var raw;
      try {
        raw = _connect.apply(java, arguments);
      } catch (e) {
        return mkResponse({});
      }
      return mkResponse(raw);
    };
  }

  // infoMap（发现页输入）桥 → MutableMap 形态。
  //
  // legado 的 `InfoMap : MutableMap<String, String>`，书源**两种取法都用**：
  //   infoMap['关键词：']     ← 下标（番茄书源通篇这个写法）
  //   infoMap.get('关键词：') ← 方法
  // 但桥对象只有方法、没有数据字段，下标读出来是 undefined，番茄的每条
  // action 都会静默拿到空值。这里把桥包一层：优先透传数据键，键名撞上
  // 桥方法名时（极罕见）以方法为准，其余方法照旧。
  if (typeof infoMap !== "undefined" && infoMap) {
    var _infoMapBridge = infoMap;
    var _infoMapGet = typeof infoMap.get === "function" ? infoMap.get : null;
    var _infoMapPut = typeof infoMap.put === "function" ? infoMap.put : null;
    if (_infoMapGet) {
      var _infoMapData = toObject(
        typeof infoMap.toJSON === "function" ? infoMap.toJSON() : {}
      );
      var _infoMapProxy = {};
      // 数据键（不存在于桥方法集合里时才铺开，避免覆盖 get/put/save…）
      Object.keys(_infoMapData).forEach(function (key) {
        if (!(key in _infoMapBridge)) _infoMapProxy[key] = _infoMapData[key];
      });
      // 桥方法：读写都落到桥（持久化由桥负责）
      Object.keys(_infoMapBridge).forEach(function (name) {
        if (typeof _infoMapBridge[name] !== "function") return;
        _infoMapProxy[name] = _infoMapBridge[name].bind(_infoMapBridge);
      });

      // 序列化：书源会把 infoMap 整体传回 Python 桥（番茄的
      // `saveKeys(infoMap)` → `infoMap.set(infoMap)`）。Python 侧
      // `_unwrap_arg` 靠 `.json()` 取内容；若这里返回空串，它会原样保留
      // JS 对象，quickjs 转换 Proxy 时直接 abort。所以必须给出数据快照。
      var _snapshot = function () {
        var out = {};
        Object.keys(_infoMapProxy).forEach(function (key) {
          if (typeof _infoMapProxy[key] === "function") return;
          out[key] = _infoMapProxy[key];
        });
        return out;
      };
      _infoMapProxy.json = function () {
        return JSON.stringify(_snapshot());
      };
      _infoMapProxy.toJSON = function () {
        return _snapshot();
      };
      _infoMapProxy.toString = function () {
        return JSON.stringify(_snapshot());
      };

      // 写：下标赋值要落盘，不能只改本地副本
      var _infoMapHandler = {
        get: function (target, prop) {
          if (typeof prop === "string" && !(prop in target)) {
            // 未缓存的键：临时向桥查询（桥可能刚被 JS 别处写入）
            var v = _infoMapGet.call(_infoMapBridge, prop);
            if (v !== null && v !== undefined && v !== "") return String(v);
          }
          return target[prop];
        },
        set: function (target, prop, value) {
          target[prop] = value;
          if (_infoMapPut && typeof prop === "string") {
            try {
              _infoMapPut.call(_infoMapBridge, prop, String(value));
            } catch (e) {}
          }
          return true;
        },
        has: function (target, prop) {
          return prop in target || _infoMapGet.call(_infoMapBridge, prop) !== "";
        },
        ownKeys: function (target) {
          return Object.keys(_snapshot());
        },
      };
      try {
        infoMap = new Proxy(_infoMapProxy, _infoMapHandler);
      } catch (e) {
        // 引擎不支持 Proxy（dukpy/旧 quickjs）时退回「数据 + 方法」的
        // 平铺对象：下标读取能用，只是新增键不落盘。
        infoMap = _infoMapProxy;
      }
    }
  }

  if (
    typeof source !== "undefined" &&
    source &&
    typeof source.getLoginInfoMap === "function"
  ) {
    var _getLoginInfoMap = source.getLoginInfoMap;
    source.getLoginInfoMap = function () {
      var raw;
      try {
        raw = _getLoginInfoMap.apply(source, arguments);
      } catch (e) {
        return mkMap({});
      }
      return mkMap(raw);
    };
  }

  G._legadoObjectsLoaded = true;
})();
