/** 解析书源的 sourceGroup 字段为分组名列表。
 *
 * legado 书源的 sourceGroup 是自由文本，常见分隔符：半角逗号、全角
 * 逗号、分号（中英）、顿号、换行。空白分隔不拆（分组名可含空格）。
 */
export function splitGroups(raw?: string | null): string[] {
  if (!raw) return [];
  return raw
    .split(/[,，;；、\n\r]+/)
    .map((s) => s.trim())
    .filter(Boolean);
}

export interface GroupStat {
  name: string;
  count: number;
}

/** 统计书源列表出现的所有分组，按数量降序、同名按拼音序。 */
export function collectGroups(
  items: { sourceGroup?: string | null }[],
): GroupStat[] {
  const counts = new Map<string, number>();
  for (const s of items)
    for (const g of splitGroups(s.sourceGroup))
      counts.set(g, (counts.get(g) ?? 0) + 1);
  return [...counts.entries()]
    .sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0], "zh"))
    .map(([name, count]) => ({ name, count }));
}
