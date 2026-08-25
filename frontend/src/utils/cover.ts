import { coverProxyUrl } from "@/api/client";

/** 中性灰占位封面（miuix / md3e 两种设计下都不突兀）。 */
export const FALLBACK_COVER_SVG =
  "data:image/svg+xml;utf8,<svg xmlns='http://www.w3.org/2000/svg' width='90' height='120'><rect width='100%25' height='100%25' rx='8' fill='%239aa7b8'/></svg>";

/**
 * 封面加载失败处理：先经代理重试一次；再失败就定格占位图。
 * 否则 img 的 @error 反复把 src 设成同一个代理地址会造成请求风暴。
 */
export function onCoverError(
  evt: Event,
  originUrl: string | undefined | null,
): void {
  const el = evt.target as HTMLImageElement;
  if (el.dataset.coverFb) {
    el.onerror = null;
    el.src = FALLBACK_COVER_SVG;
    return;
  }
  el.dataset.coverFb = "1";
  el.src = originUrl ? coverProxyUrl(originUrl) : FALLBACK_COVER_SVG;
}
