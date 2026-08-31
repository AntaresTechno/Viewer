/*
 * rhino_compat.js — Rhino(Java) 兼容层，供 legado/阅读书源脚本使用。
 *
 * 背景：legado 的书源规则用 Android 自带的 Rhino 引擎执行。Rhino 向脚本暴露
 * `JavaImporter` / `importClass` / `importPackage` / `Packages` 等「导入 Java
 * 类」全局，因此大量书源（如番茄小说）在 jsLib / @js 规则里直接
 * `new JavaImporter(...)` + `with(javaImport){...}`，并调用 okhttp3 / hutool
 * 等 Android 库。quickjs / dukpy / STPyV8 都没有这些全局，
 * 于是在「JS 绑定初始化」阶段就抛
 *   ReferenceError: JavaImporter is not defined
 *
 * 本文件在每次创建 JS 上下文时、在求值书源 jsLib **之前**注入，
 * 提供 Rhino 兼容的：
 *   - Packages（Java 包树，支持 okhttp3 / cn.hutool.* / android.os.Build 等）
 *   - JavaImporter（构造器 + importPackage / importClass / statics 方法，
 *     对象可直接用作 `with(javaImport){...}` 的作用域）
 *   - importClass / importPackage 顶层全局
 *
 * 注意：整段必须处于 **非严格模式**（不得出现 "use strict"），否则书源里的
 * `with` 语句会被引擎拒绝；引擎内各类桥接（java.md5Encode、java._http …）
 * 是全局 `java` 对象的成员，运行期按需调用。
 */

/* eslint-disable */

(function () {
  var G =
    (typeof globalThis !== "undefined" && globalThis) ||
    (typeof self !== "undefined" && self) ||
    (typeof window !== "undefined" && window) ||
    this;

  // 安全访问 java 全局（不同引擎里可能由外部桥接注入，也可能不存在）。
  function _java() {
    return typeof java !== "undefined" ? java : null;
  }
  function _javaFn(name) {
    var j = _java();
    return j && typeof j[name] === "function" ? j[name] : null;
  }

  var LATIN1 = "iso-8859-1";

  // ---------------------------------------------------------------- Pack 树
  // 允许脚本直接写 `Packages.okhttp3.Request...` 等作为对象树访问；也允许
  // importPackage(Packages.xxx) 把该包下的短类名拷贝进 with 作用域。
  if (!G.Packages) G.Packages = {};

  var _okhttp3 = (G.Packages.okhttp3 = G.Packages.okhttp3 || {});
  var _android = (G.Packages.android = G.Packages.android || {});
  var _hutool = (G.Packages.cn = {
    hutool: {
      core: { util: {}, codec: {}, zip: {} },
      crypto: { digest: {} },
    },
  });

  // ------------------------------------------------------------ okhttp3 桥
  // 把 okhttp 式链式调用翻译成一个真实 HTTP 请求（经 java._http 回到 Python）。
  _okhttp3.MediaType = {
    parse: function (s) {
      var t = String(s);
      return { _type: t, toString: function () { return t; } };
    },
  };

  _okhttp3.RequestBody = {
    create: function (content, contentType) {
      return { _content: content, _contentType: contentType };
    },
  };

  _okhttp3.Request = {
    Builder: function () {
      this._url = null;
      this._method = "GET";
      this._headers = [];
      this._body = null;
    },
  };
  _okhttp3.Request.Builder.prototype.url = function (u) {
    this._url = String(u);
    return this;
  };
  _okhttp3.Request.Builder.prototype.get = function () {
    this._method = "GET";
    this._body = null;
    return this;
  };
  _okhttp3.Request.Builder.prototype.post = function (body) {
    this._method = "POST";
    this._body = body && body._content !== undefined ? body._content : body;
    return this;
  };
  _okhttp3.Request.Builder.prototype.addHeader = function (n, v) {
    this._headers.push([String(n), String(v)]);
    return this;
  };
  _okhttp3.Request.Builder.prototype.header = _okhttp3.Request.Builder.prototype.addHeader;
  _okhttp3.Request.Builder.prototype.build = function () {
    return {
      url: this._url,
      method: this._method,
      headers: this._headers,
      body: this._body,
    };
  };

  function _okhttpExecute(request) {
    var j = _java();
    var httpFn = j && typeof j.httpRequest === "function"
      ? j.httpRequest
      : (j && typeof j._http === "function" ? j._http : null);
    if (httpFn) {
      var headers = {};
      for (var i = 0; i < request.headers.length; i++) {
        var h = request.headers[i];
        headers[h[0]] = h[1];
      }
      var raw = null;
      try {
        raw = httpFn.call(
          j,
          request.method || "GET",
          String(request.url || ""),
          headers,
          request.body
        );
      } catch (e) { raw = null; }
      // httpRequest 桥返回 JSON 字符串（自带 code/body），避免各引擎对 dict
      // 返回值支持差异（dukpy 不把 dict 直接啥给 JS）。
      var res = raw;
      if (typeof raw === "string") {
        try { res = JSON.parse(raw); } catch (e) { res = { body: raw, code: 0 }; }
      }
      if (res && typeof res === "object") {
        var bodyText = res.body !== undefined ? String(res.body) : "";
        var code = res.code !== undefined ? parseInt(res.code, 10) : 200;
        return {
          code: function () { return code; },
          message: function () { return code >= 400 ? "HTTP " + code : "OK"; },
          body: function () {
            var strBytes = j && typeof j.strBytes === "function"
              ? j.strBytes
              : (j && typeof j._strBytes === "function" ? j._strBytes : null);
            return {
              string: function () { return bodyText; },
              bytes: function () { return strBytes ? strBytes(bodyText) : []; },
            };
          },
          headers: function () {
            return res.headers || {};
          },
        };
      }
    }
    return {
      code: function () { return -1; },
      message: function () { return "okhttp unavailable"; },
      body: function () {
        return { string: function () { return ""; }, bytes: function () { return []; } };
      },
      headers: function () { return {}; },
    };
  }

  _okhttp3.OkHttpClient = function () {};
  _okhttp3.OkHttpClient.prototype.newCall = function (request) {
    return { execute: function () { return _okhttpExecute(request); } };
  };

  // ----------------------------------------------------------------- hutool
  var _util = _hutool.hutool.core.util;
  var _codec = _hutool.hutool.core.codec;
  var _zip = _hutool.hutool.core.zip;
  var _digest = _hutool.hutool.crypto.digest;

  _util.StrUtil = {
    reverse: function (s) {
      s = String(s == null ? "" : s);
      return s.split("").reverse().join("");
    },
    isEmpty: function (s) { return s === null || s === undefined || String(s) === ""; },
  };
  _util.RandomUtil = {
    randomInt: function (min, max) {
      return Math.floor(Math.random() * (max - min)) + min;
    },
    randomLetter: function (n) {
      n = n || 6;
      var s = "";
      for (var i = 0; i < n; i++) s += String.fromCharCode(97 + Math.floor(Math.random() * 26));
      return s;
    },
  };
  _util.CharUtil = {};
  // hutool 的 ZipUtil 位于 cn.hutool.core.util（与 StrUtil 同包），
  // 番茄书源正是 importPackage(Packages.cn.hutool.core.util)。
  _util.ZipUtil = {
    gzip: function (data) {
      var fn = _javaFn("gzip");
      return fn ? String(fn(String(data == null ? "" : data))) : String(data);
    },
    unGzip: function (data) {
      var fn = _javaFn("ungzip");
      return fn ? String(fn(String(data == null ? "" : data))) : String(data);
    },
    unzip: function (data) {
      var fn = _javaFn("ungzip");
      return fn ? String(fn(String(data == null ? "" : data))) : String(data);
    },
  };
  _codec.Base64 = {
    encode: function (b) {
      var fn = _javaFn("base64Encode");
      var s = b === null || b === undefined ? "" : (typeof b === "string" ? b : String(b));
      return fn ? fn(s) : s;
    },
    encodeToString: function (bytes) {
      var fn = _javaFn("_bytesBase64Encode");
      return fn ? fn(bytes) : "";
    },
    decode: function (s) {
      var fn = _javaFn("base64Decode");
      return fn ? fn(String(s)) : "";
    },
  };
  _digest.DigestUtil = {
    md5Hex: function (s) {
      var fn = _javaFn("md5Encode");
      return fn ? String(fn(String(s == null ? "" : s))) : "";
    },
    sha1Hex: function (s) {
      var fn = _javaFn("sha1Encode");
      return fn ? String(fn(String(s))) : "";
    },
  };

  // ----------------------------------------------------------------- android
  var _build = (_android.os = { Build: {}, VERSION: {} });
  var _version = (_build.Build.VERSION = {
    SDK_INT: 33,
    RELEASE: "13",
    CODENAME: "REL",
    INCREMENTAL: "",
  });
  _build.Build.BRAND = "Xiaomi";
  _build.Build.MODEL = "2201123C";
  _build.Build.DISPLAY = "".replace("", "");
  _build.Build.PRODUCT = "viewer";

  // 常见 java.* / javax.* / java.util 短类名（部分书源直接 new java.lang.String 等）
  G.Packages.java = G.Packages.java || {};
  G.Packages.java.lang = G.Packages.java.lang || {};
  G.Packages.java.util = G.Packages.java.util || {};
  G.Packages.java.net = G.Packages.java.net || {};
  G.Packages.java.math = G.Packages.java.math || {};
  G.Packages.java.text = G.Packages.java.text || {};
  G.Packages.javax = G.Packages.javax || {};

  // -------------------------------------------------------- JavaImporter 作用域
  // 每个包对象是可枚举成员容器，importPackage 时把「包下的类/子包」浅拷贝
  // 到 with 作用域对象上，使 `with(imports){ MediaType... }` 能解析到这些名字。
  function _copyOwn(src, dst) {
    if (!src) return dst;
    var p;
    for (p in src) {
      if (Object.prototype.hasOwnProperty.call(src, p)) {
        dst[p] = src[p];
      }
    }
    return dst;
  }
  function _importAll(source, dst) {
    if (!source) return dst;
    // 数组：「importPackage(包1, 包2, ...)」可能会被调用方展开成数组
    if (typeof source.length === "number") {
      for (var i = 0; i < source.length; i++) _importAll(source[i], dst);
      return dst;
    }
    return _copyOwn(source, dst);
  }
  function _globalImportAll(source) {
    var g = {};
    _importAll(source, g);
    _copyOwn(g, G);
    return G;
  }

  var _importerProto = {
    importPackage: function () {
      for (var i = 0; i < arguments.length; i++) {
        _importAll(arguments[i], this);
      }
      return this;
    },
    importClass: function () {
      for (var i = 0; i < arguments.length; i++) {
        var c = arguments[i];
        if (c && typeof c === "object") _copyOwn(c, this);
      }
      return this;
    },
    statics: function (cls) {
      return _importAll(cls, this);
    },
  };

  // `new JavaImporter(可省略包); 实例.importPackage(...)` 两种用法都支持；
  // 返回对象带 importPackage 等原型方法，同时可直接作为 with 作用域。
  G.JavaImporter = function JavaImporter() {
    var scope = Object.create(_importerProto);
    for (var i = 0; i < arguments.length; i++) {
      _importAll(arguments[i], scope);
    }
    return scope;
  };

  // 顶层导入全局（Rhino 的 importClass/importPackage），best-effort。
  G.importClass = function () {
    for (var i = 0; i < arguments.length; i++) {
      var c = arguments[i];
      if (c && typeof c === "object") _copyOwn(c, G);
    }
  };
  G.importPackage = function () {
    for (var i = 0; i < arguments.length; i++) _globalImportAll(arguments[i]);
  };

  // 让 importPackage(Packages.xxx) 天然的“包容器”。上面 Packages.okhttp3 等
  // 就是这样的容器对象（层层嵌套）。padding：确保 okhttp3.Request 可 new。
  G._rhinoCompatLoaded = true;
})();