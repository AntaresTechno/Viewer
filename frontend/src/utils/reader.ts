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

/** 长参数详情路由：仅作 resolve 失败时的兜底。 */
function detailRoute(t: ReaderTarget): string {
  const q = new URLSearchParams({
    origin: t.sourceUrl,
    name: t.name,
    author: t.author ?? "",
    cover: t.coverUrl ?? "",
  });
  return `/book/${encodeURIComponent(t.bookUrl)}?${q.toString()}`;
}

/** /books/resolve 的入参：把已知信息写入书籍短链档案（get-or-create）。 */
function resolvePayload(t: ReaderTarget) {
  return {
    sourceUrl: t.sourceUrl,
    bookUrl: t.bookUrl,
    name: t.name,
    author: t.author ?? "",
    coverUrl: t.coverUrl ?? "",
    intro: t.intro ?? "",
    kind: t.kind ?? "",
    lastChapter: t.lastChapter ?? "",
    tocUrl: t.tocUrl ?? "",
  };
}

/** 详情页统一入口：解析书籍档案后跳短链 /book/ref/:id。
 * 书架/搜索/发现/本地库/阅读器全部经此进入，URL 只保留短链一种形态。 */
export async function openDetail(router: Router, t: ReaderTarget): Promise<void> {
  try {
    const ref = await api.resolveBook(resolvePayload(t));
    await router.push(`/book/ref/${ref.id}`);
  } catch {
    // 后端不可达时退回长参数方式，详情页仍会用 bookProfile 缓存兜底
    await router.push(detailRoute(t));
  }
}

export async function openReader(router: Router, t: ReaderTarget): Promise<void> {
  try {
    const ref = await api.resolveBook(resolvePayload(t));
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
