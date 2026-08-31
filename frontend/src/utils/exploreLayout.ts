/**
 * 发现页控件的 flex 行布局（直译 legado 的 `calculateFlexRows`）。
 *
 * 参考：legado-with-MD3-main/.../ui/widget/components/explore/ExploreKindLayout.kt
 *
 * legado 的发现页不是列表而是 flex 网格：书源通过 `style` 里的
 * `layout_flexBasisPercent` / `layout_flexGrow` / `layout_wrapBefore`
 * 控制每个按钮占多宽、是否强制换行。番茄书源靠这个搭出
 * 「标题独占一行 + 榜单按钮两两并排 + 下拉框两个一行」的层次，
 * 忽略它们的话 ~200 个按钮会全部退化成一列。
 *
 * 算法要点（必须与 Kotlin 版一致，否则视觉对不上）：
 * - span 计算：`wrapBefore || basisPercent >= 1` → 整行；
 *   `basisPercent > 0` → round(maxSpan * p)；`flexGrow > 0` → 3；否则 2。
 * - 行尾补空：一行没填满时，若行内 span 全相同则均分余数（多出的逐个 +1），
 *   否则全给最后一个 —— 这让「两个 0.45 的按钮」在 maxSpan=6 下各占 3。
 */
export interface FlexItemLayout {
  flexGrow: number;
  basisPercent: number;
  wrapBefore: boolean;
}

export interface RowItem<T> {
  item: T;
  span: number;
}

/** 从 style 里取出布局三元组；缺字段按 legado FlexChildStyle 默认值。 */
export function flexLayout(
  style: Record<string, unknown> | null | undefined,
): FlexItemLayout {
  const s = style ?? {};
  const num = (v: unknown, d: number): number => {
    if (typeof v === "boolean") return d;
    if (typeof v === "number") return Number.isFinite(v) ? v : d;
    if (typeof v === "string" && v.trim() !== "") {
      const n = Number(v);
      return Number.isFinite(n) ? n : d;
    }
    return d;
  };
  return {
    flexGrow: num(s.layout_flexGrow, 0),
    basisPercent: num(s.layout_flexBasisPercent, -1),
    wrapBefore: Boolean(s.layout_wrapBefore),
  };
}

function spanOf(layout: FlexItemLayout, maxSpan: number): number {
  if (layout.wrapBefore || layout.basisPercent >= 1) return maxSpan;
  if (layout.basisPercent > 0) {
    return Math.min(maxSpan, Math.max(1, Math.round(maxSpan * layout.basisPercent)));
  }
  return layout.flexGrow > 0 ? 3 : 2;
}

/**
 * 把一维控件列表切成二维「行 × 控件」，并给出每个控件的 span。
 *
 * @param maxSpan 一行的总格数，legado 用 6（见 ExploreScreen 的 totalSpan < 6 补白）。
 */
export function calculateFlexRows<T>(
  items: readonly T[],
  maxSpan: number,
  layout: (item: T) => FlexItemLayout,
): RowItem<T>[][] {
  const rows: RowItem<T>[][] = [];
  let currentRow: RowItem<T>[] = [];
  let currentSpan = 0;

  // 行尾没填满时把剩余格数分掉：全等 span 时均分（多出的逐个 +1），
  // 否则全给最后一个 —— 避免行尾留空。
  const fillCurrentRowTail = (): void => {
    if (currentRow.length === 0) return;
    const remain = maxSpan - currentSpan;
    if (remain <= 0) return;
    const allSameSpan = new Set(currentRow.map((r) => r.span)).size === 1;
    if (allSameSpan && currentRow.length > 1) {
      const addEach = Math.floor(remain / currentRow.length);
      let extra = remain % currentRow.length;
      currentRow = currentRow.map((r) => {
        const add = extra > 0 ? addEach + 1 : addEach;
        if (extra > 0) extra -= 1;
        return { item: r.item, span: r.span + add };
      });
    } else {
      const last = currentRow.length - 1;
      currentRow[last] = {
        item: currentRow[last].item,
        span: currentRow[last].span + remain,
      };
    }
    currentSpan += remain;
  };

  for (const item of items) {
    const style = layout(item);
    const span = spanOf(style, maxSpan);
    if (
      (style.wrapBefore && currentRow.length > 0) ||
      currentSpan + span > maxSpan
    ) {
      fillCurrentRowTail();
      rows.push(currentRow);
      currentRow = [];
      currentSpan = 0;
    }
    currentRow.push({ item, span });
    currentSpan += span;
    if (currentSpan >= maxSpan) {
      rows.push(currentRow);
      currentRow = [];
      currentSpan = 0;
    }
  }
  if (currentRow.length > 0) {
    fillCurrentRowTail();
    rows.push(currentRow);
  }
  return rows;
}

/** 一行的总格数（用于决定是否在行尾补一个空白占位）。 */
export function rowSpan<T>(row: readonly RowItem<T>[]): number {
  return row.reduce((sum, r) => sum + r.span, 0);
}
