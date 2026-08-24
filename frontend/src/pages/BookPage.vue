<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from "vue";
import { useRoute, useRouter } from "vue-router";
import {
  MiuixButton,
  MiuixCard,
  MiuixProgressIndicator,
  MiuixText,
  MiuixTabRow,
} from "miuix-vue";
import {
  api,
  errMsg,
  coverProxyUrl,
} from "@/api/client";
import type { BookResult, Chapter, ShelfEntry, SourceInfo } from "@/api/client";
import { FALLBACK_COVER_SVG, onCoverError } from "@/utils/cover";
import { openReader } from "@/utils/reader";

const route = useRoute();
const router = useRouter();

// vue-router 已对路径参数做过一次 decode，这里不能再解一次，
// 否则含 %xx 的书源 URL 会被二次解码损坏。
let bookUrl = route.params.bookUrl as string | undefined;
let origin = (route.query.origin as string) ?? "";
// 详情页短链模式：/book/ref/:refId -> 直接读 book_refs 缓存档案
const refId = route.params.refId != null ? Number(route.params.refId) : null;
const qName = (route.query.name as string) ?? "";
const qAuthor = (route.query.author as string) ?? "";
// 搜索结果里已知的封面：书源详情规则缺封面项时兜底展示
const qCover = (route.query.cover as string) ?? "";

const tab = ref(0);
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
const tocCached = ref(false);

/* shelf / toc-queue state */
const inShelf = ref<ShelfEntry | null>(null);
let pollTimer: number | null = null;

/* 详情 tab：书源信息 + 规则快照（本地库展示"源规则/书源"） */
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
const resumeLabel = computed(() => {
  const p = progressInfo.value ?? inShelf.value?.progress ?? null;
  if (p && p.chapterIndex >= 0) {
    return p.chapterTitle || `第 ${p.chapterIndex + 1} 章`;
  }
  return "未开始阅读";
});
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
    await loadSourceInfo();
    const r = await api.bookInfo(origin, bookUrl, qName, qAuthor, qCover);
    // 后端已做合并：规则取不到的字段保留搜索阶段已知值
    info.value = {
      ...r,
      name: r.name || qName,
      author: r.author || qAuthor,
      coverUrl: r.coverUrl || qCover || "",
    };
    document.title = `${info.value.name} · Viewer`;
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
  } catch (e) {
    alert(errMsg(e));
  } finally {
    loadingToc.value = false;
  }
}

function switchTab(i: number) {
  tab.value = i;
  if (i === 1) void loadToc();
}

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

const resumeIndex = computed(() => inShelf.value?.progress?.chapterIndex ?? -1);
const hasProgress = computed(
  () => inShelf.value?.progress != null && resumeIndex.value >= 0,
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

const tabs = ["详情", "目录"];
</script>

<template>
  <div>
    <button class="back" @click="router.back()">← 返回</button>

    <div v-if="loadingInfo" class="center"><MiuixProgressIndicator /></div>

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

    <template v-else-if="info">
      <!-- detail header -->
      <div class="hero">
        <img
          class="cover"
          :src="displayCover ? coverProxyUrl(displayCover) : FALLBACK_COVER_SVG"
          @error="onCoverError($event, displayCover)"
        >
        <div class="hero-info">
          <MiuixText type="title2">{{ info.name }}</MiuixText>
          <div class="author">{{ info.author }}</div>
          <div v-if="info.kind" class="kinds">{{ info.kind }}</div>
          <p class="intro">{{ info.intro }}</p>
          <div v-if="info.lastChapter" class="last">
            最新：{{ info.lastChapter }}
          </div>
          <div class="ops">
            <MiuixButton type="primary" @click="readFrom(hasProgress ? resumeIndex : 0)">
              {{ hasProgress ? "继续阅读" : "开始阅读" }}
            </MiuixButton>
            <MiuixButton v-if="!inShelf" @click="addToShelf">加入书架</MiuixButton>
            <MiuixButton v-else @click="removeFromShelf">移出书架</MiuixButton>
            <MiuixButton @click="downloadToLibrary">下载到本地库</MiuixButton>
          </div>
          <div v-if="hasProgress" class="resume-hint">
            上次读到：{{ inShelf?.progress?.chapterTitle }}
          </div>
        </div>
      </div>

      <MiuixTabRow
        :tabs="tabs"
        :model-value="tab"
        @update:model-value="switchTab($event)"
      />

      <!-- detail (本地书库信息) -->
      <MiuixCard v-if="tab === 0" class="panel" :show-indication="false">
        <dl class="detail-grid">
          <div class="d-row"><dt>书名</dt><dd>{{ info.name }}</dd></div>
          <div class="d-row"><dt>作者</dt><dd>{{ info.author || "佚名" }}</dd></div>
          <div class="d-row"><dt>分类</dt><dd>{{ info.kind || "—" }}</dd></div>
          <div class="d-row"><dt>内容</dt><dd class="intro">{{ info.intro || "暂无简介" }}</dd></div>
          <div class="d-row"><dt>上次看到</dt><dd>{{ resumeLabel }}</dd></div>
          <div class="d-row"><dt>最新章节</dt><dd>{{ info.lastChapter || "—" }}</dd></div>
          <div class="d-row"><dt>章节数</dt><dd>{{ chapters.length ? `${chapters.length} 章` : "—" }}</dd></div>
          <div class="d-row">
            <dt>书源</dt>
            <dd>{{ sourceInfo ? `${sourceInfo.name}（${sourceInfo.type}）` : origin || "—" }}</dd>
          </div>
          <div class="d-row">
            <dt>源地址</dt>
            <dd class="mono">{{ origin || "—" }}</dd>
          </div>
        </dl>

        <div class="rules">
          <div class="rules-title">源规则</div>
          <pre class="rules-json">{{ sourceInfo ? stringify(sourceInfo.rules) : "加载中…" }}</pre>
        </div>
      </MiuixCard>

      <!-- toc -->
      <MiuixCard v-if="tab === 1" class="panel" :show-indication="false">
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
        <div v-if="loadingToc" class="center"><MiuixProgressIndicator /></div>
        <ol v-else class="toc-list">
          <li v-for="(c, i) in chapters" :key="c.url + i">
            <a @click.prevent="readFrom(i)">
              <span class="num">{{ i + 1 }}</span>
              <span class="ttl">{{ c.title || `第${i + 1}章` }}</span>
            </a>
          </li>
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
    </template>
  </div>
</template>

<style scoped>
.back {
  border: 0;
  background: none;
  color: var(--m-color-primary);
  cursor: pointer;
  font-size: 14px;
  margin-bottom: 12px;
  padding: 0;
}
.hero {
  display: flex;
  gap: 22px;
  margin-bottom: 18px;
}
.cover {
  width: 130px;
  height: 176px;
  border-radius: 14px;
  object-fit: cover;
  background: var(--m-color-surface-container-high);
  flex: none;
}
.hero-info {
  min-width: 0;
}
.author {
  color: var(--m-color-on-surface-secondary);
  margin: 4px 0;
}
.kinds {
  font-size: 12px;
  color: var(--m-color-primary);
  margin-bottom: 6px;
}
.intro {
  color: var(--m-color-on-background-variant);
  font-size: 13px;
  line-height: 1.6;
  max-width: 640px;
}
.last {
  font-size: 12px;
  color: var(--m-color-outline);
  margin: 8px 0;
}
.ops {
  display: flex;
  gap: 10px;
}
.resume-hint {
  margin-top: 8px;
  font-size: 12px;
  color: var(--m-color-on-background-variant);
}
.panel {
  margin-top: 14px;
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
}
@media (max-width: 720px) {
  .toc-list {
    columns: 1;
  }
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

/* ---- 详情 tab ---- */
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
.d-row dt {
  color: var(--m-color-on-background-variant);
  flex: none;
}
.d-row dd {
  margin: 0;
  color: var(--m-color-on-surface);
  min-width: 0;
  word-break: break-word;
  white-space: pre-wrap;
}
.d-row .intro {
  max-width: none;
}
.mono {
  font-family: ui-monospace, "Cascadia Code", Consolas, monospace;
  font-size: 12px;
  overflow-wrap: anywhere;
}
.rules {
  margin-top: 14px;
}
.rules-title {
  font-weight: 600;
  font-size: 13px;
  margin-bottom: 6px;
}
.rules-json {
  margin: 0;
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
