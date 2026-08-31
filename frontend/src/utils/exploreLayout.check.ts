/**
 * exploreLayout 的一致性自校验（对照 legado 的 Kotlin 实现）。
 *
 * 前端没有测试运行器，这里用一个可直接执行的脚本保证 span 算法与
 * legado `ExploreKindLayout.calculateFlexRows` 一致 —— 这是「番茄发现页
 * 200 个按钮排得对不对」的唯一依据，改坏了不会有类型错误提示。
 *
 * 运行：  npx tsx frontend/src/utils/exploreLayout.check.ts
 *        （或 npx vite-node，项目没装 tsx 时用后者）
 */
import {
  calculateFlexRows,
  flexLayout,
  rowSpan,
  type FlexItemLayout,
  type RowItem,
} from "./exploreLayout";

let failed = 0;

function check(name: string, actual: unknown, expected: unknown): void {
  const a = JSON.stringify(actual);
  const e = JSON.stringify(expected);
  if (a === e) {
    console.log(`  ok   ${name}`);
  } else {
    failed += 1;
    console.log(`  FAIL ${name}\n       got      ${a}\n       expected ${e}`);
  }
}

/** 只关心每行 span 序列，便于比对。 */
function spans<T>(rows: RowItem<T>[][]): number[][] {
  return rows.map((r) => r.map((c) => c.span));
}

const L = (
  flexGrow: number,
  basisPercent: number,
  wrapBefore = false,
): FlexItemLayout => ({ flexGrow, basisPercent, wrapBefore });

// ---- span 计算：与 Kotlin spanOf 逐分支对照 -----------------------------
console.log("span 计算（maxSpan = 6）:");
{
  // basisPercent >= 1 → 整行
  check("basisPercent=1 占整行", spans(calculateFlexRows(
    ["a"], 6, () => L(1, 1))), [[6]]);
  // wrapBefore 强制整行
  check("wrapBefore 占整行", spans(calculateFlexRows(
    ["a"], 6, () => L(0, -1, true))), [[6]]);
  // basisPercent=0.45 → round(6*0.45)=3
  check("basisPercent=0.45 → 3", spans(calculateFlexRows(
    ["a", "b"], 6, () => L(1, 0.45))), [[3, 3]]);
  // flexGrow>0 且无 basis → 3
  check("flexGrow>0 无 basis → 3", spans(calculateFlexRows(
    ["a", "b"], 6, () => L(1, -1))), [[3, 3]]);
  // 都没有 → 2
  check("无 flexGrow/basis → 2", spans(calculateFlexRows(
    ["a", "b", "c"], 6, () => L(0, -1))), [[2, 2, 2]]);
  // 0.465 → round(2.79) = 3
  check("basisPercent=0.465 → 3", spans(calculateFlexRows(
    ["a", "b"], 6, () => L(0, 0.465))), [[3, 3]]);
}

// ---- 行尾补空：两个 0.45 的按钮在 6 格里各占 3 --------------------------
console.log("行尾补空:");
{
  // 3 个 span=2 的按钮在一行：remain=0，不变
  check("填满不补", spans(calculateFlexRows(
    ["a", "b", "c"], 6, () => L(0, 0.333))), [[2, 2, 2]]);
  // 2 个 span=2：remain=2，全等 span → 各 +1 → [3, 3]
  check("全等 span 均分余数", spans(calculateFlexRows(
    ["a", "b"], 6, () => L(0, 0.333))), [[3, 3]]);
  // span 不等：余数全给最后一个 → [2, 4]
  // 注意：回调只接收 item，差异要编码进 item 自身
  check("不等 span 余数给末位", spans(calculateFlexRows(
    [{ n: "a", p: 0.333 }, { n: "b", p: 0.6 }], 6, (it) => L(0, it.p))),
    [[2, 4]]);
  // 余数除不尽：3 个 span=1，remain=3 → 各 +1（extra=0）
  check("余数整除", spans(calculateFlexRows(
    ["a", "b", "c"], 6, () => L(0, 1 / 6))), [[2, 2, 2]]);
  // 余数除不尽：4 个 span=1，remain=2 → addEach=0, extra=2 → [2,2,1,1]
  check("余数不整除时逐个 +1", spans(calculateFlexRows(
    ["a", "b", "c", "d"], 6, () => L(0, 1 / 6))), [[2, 2, 1, 1]]);
}

// ---- 换行 ---------------------------------------------------------------
console.log("换行:");
{
  // 4 个 span=3 → 两行各 [3,3]
  check("4 个 span3 → 两行", spans(calculateFlexRows(
    ["a", "b", "c", "d"], 6, () => L(1, 0.45))), [[3, 3], [3, 3]]);
  // wrapBefore 且当前行非空 → 先收尾再换行
  const rows = calculateFlexRows(
    [{ n: "a", p: 0.333, w: false }, { n: "b", p: -1, w: true }],
    6,
    (it) => L(0, it.p, it.w),
  );
  check("wrapBefore 先收尾再换行", spans(rows), [[6], [6]]);
}

// ---- 番茄发现页的真实形状 ----------------------------------------------
console.log("番茄形状（标题行 + 榜单 + 控件）:");
{
  // 与 backend/tests/test_explore_ui.py::FQ_SHAPED 的 exploreUrl 同构
  const kinds: { title: string; style: Record<string, unknown> }[] = [
    { title: "༺ ✨番茄榜单✨ ༻", style: { layout_flexGrow: 1, layout_flexBasisPercent: 1 } },
    { title: "推荐榜", style: { layout_flexGrow: 1, layout_flexBasisPercent: 0.45 } },
    { title: "完本榜", style: { layout_flexGrow: 1, layout_flexBasisPercent: 0.45 } },
    { title: "关键词：", style: { layout_flexGrow: 1, layout_flexBasisPercent: 0.6 } },
    { title: "搜索", style: { layout_flexGrow: 1, layout_flexBasisPercent: -1 } },
    { title: "⚙", style: { layout_flexGrow: 1, layout_flexBasisPercent: -1 } },
    { title: "分类：", style: { layout_flexGrow: 1, layout_flexBasisPercent: 0.45 } },
    { title: "偏好：", style: { layout_flexGrow: 1, layout_flexBasisPercent: 0.45 } },
  ];
  const rows = calculateFlexRows(kinds, 6, (k) => flexLayout(k.style));
  // 逐行推导（maxSpan=6）：
  //   标题 basis=1            → 6              行1 = [6]
  //   推荐榜/完本榜 0.45→3     → 3+3=6          行2 = [3,3]
  //   关键词 0.6→4，搜索 -1(flexGrow)→3，4+3=7 放不下 →
  //     关键词先补满整行（余数 2 给末位）行3 = [6]，搜索另起一行
  //   ⚙ flexGrow→3，与搜索 3 同行              行4 = [3,3]
  //   分类/偏好 0.45→3                         行5 = [3,3]
  check("番茄形状分行", spans(rows), [[6], [3, 3], [6], [3, 3], [3, 3]]);
  // 每行总 span 都必须填满 6（前端据此决定要不要补白）
  check("每行填满 6", rows.map(rowSpan), [6, 6, 6, 6, 6]);
  check("控件都在，无丢失", rows.flat().map((c) => c.item.title), [
    "༺ ✨番茄榜单✨ ༻", "推荐榜", "完本榜", "关键词：", "搜索", "⚙", "分类：", "偏好：",
  ]);
}

// ---- 缺 style / 脏数据 --------------------------------------------------
console.log("缺省与脏数据:");
{
  check("缺 style 用默认值", flexLayout(null), {
    flexGrow: 0, basisPercent: -1, wrapBefore: false,
  });
  check("字符串数字被转换", flexLayout({ layout_flexBasisPercent: "0.5" })
    .basisPercent, 0.5);
  check("非法值回落默认", flexLayout({ layout_flexBasisPercent: "abc" })
    .basisPercent, -1);
  check("布尔不当数字", flexLayout({ layout_flexGrow: true }).flexGrow, 0);
  check("空列表 → 无行", calculateFlexRows([], 6, () => L(0, -1)), []);
}

console.log(failed === 0 ? "\n全部通过" : `\n${failed} 项失败`);
if (failed > 0) process.exit(1);
