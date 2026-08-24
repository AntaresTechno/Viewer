<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref } from "vue";
import { useRoute, useRouter } from "vue-router";
import { api, errMsg, coverProxyUrl } from "@/api/client";
import type { Chapter } from "@/api/client";
import { detailRoute } from "@/utils/reader";
import { FALLBACK_COVER_SVG, onCoverError } from "@/utils/cover";

const route = useRoute();
const router = useRouter();

const qId = route.query.id != null ? Number(route.query.id) : null;
/** 书籍缓存档案 id（详情页短链 /book/ref/:id 用）。 */
let refId: number | null = qId;
let bookUrl = route.query.book as string ?? "";
let origin = route.query.origin as string ?? "";
const bookName = ref(route.query.name as string ?? "未命名");
const author = ref(route.query.author ?? "");
const startIndex = route.query.index != null ? Number(route.query.index) : null;

/** 每次打开章节后自动预取的后续章节数 */
const PREFETCH_COUNT = 5;

/* ------------------------------------------------------------- state */
/** 书籍本地缓存档案：打开阅读器全程零书源请求即可展示的基本信息。 */
const profile = ref({
  coverUrl: route.query.cover as string ?? "",
  intro: "",
  kind: "",
  lastChapter: "",
  tocUrl: "",
});

const chapters = ref<Chapter[]>([]);
const current = ref(-1);
const paragraphs = ref<string[]>([]);
const loadingToc = ref(true);
const loadingContent = ref(false);
const tocError = ref("");
const contentError = ref("");
/** 目录缓存未命中、已排队后台抓取（轮询中）。 */
const tocQueued = ref(false);
let tocPollTimer: number | null = null;

type Mode = "scroll" | "paged";
const mode = ref<Mode>((localStorage.getItem("reader_mode") as Mode) || "scroll");
const fontSize = ref(Number(localStorage.getItem("reader_font") ?? 17));

const showChapters = ref(false);
const showSettings = ref(false);
/** 书籍信息弹层（封面/作者/分类/最新章节/简介，全部来自缓存档案） */
const showInfo = ref(false);
const introLoading = ref(false);

/* paging */
const pageIndex = ref(0);
const pageCount = ref(1);
const pagedViewport = ref<HTMLElement | null>(null);
const pagedContent = ref<HTMLElement | null>(null);
/** 滑动模式的滚动容器（页面本体不再滚动，footer 恒贴底） */
const bodyEl = ref<HTMLElement | null>(null);

let saveTimer: number | null = null;

document.title = `${bookName.value} · 阅读`;

function persistPrefs() {
  localStorage.setItem("reader_mode", mode.value);
  localStorage.setItem("reader_font", String(fontSize.value));
}

/* --------------------------------------------------------- data load */
/** 短 id 入口：/reader?id=N；长参数入口会解析成短 id 并替换地址栏。 */
async function bootstrapBook() {
  if (qId != null && Number.isFinite(qId)) {
    const r = await api.bookRef(qId);
    bookUrl = r.bookUrl;
    origin = r.sourceUrl;
    bookName.value = r.name || "未命名";
    author.value = r.author ?? "";
    profile.value = {
      coverUrl: r.coverUrl ?? "",
      intro: r.intro ?? "",
      kind: r.kind ?? "",
      lastChapter: r.lastChapter ?? "",
      tocUrl: r.tocUrl ?? "",
    };
    return;
  }
  if (!bookUrl) {
    throw new Error("缺少书籍参数（?id= 或 ?book=&origin=）");
  }
  try {
    const r = await api.resolveBook({
      sourceUrl: origin,
      bookUrl,
      name: bookName.value,
      author: author.value,
      coverUrl: route.query.cover as string ?? "",
    });
    window.history.replaceState(null, "", `/reader?id=${r.id}`);
    refId = r.id;
    profile.value = {
      coverUrl: r.coverUrl ?? "",
      intro: r.intro ?? "",
      kind: r.kind ?? "",
      lastChapter: r.lastChapter ?? "",
      tocUrl: r.tocUrl ?? "",
    };
  } catch {
    /* 解析失败不阻塞阅读，保留长 URL */
  }
}

function stopTocPoll() {
  if (tocPollTimer !== null) {
    window.clearInterval(tocPollTimer);
    tocPollTimer = null;
  }
}

/**
 * 目录缓存优先：命中直接返回；未命中排队一次后台抓取并轮询，
 * 不再在打开阅读器的请求里现场抓整本目录。
 * @returns true=目录就绪；false=失败（tocError 已写）
 */
function loadChaptersCacheFirst(): Promise<boolean> {
  return new Promise((resolve) => {
    let queuedOnce = false;
    const finish = (ok: boolean) => {
      stopTocPoll();
      resolve(ok);
    };
    const attempt = async (): Promise<boolean> => {
      try {
        const r = await api.chaptersCached(origin, bookUrl, false);
        if (r.chapters.length) {
          chapters.value = r.chapters;
          tocQueued.value = false;
          return true;
        }
      } catch (e) {
        // 首次请求失败才算致命；轮询中的瞬时错误继续重试
        if (!queuedOnce) throw e;
        return false;
      }
      if (!queuedOnce) {
        queuedOnce = true;
        tocQueued.value = true;
        void api.chaptersRefresh(origin, bookUrl).catch(() => {});
        return false;
      }
      try {
        const st = await api.tocStatus(origin, bookUrl);
        if (st.status === "error") {
          tocError.value = `目录抓取失败：${st.error || "未知错误"}`;
          tocQueued.value = false;
          return false;
        }
      } catch { /* 状态查询失败不中断轮询 */ }
      return false;
    };
    const step = () => {
      attempt()
        .then((done) => {
          if (done || tocError.value) return finish(done);
          if (tocPollTimer === null) tocPollTimer = window.setInterval(step, 2000);
        })
        .catch((e) => {
          tocError.value = errMsg(e);
          finish(false);
        });
    };
    step();
  });
}

/** 目录就绪后：定位起始章并打开。 */
async function startReading() {
  let idx = 0;
  const ssKey = `reader_start_${bookUrl}`;
  const ssStart = Number(sessionStorage.getItem(ssKey));
  if (startIndex != null && Number.isFinite(startIndex)) {
    idx = Math.min(Math.max(0, startIndex), chapters.value.length - 1);
  } else if (Number.isFinite(ssStart) && sessionStorage.getItem(ssKey) !== null) {
    idx = Math.min(Math.max(0, ssStart), chapters.value.length - 1);
    sessionStorage.removeItem(ssKey); // 一次性使用
  } else {
    try {
      const p = await api.progressGet(bookUrl);
      if (p.progress) idx = Math.min(p.progress.chapterIndex, chapters.value.length - 1);
    } catch { /* 无进度则从头开始 */ }
  }
  await openChapter(idx);
}

onMounted(async () => {
  try {
    await bootstrapBook();
    document.title = `${bookName.value} · 阅读`;
    const ready = await loadChaptersCacheFirst();
    if (!ready) return;
    await startReading();
  } catch (e) {
    tocError.value = errMsg(e);
  } finally {
    loadingToc.value = false;
  }
});

onBeforeUnmount(stopTocPoll);

/** 简介缺失时按需从书源补一次详情，并写回本地缓存档案。 */
async function fetchIntro() {
  if (introLoading.value) return;
  introLoading.value = true;
  try {
    const info = await api.bookInfo(
      origin, bookUrl, bookName.value, author.value, profile.value.coverUrl,
    );
    profile.value.intro = (info.intro ?? "").trim();
    profile.value.kind = info.kind?.trim() || profile.value.kind;
    profile.value.lastChapter = info.lastChapter?.trim() || profile.value.lastChapter;
    profile.value.tocUrl = info.tocUrl?.trim() || profile.value.tocUrl;
    if (info.coverUrl) profile.value.coverUrl = info.coverUrl;
    // 持久化到书籍档案，下次直接缓存展示
    await api.resolveBook({
      sourceUrl: origin, bookUrl,
      name: bookName.value, author: author.value,
      coverUrl: profile.value.coverUrl,
      intro: profile.value.intro, kind: profile.value.kind,
      lastChapter: profile.value.lastChapter, tocUrl: profile.value.tocUrl,
    });
  } catch (e) {
    alert(`简介获取失败：${errMsg(e)}`);
  } finally {
    introLoading.value = false;
  }
}

/** 正文插图统一走后端缓存代理：防盗链 + 磁盘缓存防失效。 */
function proxyImages(paragraph: string): string {
  return paragraph.replace(/src="([^"]+)"/g, (_m, u: string) => {
    if (u.startsWith("data:")) return `src="${u}"`;
    return `src="${coverProxyUrl(u)}"`;
  });
}

async function openChapter(idx: number, restoreOffset = 0) {
  if (!chapters.value[idx] || loadingContent.value) return;
  current.value = idx;
  pageIndex.value = 0;
  contentError.value = "";
  loadingContent.value = true;
  paragraphs.value = [];
  try {
    const ch = chapters.value[idx];
    const r = await api.content(
      origin, ch.url, ch.baseUrl, ch.title ?? "", bookName.value, bookUrl,
      chapters.value[idx + 1]?.url ?? "",
    );
    paragraphs.value = r.content
      .split(/\n+/)
      .map((p) => p.trim())
      .filter(Boolean);
    void api.saveProgress(bookUrl, idx, ch.title ?? "", 0);
    prefetchUpcoming(idx);
    await nextTick();
    if (mode.value === "scroll") {
      bodyEl.value?.scrollTo({ top: restoreOffset });
    } else {
      await measureViewport();
      recountPages();
      goToPage(Math.min(restoreOffset, pageCount.value - 1));
    }
  } catch (e) {
    contentError.value = errMsg(e);
  } finally {
    loadingContent.value = false;
  }
}

/** 预取当前章之后的 N 章（后端缓存，默认 5 章），失败静默。 */
function prefetchUpcoming(idx: number) {
  const upcoming = chapters.value
    .slice(idx + 1, idx + 1 + PREFETCH_COUNT)
    .map((c) => ({ url: c.url, title: c.title ?? "", base: c.baseUrl, isVolume: c.isVolume }));
  if (upcoming.length) {
    void api.prefetchContent(origin, upcoming).catch(() => {});
  }
}

async function nextChapter() {
  if (current.value < chapters.value.length - 1) {
    await openChapter(current.value + 1);
  }
}

async function prevChapter() {
  if (current.value > 0) await openChapter(current.value - 1);
}

/* -------------------------------------------------------- pagination */
const GAP = 48; // 与 .paged-content column-gap 保持一致
const vpWidth = ref(0);

/** 测量可视宽度并写入 column-width（CSS 该属性不接受百分比，必须用 px）。 */
async function measureViewport() {
  const vp = pagedViewport.value;
  if (!vp) return;
  const w = Math.max(200, vp.clientWidth);
  if (w !== vpWidth.value) {
    vpWidth.value = w;
    // 等样式生效后再让调用方重算页数
    await nextTick();
  }
}

function recountPages() {
  const vp = pagedViewport.value;
  const ct = pagedContent.value;
  if (!vp || !ct || mode.value !== "paged") return;
  const w = vpWidth.value || vp.clientWidth;
  const total = ct.scrollWidth + GAP;
  pageCount.value = Math.max(1, Math.round(total / (w + GAP)));
  if (pageIndex.value > pageCount.value - 1) {
    pageIndex.value = pageCount.value - 1;
    applyPageTransform();
  }
}

function applyPageTransform() {
  const vp = pagedViewport.value;
  const ct = pagedContent.value;
  if (!vp || !ct) return;
  const w = vpWidth.value || vp.clientWidth;
  ct.style.transform = `translateX(-${pageIndex.value * (w + GAP)}px)`;
}

function goToPage(i: number) {
  pageIndex.value = Math.min(Math.max(0, i), pageCount.value - 1);
  applyPageTransform();
  scheduleSave();
}

function nextPage() {
  if (mode.value !== "paged") return;
  if (pageIndex.value >= pageCount.value - 1) {
    void nextChapter();
  } else {
    goToPage(pageIndex.value + 1);
  }
}

function prevPage() {
  if (mode.value !== "paged") return;
  if (pageIndex.value <= 0) {
    void prevChapter().then(() => {
      // 跳到上一章末页
      nextTick(() => {
        recountPages();
        goToPage(pageCount.value - 1);
      });
    });
  } else {
    goToPage(pageIndex.value - 1);
  }
}

/* ------------------------------------------------------ progress save */
function scheduleSave() {
  if (saveTimer !== null) window.clearTimeout(saveTimer);
  saveTimer = window.setTimeout(() => {
    const ch = chapters.value[current.value];
    if (!ch) return;
    const offset = mode.value === "paged"
      ? pageIndex.value
      : (bodyEl.value?.scrollTop ?? 0);
    void api.saveProgress(bookUrl, current.value, ch.title ?? "", offset);
  }, 700);
}

function onScroll() {
  if (mode.value === "scroll") scheduleSave();
}

onMounted(() => {
  bodyEl.value?.addEventListener("scroll", onScroll, { passive: true });
  window.addEventListener("resize", onResize);
  window.addEventListener("keydown", onKeydown);
});
onBeforeUnmount(() => {
  bodyEl.value?.removeEventListener("scroll", onScroll);
  window.removeEventListener("resize", onResize);
  window.removeEventListener("keydown", onKeydown);
  if (saveTimer !== null) window.clearTimeout(saveTimer);
});

async function onResize() {
  await measureViewport();
  recountPages();
  applyPageTransform();
}

/* ------------------------------------------------------------- input */
function onTapRegion(e: MouseEvent) {
  if (mode.value !== "paged") return;
  const x = e.clientX;
  const w = window.innerWidth;
  if (x < w * 0.3) prevPage();
  else if (x > w * 0.7) nextPage();
}

function onTouchStart(e: TouchEvent) {
  if (mode.value !== "paged") return;
  touchX = e.touches[0].clientX;
  touchY = e.touches[0].clientY;
}

let touchX = 0;
let touchY = 0;

function onTouchEnd(e: TouchEvent) {
  if (mode.value !== "paged") return;
  const dx = e.changedTouches[0].clientX - touchX;
  const dy = e.changedTouches[0].clientY - touchY;
  if (Math.abs(dx) > 40 && Math.abs(dx) > Math.abs(dy)) {
    if (dx < 0) nextPage();
    else prevPage();
  }
}

function onKeydown(e: KeyboardEvent) {
  if (e.key === "ArrowRight" || e.key === "PageDown") {
    mode.value === "paged" ? nextPage() : nextChapter();
  } else if (e.key === "ArrowLeft" || e.key === "PageUp") {
    mode.value === "paged" ? prevPage() : prevChapter();
  } else if (e.key === "Escape") {
    showChapters.value = false;
    showSettings.value = false;
  }
}

/** 纯文本段落去掉可能残留的标签与 &nbsp;。 */
function strip(p: string): string {
  return p.replace(/&nbsp;/g, " ").replace(/<[^>]+>/g, "");
}

function switchMode(m: Mode) {
  if (m === mode.value) return;
  const was = mode.value;
  mode.value = m;
  persistPrefs();
  // 切换模式后按比例换算，尽量停留在同一阅读位置
  void nextTick(async () => {
    if (m === "paged") {
      await measureViewport();
      recountPages();
      applyPageTransform();
    }
    const doc = bodyEl.value;
    if (was === "paged") {
      const ratio = pageIndex.value / Math.max(1, pageCount.value - 1);
      if (m === "scroll" && doc) {
        doc.scrollTo({
          top: ratio * Math.max(0, doc.scrollHeight - doc.clientHeight),
        });
      }
    } else if (was === "scroll" && doc) {
      const ratio = doc.scrollTop
        / Math.max(1, doc.scrollHeight - doc.clientHeight);
      goToPage(Math.round(ratio * Math.max(0, pageCount.value - 1)));
    }
  });
}

function changeFont(delta: number) {
  fontSize.value = Math.min(24, Math.max(13, fontSize.value + delta));
  persistPrefs();
  void nextTick(() => {
    recountPages();
    applyPageTransform();
  });
}

function goDetail() {
  if (refId != null) {
    // 详情页短链：直接读 book_refs 缓存档案，避免拼超长 query
    void router.push(`/book/ref/${refId}`);
    return;
  }
  void router.push(detailRoute({
    sourceUrl: origin,
    bookUrl,
    name: bookName.value,
    author: author.value,
    coverUrl: profile.value.coverUrl,
    intro: profile.value.intro,
    kind: profile.value.kind,
    lastChapter: profile.value.lastChapter,
    tocUrl: profile.value.tocUrl,
  }));
}

function goBack() {
  if (window.history.length > 1) router.back();
  else goDetail();
}

const chapterTitle = computed(() => chapters.value[current.value]?.title ?? "");
</script>

<template>
  <div class="reader-page" @click="onTapRegion"
       @touchstart.passive="onTouchStart" @touchend.passive="onTouchEnd">
    <!-- top bar -->
    <header class="bar top">
      <button class="ib" title="返回" @click.stop="goBack">←</button>
      <div class="titles">
        <div class="t-book">{{ bookName }}</div>
        <div class="t-chap">{{ chapterTitle }}</div>
      </div>
      <button class="ib" title="书籍信息" @click.stop="showInfo = true">ⓘ</button>
      <button class="ib" title="章节列表" @click.stop="showChapters = true">☰</button>
      <button class="ib" title="阅读设置" @click.stop="showSettings = !showSettings">Aa</button>
    </header>

    <!-- settings popover -->
    <div v-if="showSettings" class="settings-pop" @click.stop>
      <div class="set-row">
        <span class="lbl">阅读模式</span>
        <div class="seg">
          <button :class="{ on: mode === 'scroll' }" @click="switchMode('scroll')">滑动</button>
          <button :class="{ on: mode === 'paged' }" @click="switchMode('paged')">翻页</button>
        </div>
      </div>
      <div class="set-row">
        <span class="lbl">字号</span>
        <div class="seg">
          <button @click="changeFont(-1)">A−</button>
          <span class="fs-num">{{ fontSize }}</span>
          <button @click="changeFont(1)">A＋</button>
        </div>
      </div>
      <p v-if="mode === 'paged'" class="hint">点击屏幕左/右侧或左右滑动翻页；键盘 ← → 可用。</p>
    </div>

    <!-- body：唯一滚动容器（页面本体不滚动，顶栏/底栏恒贴屏） -->
    <main ref="bodyEl" class="body">
      <div v-if="loadingToc" class="center">
        <span class="spin" />
        <p v-if="tocQueued" class="wait-tip">目录缓存为空，正在后台抓取…</p>
      </div>

      <div v-else-if="tocError" class="center err-block">
        <p>{{ tocError }}</p>
        <button class="btn" @click="goBack">返回详情</button>
      </div>

      <template v-else>
        <div v-if="loadingContent" class="center"><span class="spin" /></div>
        <div v-else-if="contentError" class="center err-block">
          <p>本章加载失败：{{ contentError }}</p>
          <p class="offline-tip">
            若当前是断网状态，说明「{{ bookName }}」本章还没下载到本地书库。<br>
            联网时先在详情页点「下载到本地库」，之后离线也能读。
          </p>
          <button class="btn" @click="openChapter(current)">重试</button>
        </div>

        <!-- 章节内容：切换上一章/下一章时淡入淡出过渡（appear 保证每次挂载都播放入场动画） -->
        <Transition v-else name="chapter" mode="out-in" appear>
          <!-- 滑动模式 -->
          <article
            v-if="mode === 'scroll'"
            :key="current"
            class="content scroll-mode"
            :style="{ fontSize: fontSize + 'px' }"
          >
            <template v-for="(p, i) in paragraphs" :key="i">
              <p
                v-if="p.startsWith('<img')"
                class="para img-para"
                v-html="proxyImages(p)"
              />
              <p v-else class="para">{{ strip(p) }}</p>
            </template>
            <div class="chapter-nav">
              <button class="btn" :disabled="current <= 0" @click="prevChapter">上一章</button>
              <button
                class="btn primary"
                :disabled="current >= chapters.length - 1"
                @click="nextChapter"
              >下一章</button>
            </div>
          </article>

          <!-- 翻页模式：CSS 多列横向分页 -->
          <div v-else :key="current" class="paged-viewport" ref="pagedViewport">
            <div
              class="paged-content"
              ref="pagedContent"
              :style="{
                fontSize: fontSize + 'px',
                columnWidth: (vpWidth || 400) + 'px',
              }"
            >
              <template v-for="(p, i) in paragraphs" :key="i">
                <p
                  v-if="p.startsWith('<img')"
                  class="para img-para"
                  v-html="proxyImages(p)"
                />
                <p v-else class="para">{{ strip(p) }}</p>
              </template>
              <div class="paged-end">本章完 · {{ pageCount }} 页</div>
            </div>
          </div>
        </Transition>
      </template>
    </main>

    <!-- bottom bar -->
    <footer v-if="!loadingToc && !tocError" class="bar bottom">
      <button class="btn" :disabled="current <= 0" @click.stop="prevChapter">上一章</button>
      <span v-if="mode === 'paged'" class="pos">{{ pageIndex + 1 }} / {{ pageCount }}</span>
      <span v-else class="pos">{{ current + 1 }} / {{ chapters.length }}</span>
      <button
        class="btn primary"
        :disabled="current >= chapters.length - 1"
        @click.stop="nextChapter"
      >下一章</button>
    </footer>

    <!-- chapter drawer -->
    <Transition name="drawer">
      <div v-if="showChapters" class="drawer-mask" @click.self="showChapters = false">
        <aside class="drawer">
          <div class="drawer-head">
            <span>目录（{{ chapters.length }} 章）</span>
            <button class="ib" @click="showChapters = false">✕</button>
          </div>
          <ol class="drawer-list">
            <li v-for="(c, i) in chapters" :key="c.url + i">
              <a
                :class="{ cur: i === current }"
                @click.prevent="showChapters = false; openChapter(i)"
              >
                <span class="num">{{ i + 1 }}</span>
                <span class="ttl">{{ c.title || `第${i + 1}章` }}</span>
              </a>
            </li>
          </ol>
        </aside>
      </div>
    </Transition>

    <!-- book info sheet：全部来自本地缓存档案，不现场请求书源 -->
    <Transition name="drawer">
      <div v-if="showInfo" class="drawer-mask" @click.self="showInfo = false">
        <aside class="drawer info-drawer" @click.stop>
          <div class="info-head">
            <img
              class="info-cover"
              :src="profile.coverUrl ? coverProxyUrl(profile.coverUrl) : FALLBACK_COVER_SVG"
              alt=""
              loading="lazy"
              @error="onCoverError($event, profile.coverUrl)"
            >
            <div class="info-meta">
              <div class="info-name">{{ bookName }}</div>
              <div class="info-author">{{ author || "佚名" }}</div>
              <div v-if="profile.kind" class="info-kind">{{ profile.kind }}</div>
              <div v-if="profile.lastChapter" class="info-last">
                最新：{{ profile.lastChapter }}
              </div>
              <div class="info-count">共 {{ chapters.length }} 章</div>
            </div>
          </div>
          <div class="info-body">
            <h4 class="info-sec">简介</h4>
            <p v-if="profile.intro" class="info-intro">{{ profile.intro }}</p>
            <p v-else class="info-intro empty">暂无简介</p>
            <button
              v-if="!profile.intro"
              class="btn"
              :disabled="introLoading"
              @click="fetchIntro"
            >{{ introLoading ? "获取中…" : "从书源获取简介" }}</button>
          </div>
          <div class="info-foot">
            <button class="btn" @click="showInfo = false">关闭</button>
            <button class="btn primary" @click="showInfo = false; goDetail()">查看详情页</button>
          </div>
        </aside>
      </div>
    </Transition>
  </div>
</template>

<style scoped>
/* 全屏 flex 列：顶栏 / 滚动正文 / 底栏三段，底栏恒在屏幕最底部，
   不依赖 position:fixed（任何缩放、全页截图、移动端动态视口下都不会跑位） */
.reader-page {
  height: 100vh;
  height: 100dvh;
  display: flex;
  flex-direction: column;
  overflow: hidden;
  background: var(--m-color-surface-container-lowest, #faf9f7);
  color: var(--m-color-on-surface, #1d1b1a);
}
.bar {
  flex: none;
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 8px 14px;
  background: var(--m-color-surface-container, #f3f1ee);
  border-bottom: 1px solid rgba(0, 0, 0, 0.06);
}
.bar.top { height: 52px; }
.bar.bottom {
  border-bottom: 0;
  border-top: 1px solid rgba(0, 0, 0, 0.06);
  /* 三列网格：位置指示器严格水平居中，且整条贴住屏幕底边 */
  display: grid;
  grid-template-columns: 1fr auto 1fr;
  align-items: center;
  padding-bottom: calc(8px + env(safe-area-inset-bottom, 0px));
}
/* 按钮按内容自适应宽度，不撑满两侧列（避免"上一章"占满左半屏） */
.bar.bottom .btn { padding: 5px 12px; font-size: 12px; }
.bar.bottom .btn:first-child { justify-self: start; }
.bar.bottom .btn:last-child { justify-self: end; }
.ib {
  border: 0;
  background: transparent;
  color: inherit;
  font-size: 18px;
  width: 38px;
  height: 38px;
  border-radius: 12px;
  cursor: pointer;
  flex: none;
}
.ib:hover { background: rgba(0, 0, 0, 0.06); }
.titles {
  flex: 1;
  min-width: 0;
  line-height: 1.25;
}
.t-book {
  font-weight: 600;
  font-size: 14px;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.t-chap {
  font-size: 12px;
  opacity: 0.65;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
/* 唯一滚动容器：正文在中间滚动，顶栏/底栏不参与滚动；
   隐藏滚动条（保留滚动），阅读界面更沉浸 */
.body {
  flex: 1;
  min-height: 0;
  overflow-y: auto;
  overscroll-behavior: contain;
  -webkit-overflow-scrolling: touch;
  scrollbar-width: none; /* Firefox */
  width: 100%;
  max-width: 860px;
  margin: 0 auto;
  padding: 16px 20px;
}
.body::-webkit-scrollbar { display: none; } /* Chrome / Edge / Safari */
.center {
  display: grid;
  place-items: center;
  padding: 80px 0;
  gap: 12px;
}
.wait-tip {
  margin: 0;
  font-size: 13px;
  opacity: 0.6;
}
.err-block { color: var(--m-color-error, #b3261e); text-align: center; }
.offline-tip { color: var(--m-color-on-background-variant); font-size: 12px; }
.spin {
  width: 28px;
  height: 28px;
  border-radius: 50%;
  border: 3px solid rgba(0, 0, 0, 0.15);
  border-top-color: var(--m-color-primary, #5b6ac4);
  animation: r 0.9s linear infinite;
}
@keyframes r { to { transform: rotate(360deg); } }

.para {
  line-height: 1.95;
  text-indent: 2em;
  margin: 0 0 0.9em;
  word-break: break-word;
}
.img-para { text-indent: 0; text-align: center; }
.img-para :deep(img) { max-width: 100%; border-radius: 8px; }
.chapter-nav {
  display: flex;
  justify-content: space-between;
  margin-top: 28px;
}
.btn {
  border: 1px solid rgba(0, 0, 0, 0.14);
  background: var(--m-color-surface-container, #f3f1ee);
  color: inherit;
  border-radius: 999px;
  padding: 7px 16px;
  font-size: 13px;
  cursor: pointer;
  transition: transform 0.12s ease, opacity 0.15s ease, background 0.15s ease;
}
.btn:active:not(:disabled) { transform: scale(0.94); }
.btn:disabled { opacity: 0.4; cursor: default; }
.btn.primary {
  background: var(--m-color-primary, #5b6ac4);
  border-color: transparent;
  color: var(--m-color-on-primary, #fff);
}
.pos { font-size: 12px; opacity: 0.6; }

/* ---- 章节切换动画：上一章/下一章 淡入淡出 ---- */
.chapter-enter-active, .chapter-leave-active {
  transition: opacity 0.22s ease, transform 0.22s ease;
}
.chapter-enter-from { opacity: 0; transform: translateY(10px); }
.chapter-leave-to { opacity: 0; transform: translateY(-10px); }

/* ---- 翻页模式 ---- */
.paged-viewport {
  height: 100%;
  overflow: hidden;
}
.paged-content {
  column-gap: 48px;
  /* column-width 由模板内联写入 px（该属性不接受百分比）；
     column-fill:auto 让内容按列填满固定高度后横向溢出 */
  column-fill: auto;
  height: 100%;
  transition: transform 0.18s ease;
  will-change: transform;
}
.paged-end {
  text-align: center;
  opacity: 0.55;
  font-size: 13px;
  padding: 24px 0;
}

/* ---- 设置弹层 ---- */
.settings-pop {
  position: fixed;
  top: 58px;
  right: 14px;
  z-index: 40;
  background: var(--m-color-surface-container, #f3f1ee);
  border-radius: 16px;
  box-shadow: 0 10px 32px rgba(0, 0, 0, 0.18);
  padding: 14px 16px;
  display: grid;
  gap: 12px;
  min-width: 240px;
}
.set-row { display: flex; align-items: center; justify-content: space-between; gap: 14px; }
.lbl { font-size: 13px; opacity: 0.75; }
.seg {
  display: inline-flex;
  align-items: center;
  border: 1px solid rgba(0, 0, 0, 0.14);
  border-radius: 999px;
  overflow: hidden;
}
.seg button {
  border: 0;
  background: transparent;
  color: inherit;
  padding: 5px 12px;
  font-size: 13px;
  cursor: pointer;
}
.seg button.on {
  background: var(--m-color-primary, #5b6ac4);
  color: var(--m-color-on-primary, #fff);
}
.fs-num { font-size: 13px; min-width: 22px; text-align: center; }
.hint { font-size: 11px; opacity: 0.55; margin: 0; }

/* ---- 章节抽屉 ---- */
.drawer-mask {
  position: fixed;
  inset: 0;
  z-index: 50;
  background: rgba(0, 0, 0, 0.35);
}
.drawer {
  position: absolute;
  top: 0;
  right: 0;
  bottom: 0;
  width: min(340px, 86vw);
  background: var(--m-color-surface-container-lowest, #faf9f7);
  display: flex;
  flex-direction: column;
}
.drawer-head {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 12px 14px;
  font-size: 14px;
  font-weight: 600;
  border-bottom: 1px solid rgba(0, 0, 0, 0.08);
}
.drawer-list {
  overflow-y: auto;
  list-style: none; /* 自动编号与块级链接会拆成两行，改用行内序号 */
  padding: 6px 4px 16px;
  margin: 0;
}
.drawer-list a {
  display: flex;
  align-items: baseline;
  gap: 8px;
  padding: 8px 12px;
  font-size: 13px;
  border-radius: 8px;
  cursor: pointer;
}
.drawer-list .num {
  flex: none;
  min-width: 2.2em;
  text-align: right;
  font-variant-numeric: tabular-nums;
  color: var(--m-color-on-background-variant, #9a918e);
  font-size: 11px;
}
.drawer-list .ttl {
  min-width: 0;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.drawer-list a:hover { background: rgba(0, 0, 0, 0.05); }
.drawer-list a.cur { color: var(--m-color-primary, #5b6ac4); font-weight: 600; }
.drawer-enter-active, .drawer-leave-active { transition: opacity 0.15s ease; }
.drawer-enter-from, .drawer-leave-to { opacity: 0; }

/* ---- 书籍信息弹层（缓存档案展示） ---- */
.info-drawer {
  display: flex;
  flex-direction: column;
  padding-bottom: env(safe-area-inset-bottom, 0px);
}
.info-head {
  display: flex;
  gap: 14px;
  padding: 16px 14px 12px;
  border-bottom: 1px solid rgba(0, 0, 0, 0.08);
}
.info-cover {
  width: 92px;
  height: 124px;
  object-fit: cover;
  border-radius: 10px;
  background: var(--m-color-surface-container-high, #eceae7);
  flex: none;
}
.info-meta { min-width: 0; align-self: center; }
.info-name {
  font-size: 16px;
  font-weight: 700;
  line-height: 1.35;
  word-break: break-word;
}
.info-author {
  margin-top: 4px;
  font-size: 13px;
  opacity: 0.65;
}
.info-kind {
  margin-top: 6px;
  font-size: 12px;
  color: var(--m-color-primary, #5b6ac4);
}
.info-last {
  margin-top: 6px;
  font-size: 11px;
  opacity: 0.55;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.info-count {
  margin-top: 2px;
  font-size: 11px;
  opacity: 0.55;
}
.info-body {
  flex: 1;
  min-height: 0;
  overflow-y: auto;
  padding: 12px 16px;
}
.info-sec {
  margin: 0 0 8px;
  font-size: 13px;
  font-weight: 600;
}
.info-intro {
  margin: 0 0 10px;
  font-size: 13px;
  line-height: 1.75;
  white-space: pre-wrap;
  word-break: break-word;
  color: var(--m-color-on-surface, #1d1b1a);
  opacity: 0.85;
}
.info-intro.empty { opacity: 0.45; }
.info-foot {
  flex: none;
  display: flex;
  justify-content: flex-end;
  gap: 10px;
  padding: 10px 14px;
  border-top: 1px solid rgba(0, 0, 0, 0.08);
}
</style>
