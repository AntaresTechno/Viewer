/** 阅读器入口：解析短 id 后跳转 /reader?id=N，避免超长 URL。 */
import type { Router } from "vue-router";
import { api } from "@/api/client";

export interface ReaderTarget {
  sourceUrl: string;
  bookUrl: string;
  name: string;
  author?: string;
  coverUrl?: string;
  /** 已知的简介/分类/最新章节/目录页：随 resolve 写入本地缓存档案 */
  intro?: string;
  kind?: string;
  lastChapter?: string;
  tocUrl?: string;
}

export function detailRoute(t: ReaderTarget): string {
  const q = new URLSearchParams({
    origin: t.sourceUrl,
    name: t.name,
    author: t.author ?? "",
    cover: t.coverUrl ?? "",
  });
  return `/book/${encodeURIComponent(t.bookUrl)}?${q.toString()}`;
}

export async function openReader(router: Router, t: ReaderTarget): Promise<void> {
  try {
    const ref = await api.resolveBook({
      sourceUrl: t.sourceUrl,
      bookUrl: t.bookUrl,
      name: t.name,
      author: t.author ?? "",
      coverUrl: t.coverUrl ?? "",
      intro: t.intro ?? "",
      kind: t.kind ?? "",
      lastChapter: t.lastChapter ?? "",
      tocUrl: t.tocUrl ?? "",
    });
    await router.push(`/reader?id=${ref.id}`);
  } catch {
    // 解析失败退回长参数方式，阅读器页自己也会兜底重试
    const q = new URLSearchParams({
      origin: t.sourceUrl,
      name: t.name,
      author: t.author ?? "",
      cover: t.coverUrl ?? "",
    });
    await router.push(`/reader?book=${encodeURIComponent(t.bookUrl)}&${q.toString()}`);
  }
}
