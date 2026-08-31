<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from "vue";
import { useRoute, useRouter } from "vue-router";
import {
  MiuixButton,
  MiuixCard,
  MiuixText,
} from "miuix-vue";
import {
  api,
  errMsg,
} from "@/api/client";
import type { Chapter, ShelfEntry, SourceInfo } from "@/api/client";
import BookDetailHero from "@/components/BookDetailHero.vue";
import { openReader } from "@/utils/reader";

const route = useRoute();
const router = useRouter();

// vue-router 已对路径参数做过一次 decode，这里不能再解一次，
// 否则含 %xx 的书源 URL 会被二次解码损坏。
let bookUrl = (route.params.bookUrl as string | undefined) ?? "";
let origin = (route.query.origin as string) ?? "";
// 详情页短链模式：/book/ref/:refId -> 直接读 book_refs 缓存档案
const refId = route.params.refId != null ? Number(route.params.refId) : null;
const qName = (route.query.name as string) ?? "";
const qAuthor = (route.query.author as string) ?? "";
// 搜索结果里已知的封面：书源详情规则缺封面项时兜底展示
const qCover = (route.query.cover as string) ?? "";

const loadingInfo = ref(true);
const loadingToc = ref(false);
const loadError = ref("");
const info = ref<(BookResultShaped) | null>(null);
interface BookResultShaped {
  name: string;
  author?: string;
  kind?: string;
  intro?: string;
  coverUrl?: string;
  lastChapter?: string;
  tocUrl: string;
}
const chapters = ref<Chapter[]>([]);
/** 是否存在分卷标记；没有则目录顶部补一个「卷0」分隔。 */
const hasVols = computed(() => chapters.value.some((c) => !!c.isVolume));
/** 章序号（跳过卷分隔头）：第 i 条里第几个真章节。 */
function realNum(i: number): number {
  let n = 0;
  for (let k = 0; k <= i; k++) if (!chapters.value[k]?.isVolume) n++;
  return n;
}
const tocCached = ref(false);

/* shelf / toc-queue state */
const inShelf = ref<ShelfEntry | null>(null);
let pollTimer: number | null = null;

/* 书源信息（页尾「书源与规则」折叠区展示） */
const sourceInfo = ref<SourceInfo | null>(null);
async function loadSourceInfo() {
  if (!origin || sourceInfo.value) return;
  try {
    sourceInfo.value = await api.sourceInfo(origin);
  } catch {
    /* 书源信息拿不到不阻塞详情展示 */
  }
}

/* 阅读进度（不依赖是否在书架） */
const progressInfo = ref<{
  chapterIndex: number;
  chapterTitle: string;
  updatedAt: string | null;
} | null>(null);
async function loadProgress() {
  if (!bookUrl) return;
  try {
    const r = await api.progressGet(bookUrl);
    progressInfo.value = r.progress;
  } catch {
    /* 无进度 */
  }
}
const activeProgress = computed(
  () => inShelf.value?.progress ?? progressInfo.value ?? null,
);

function stringify(v: unknown): string {
  try {
    return JSON.stringify(v, null, 2);
  } catch {
    return String(v);
  }
}

const displayCover = computed(() => info.value?.coverUrl || qCover || "");
const tocStatus = computed(() => inShelf.value?.toc ?? null);

async function loadInfo() {
  loadingInfo.value = true;
  loadError.value = "";
  try {
    void loadSourceInfo();
    const r = await api.bookInfo(origin, bookUrl, qName, qAuthor, qCover);
    // 后端已做合并：规则取不到的字段保留搜索阶段已知值
    info.value = {
      ...r,
      name: r.name || qName,
      author: r.author || qAuthor,
      coverUrl: r.coverUrl || qCover || "",
    };
    document.title = `${info.value.name} · Viewer`;
    void autoRefreshToc();
  } catch (e) {
    // 已有缓存档案时静默保留缓存内容，不弹错误面板
    if (!info.value) loadError.value = errMsg(e);
  } finally {
    loadingInfo.value = false;
  }
}

/**
 * 先用本地缓存档案即时渲染（书名/封面/简介等不等待书源请求），再后台刷新。
 * 短链 /book/ref/:id 直接读 book_refs 档案；长链按来源+地址查档案。
 */
onMounted(async () => {
  try {
    if (refId != null) {
      const r = await api.bookRef(refId);
      bookUrl = r.bookUrl;
      origin = r.sourceUrl;
      info.value = {
        name: r.name || qName,
        author: r.author || qAuthor,
        kind: r.kind || "",
        intro: r.intro || "",
        coverUrl: r.coverUrl || qCover || "",
        lastChapter: r.lastChapter || "",
        tocUrl: r.tocUrl || "",
      };
      document.title = `${info.value.name} · Viewer`;
    } else if (origin && bookUrl) {
      const p = await api.bookProfile(origin, bookUrl);
      if (p.found) {
        info.value = {
          name: p.name || qName,
          author: p.author || qAuthor,
          kind: p.kind || "",
          intro: p.intro || "",
          coverUrl: p.coverUrl || qCover || "",
          lastChapter: p.lastChapter || "",
          tocUrl: p.tocUrl || "",
        };
        document.title = `${info.value.name} · Viewer`;
      }
    }
  } catch { /* 查不到档案就走现场请求 */ }
  void loadInfo();
  void loadProgress();
});

/** 缓存档案或书源详情就绪后拉取目录（懒加载，失败不打断详情）。 */
watch(info, () => void loadToc());

onMounted(async () => {
  try {
    const shelf = await api.shelf();
    inShelf.value =
      shelf.items.find((x) => x.bookUrl === bookUrl) ?? null;
  } catch {
    /* shelf permission may be missing */
  }
});

onBeforeUnmount(() => {
  disposed = true;
  if (pollTimer !== null) window.clearInterval(pollTimer);
});

function refreshToc() {
  if (!inShelf.value) return;
  api.shelfRefreshToc(inShelf.value.id)
    .then(() => startPolling())
    .catch((e) => alert(errMsg(e)));
}

/** 目录在后台队列抓取：轮询书架状态直到完成或失败。 */
function startPolling() {
  if (pollTimer !== null) return;
  pollTimer = window.setInterval(async () => {
    try {
      const s = await api.shelf();
      const entry = s.items.find((x) => x.bookUrl === bookUrl) ?? null;
      inShelf.value = entry;
      const st = entry?.toc?.status;
      if (st === "done" || st === "error" || st === undefined) {
        stopPolling();
        if (st === "done") await loadToc(true);
      }
    } catch {
      stopPolling();
    }
  }, 2000);
}

function stopPolling() {
  if (pollTimer !== null) {
    window.clearInterval(pollTimer);
    pollTimer = null;
  }
}

async function loadToc(force = false) {
  if (!info.value || loadingToc.value) return;
  if (chapters.value.length && !force) return;
  loadingToc.value = true;
  try {
    const r = await api.chaptersCached(origin, bookUrl);
    chapters.value = r.chapters;
    tocCached.value = r.cached;
    void autoRefreshToc();
  } catch (e) {
    alert(errMsg(e));
  } finally {
    loadingToc.value = false;
  }
}

/* ---- 目录缓存落后于书源最新章节时的自动补偿 ----
 * 书源详情是实时抓的，目录却优先读本地缓存，两者并存会出现
 * 「最新 第454章 · 共 415 章」这类矛盾。检测到落后就排队一次后台重抓
 * （不限是否加入书架），轮询到完成后强制重载目录；期间在章节数旁标注「缓存」。 */
const tocStale = ref(false);
const tocAutoRefreshing = ref(false);
let tocAutoAttempts = 0;
let disposed = false;

function latestChapterNo(title?: string): number | null {
  const m = /第\s*(\d+)\s*[章节回話節]/.exec(title ?? "");
  return m ? Number(m[1]) : null;
}

function tocLagsSource(): boolean {
  const latest = latestChapterNo(info.value?.lastChapter);
  return latest != null
    && chapters.value.length > 0
    && chapters.value.length < latest;
}

async function autoRefreshToc() {
  if (disposed || !info.value || !origin || !bookUrl) return;
  if (tocAutoRefreshing.value) return;
  tocStale.value = tocLagsSource();
  if (!tocStale.value) return;
  tocAutoRefreshing.value = true;
  try {
    // 章节号可能存在卷号混编等特殊情况，自动重试最多两次
    while (tocLagsSource() && tocAutoAttempts < 2 && !disposed) {
      tocAutoAttempts++;
      try {
        await api.chaptersRefresh(origin, bookUrl);
      } catch {
        break; // 排队失败：保留缓存目录与「缓存」标注
      }
      // 轮询抓取状态直到完成/失败（3s × 20 次 ≈ 上限一分钟）
      for (let i = 0; i < 20 && !disposed; i++) {
        await new Promise((r) => setTimeout(r, 3000));
        if (disposed) return;
        let st = "";
        try {
          st = (await api.tocStatus(origin, bookUrl)).status;
        } catch {
          break;
        }
        if (st === "done" || st === "error" || st === "none") break;
      }
      if (disposed) return;
      await loadToc(true);
    }
  } finally {
    tocAutoRefreshing.value = false;
    tocStale.value = tocLagsSource();
  }
}

const tocCountNote = computed(() => {
  if (!tocStale.value) return "";
  return tocAutoRefreshing.value ? "缓存 · 更新中…" : "缓存";
});

/* ------------------------------------------------------------- reading */
function readFrom(index: number | null) {
  void openReader(router, {
    sourceUrl: origin,
    bookUrl,
    name: info.value?.name ?? qName,
    author: info.value?.author ?? qAuthor,
    coverUrl: displayCover.value,
    intro: info.value?.intro ?? "",
    kind: info.value?.kind ?? "",
    lastChapter: info.value?.lastChapter ?? "",
    tocUrl: info.value?.tocUrl ?? "",
  }).then(() => {
    if (index !== null && index >= 0) {
      // 通过 sessionStorage 把起始章带给阅读器（短链 URL 不再携带）
      sessionStorage.setItem(`reader_start_${bookUrl}`, String(index));
    }
  });
}

const resumeIndex = computed(() => activeProgress.value?.chapterIndex ?? -1);
const hasProgress = computed(
  () => activeProgress.value != null && resumeIndex.value >= 0,
);

/* ------------------------------------------------------------ shelf ops */
async function addToShelf() {
  if (!info.value) return;
  try {
    await api.shelfAdd({
      bookUrl,
      tocUrl: info.value.tocUrl || bookUrl,
      name: info.value.name,
      author: info.value.author ?? "",
      coverUrl: displayCover.value,
      intro: info.value.intro ?? "",
      lastChapter: info.value.lastChapter ?? "",
      sourceUrl: origin,
    });
    const shelf = await api.shelf();
    inShelf.value =
      shelf.items.find((x) => x.bookUrl === bookUrl) ?? null;
  } catch (e) {
    alert(errMsg(e));
  }
}

async function removeFromShelf() {
  if (!inShelf.value) return;
  try {
    await api.shelfRemove(inShelf.value.id);
    inShelf.value = null;
  } catch (e) {
    alert(errMsg(e));
  }
}

/* ------------------------------------------------------------ local lib */
let libDownloading = false;
async function downloadToLibrary() {
  if (!info.value) return;
  if (libDownloading) return;
  libDownloading = true;
  try {
    await api.libraryDownload({
      sourceUrl: origin,
      bookUrl,
      name: info.value.name,
      author: info.value.author ?? "",
      cover: displayCover.value,
    });
    alert("已开始下载到本地书库，可在「本地库」页查看进度。");
  } catch (e) {
    alert(errMsg(e));
  } finally {
    libDownloading = false;
  }
}
</script>

<template>
  <div>
    <button class="back" @click="router.back()">
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"
           stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
        <path d="m14.5 5.5-6.5 6.5 6.5 6.5"/>
      </svg>
      返回
    </button>

    <!-- 详情加载中：骨架屏（封面 + 标题 + 章节面板占位，微光扫过），
         不再是悬空的小转圜 —— 版面先成形，读起来更像“还在载入”而非“空白”。 -->
    <div v-if="loadingInfo" class="skel" aria-hidden="true">
      <div class="skel-hero">
        <div class="skel-top">
          <div class="skel-cover"></div>
          <div class="skel-side">
            <div class="skel-line skel-tit"></div>
            <div class="skel-line skel-by"></div>
            <div class="skel-line skel-chips"></div>
          </div>
        </div>
        <div class="skel-intro">
          <div class="skel-line"></div>
          <div class="skel-line skel-third"></div>
        </div>
      </div>
      <div class="skel-panel">
        <div class="skel-line skel-head"></div>
        <div class="skel-row"></div>
        <div class="skel-row"></div>
        <div class="skel-row skel-short"></div>
      </div>
    </div>

    <!-- 详情加载失败：可见错误态 + 重试（书源下线/网络不通时不再白屏） -->
    <MiuixCard v-else-if="loadError" class="error-panel" :show-indication="false">
      <MiuixText type="title3">无法打开本书</MiuixText>
      <p class="err-msg">{{ loadError }}</p>
      <p class="err-tip">
        书源（{{ origin || "未知来源" }}）可能已失效或当前网络无法访问，可返回重新搜索换源。
      </p>
      <div class="ops">
        <MiuixButton type="primary" @click="loadInfo">重试</MiuixButton>
        <MiuixButton @click="router.back()">返回</MiuixButton>
      </div>
    </MiuixCard>

    <div v-else-if="info" class="detail-wrap">
      <!-- 统一详情头部：与阅读器内「书籍信息」弹层共用同一组件 -->
      <BookDetailHero
        class="page-hero"
        :origin="origin"
        :book-url="bookUrl"
        :name="info.name"
        :author="info.author"
        :kind="info.kind"
        :intro="info.intro"
        :cover-url="displayCover"
        :last-chapter="info.lastChapter"
        :chapter-count="chapters.length || null"
        :chapter-count-note="tocCountNote"
      />

      <!-- 操作排 -->
      <div class="ops">
        <MiuixButton type="primary" @click="readFrom(hasProgress ? resumeIndex : 0)">
          {{ hasProgress ? "继续阅读" : "开始阅读" }}
        </MiuixButton>
        <MiuixButton v-if="!inShelf" @click="addToShelf">加入书架</MiuixButton>
        <MiuixButton v-else @click="removeFromShelf">移出书架</MiuixButton>
        <MiuixButton @click="downloadToLibrary">下载到本地库</MiuixButton>
      </div>
      <div v-if="hasProgress" class="resume-hint">
        上次读到：{{ activeProgress?.chapterTitle }}
      </div>

      <!-- 目录 -->
      <MiuixCard class="panel" :show-indication="false">
        <div class="toc-bar">
          <span class="toc-meta">
            共 {{ chapters.length }} 章
            <span v-if="tocCached">（缓存）</span>
            <template v-if="tocStatus && tocStatus.status !== 'none'">
              ·
              <span v-if="tocStatus.status === 'queued'" class="st-wait">排队抓取中…</span>
              <span v-else-if="tocStatus.status === 'running'" class="st-wait">抓取中…</span>
              <span v-else-if="tocStatus.status === 'error'" class="st-err"
                    :title="tocStatus.error">抓取失败</span>
            </template>
          </span>
          <MiuixButton
            v-if="inShelf"
            size="small"
            :disabled="tocStatus?.status === 'queued' || tocStatus?.status === 'running'"
            @click="refreshToc"
          >刷新目录</MiuixButton>
        </div>
        <div v-if="loadingToc" class="skel-rows" aria-hidden="true">
          <div class="skel-row"></div>
          <div class="skel-row"></div>
          <div class="skel-row skel-short"></div>
        </div>
        <ol v-else class="toc-list">
          <li v-if="!hasVols" class="vol-sep">卷0</li>
          <template v-for="(c, i) in chapters" :key="c.url + i">
            <li v-if="c.isVolume" class="vol-sep">{{ c.title }}</li>
            <li v-else>
              <a @click.prevent="readFrom(i)">
                <span class="num">{{ realNum(i) }}</span>
                <span class="ttl">{{ c.title || `第${realNum(i)}章` }}</span>
              </a>
            </li>
          </template>
        </ol>
        <div v-if="!loadingToc && !chapters.length" class="center empty">
          <template v-if="tocStatus?.status === 'queued' || tocStatus?.status === 'running'">
            目录正在后台抓取，稍等片刻会自动出现…
          </template>
          <template v-else>
            没有解析到章节（目录页可能需要登录或规则不匹配）。
          </template>
        </div>
      </MiuixCard>

      <!-- 书源与规则：折叠收纳，不再占用独立标签页 -->
      <details class="src-card">
        <summary>书源与规则</summary>
        <dl class="detail-grid">
          <div class="d-row">
            <dt>书源</dt>
            <dd>{{ sourceInfo ? `${sourceInfo.name}（${sourceInfo.type}）` : origin || "—" }}</dd>
          </div>
          <div class="d-row">
            <dt>源地址</dt>
            <dd class="mono">{{ origin || "—" }}</dd>
          </div>
        </dl>
        <pre class="rules-json">{{ sourceInfo ? stringify(sourceInfo.rules) : "加载中…" }}</pre>
      </details>
    </div>
  </div>
</template>

<style scoped>
.back {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  border: 0;
  background: none;
  color: var(--m-color-on-surface-secondary);
  cursor: pointer;
  font-size: 13.5px;
  margin-bottom: 14px;
  padding: 6px 12px 6px 6px;
  border-radius: 999px;
  font-family: inherit;
}
.back:hover {
  background: var(--m-color-surface-container-high);
  color: var(--m-color-on-surface);
}
.back svg {
  width: 17px;
  height: 17px;
}

.page-hero {
  margin-bottom: 16px;
}
.ops {
  display: flex;
  flex-wrap: wrap;
  gap: 10px;
}
.resume-hint {
  margin-top: 10px;
  font-size: 12px;
  color: var(--m-color-primary);
}

.center {
  display: grid;
  place-items: center;
  padding: 40px 0;
}

/* ---- 详情加载骨架屏 ----
 * 版面与真实内容大致同形：封面 + 标题区 + 简介 + 章节面板。
 * 微光扫过只动 transform（合成器动画），主线程忙也不卡；
 * 减弱动态偏好下退为静态色块（略去扫光，仍不阻碍理解）。 */
.skel {
  display: grid;
  gap: 16px;
}
.skel-hero,
.skel-panel {
  position: relative;
  overflow: hidden;
  border-radius: var(--app-radius-card, 20px);
  background: var(--m-color-surface-container);
}
.skel-hero {
  padding: 26px 30px 22px;
}
.skel-panel {
  padding: 18px;
}
.skel-top {
  display: flex;
  align-items: flex-end;
  gap: 18px;
}
.skel-cover {
  width: 118px;
  aspect-ratio: 27 / 38;
  border-radius: 12px;
  background: var(--m-color-surface-container-high);
  flex: none;
}
.skel-side {
  flex: 1;
  min-width: 0;
  display: grid;
  gap: 10px;
}
.skel-intro {
  display: grid;
  gap: 10px;
  margin-top: 20px;
}
.skel-line,
.skel-row {
  height: 13px;
  border-radius: 6px;
  background: var(--m-color-surface-container-high);
}
.skel-tit {
  height: 28px;
  width: 72%;
}
.skel-by {
  width: 40%;
}
.skel-chips {
  height: 22px;
  width: 55%;
  border-radius: 999px;
}
.skel-third {
  width: 55%;
}
.skel-head {
  height: 16px;
  width: 34%;
  margin-bottom: 16px;
}
.skel-row {
  height: 12px;
  margin-bottom: 12px;
}
@media (max-width: 720px) {
  .skel-top {
    flex-direction: column;
    align-items: center;
    text-align: center;
  }
  .skel-side,
  .skel-intro {
    max-width: 88%;
  }
}
@media (prefers-reduced-motion: no-preference) {
  .skel-hero::after,
  .skel-panel::after {
    content: "";
    position: absolute;
    inset: 0;
    transform: translateX(-100%);
    background: linear-gradient(
      100deg,
      transparent 25%,
      color-mix(in srgb, var(--m-color-on-surface) 8%, transparent) 50%,
      transparent 75%
    );
    animation: skel-sweep 1.4s linear infinite;
  }
}
@keyframes skel-sweep {
  to { transform: translateX(100%); }
}
@media (prefers-reduced-motion: reduce) {
  .skel-hero::after,
  .skel-panel::after {
    display: none;
  }
}

/* 详情正文入场：骨架屏 → 内容轻浮入（不再硬切换） */
.detail-wrap {
  animation: det-in 0.4s var(--app-ease-calm, cubic-bezier(0.22, 0.61, 0.36, 1)) both;
}
@keyframes det-in {
  from {
    opacity: 0;
    transform: translateY(8px);
  }
  to {
    opacity: 1;
    transform: none;
  }
}
@media (prefers-reduced-motion: reduce) {
  .detail-wrap {
    animation: none;
  }
}
.panel {
  margin-top: 16px;
}
.error-panel {
  max-width: 560px;
  --app-card-pad: 24px;
}
.err-msg {
  color: var(--m-color-error);
  font-size: 13px;
  margin: 10px 0 4px;
}
.err-tip {
  color: var(--m-color-on-background-variant);
  font-size: 13px;
  line-height: 1.6;
  margin: 0 0 14px;
}

/* ---- 目录 ---- */
.toc-bar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  margin-bottom: 10px;
}
.toc-meta {
  font-size: 12px;
  color: var(--m-color-on-background-variant);
}
.st-wait {
  color: var(--m-color-primary);
}
.st-err {
  color: var(--m-color-error);
  cursor: help;
}
.toc-list {
  columns: 2;
  column-gap: 30px;
  list-style: none; /* 自动编号与块级链接会拆成两行，改用行内序号 */
  padding-left: 4px;
  margin: 0;
  max-height: 460px;
  overflow-y: auto;
}
.toc-list a {
  cursor: pointer;
  display: flex;
  align-items: baseline;
  gap: 8px;
  padding: 5px 8px;
  border-radius: 8px;
  font-size: 13px;
  color: var(--m-color-on-surface);
}
.toc-list .num {
  flex: none;
  min-width: 2.2em;
  text-align: right;
  font-variant-numeric: tabular-nums;
  font-size: 11px;
  color: var(--m-color-on-background-variant);
}
.toc-list .ttl {
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.toc-list a:hover {
  background: var(--m-color-surface-container-high);
}
.toc-list .vol-sep {
  list-style: none;
  margin: 10px 0 4px;
  padding: 6px 8px;
  font-size: 12px;
  font-weight: 600;
  color: var(--m-color-on-background-variant);
  border-top: 1px solid var(--m-color-outline-variant, rgba(128, 128, 128, 0.35));
  text-align: left;
  grid-column: 1 / -1;
}
@media (max-width: 720px) {
  .toc-list {
    columns: 1;
  }
}
.empty {
  color: var(--m-color-on-background-variant);
  font-size: 13px;
}

/* ---- 书源与规则折叠区 ---- */
.src-card {
  margin-top: 16px;
  padding: 2px 20px 16px;
  border-radius: var(--app-radius-card, 20px);
  background: var(--m-color-surface-container);
}
.src-card summary {
  cursor: pointer;
  padding: 12px 0;
  font-size: 13.5px;
  font-weight: 600;
  color: var(--m-color-on-surface-secondary);
  user-select: none;
}
.src-card[open] summary {
  color: var(--m-color-on-surface);
}
.detail-grid {
  margin: 0;
}
.d-row {
  display: grid;
  grid-template-columns: 84px 1fr;
  gap: 12px;
  padding: 7px 0;
  border-bottom: 1px solid var(--m-color-divider-line);
  font-size: 13px;
}
.d-row:last-of-type {
  border-bottom: 0;
}
.d-row dt {
  color: var(--m-color-on-background-variant);
  flex: none;
}
.d-row dd {
  margin: 0;
  color: var(--m-color-on-surface);
  min-width: 0;
  word-break: break-word;
}
.mono {
  font-family: ui-monospace, "Cascadia Code", Consolas, monospace;
  font-size: 12px;
  overflow-wrap: anywhere;
}
.rules-json {
  margin: 12px 0 0;
  padding: 12px;
  border-radius: 12px;
  background: var(--m-color-surface-container-high);
  color: var(--m-color-on-surface-variant);
  font-family: ui-monospace, "Cascadia Code", Consolas, monospace;
  font-size: 11px;
  line-height: 1.5;
  max-height: 320px;
  overflow: auto;
  white-space: pre-wrap;
  word-break: break-word;
}
</style>
