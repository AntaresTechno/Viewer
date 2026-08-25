"""净化规则来源目录。

三个来源（与净化页三张卡一一对应）：

1. **内置净化** — ``key="builtin-md3"``。引擎内置的清洗层，以代码实现
   为管线固定第一步，默认启用、不可删除；
2. **乌云净化** — ``key="wuyun"``。社区成包（随插件分发于
   data/wuyun.json），一键安装为本地规则包，安装后默认停用；
3. **自定义规则** — 上传 JSON / URL 拉取 / 粘贴导入，见 plugin.py 的
   import 系列路由。
"""
from __future__ import annotations

import json
from importlib import resources

# 乌云净化打包文件（legado 替换规则导出格式）
WUYUN_KEY = "wuyun"
_WUYUN_RESOURCE = "data/wuyun.json"

BUILTIN_SOURCES: list[dict] = [
    {
        "key": "builtin-md3",
        "title": "内置净化",
        "description": "基础清洗层，始终生效：清理实体字符与不可见字符、"
                       "剥离 HTML 残留、规整段落缩进。",
        "installable": False,
    },
    {
        "key": WUYUN_KEY,
        "title": "乌云净化",
        "description": "社区流行的替换净化方案，覆盖排版格式与广告推广清理。",
        "installable": True,
    },
]


def load_wuyun_rules() -> list[dict]:
    """读取打包的乌云净化 JSON（legado ReplaceRule 数组）。"""
    text = resources.files(__package__).joinpath(_WUYUN_RESOURCE).read_text(
        encoding="utf-8"
    )
    obj = json.loads(text)
    if isinstance(obj, dict):
        return [obj]
    return [r for r in obj if isinstance(r, dict)]


def preset_by_key(key: str) -> dict | None:
    for p in BUILTIN_SOURCES:
        if p["key"] == key:
            return p
    return None
