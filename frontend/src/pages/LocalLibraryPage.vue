<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from "vue";
import { useRouter } from "vue-router";
import { api, errMsg, coverProxyUrl } from "@/api/client";
import { FALLBACK_COVER_SVG, onCoverError } from "@/utils/cover";

interface LibraryBook {
  sourceUrl: string;
  bookUrl: string;
  name: string;
  author: string;
  coverUrl: string;
  intro: string;
  storedChapters: number;
  totalChapters: number;
}
interface LibraryOverview {
  chapters: number;
  images: number;
  covers: number;
  booksTotal: number;
  books: LibraryBook[];
}
interface DownloadJob {
  key: string;
  sourceUrl: string;
  bookUrl: string;
  name: string;
  status: string;
  total: number;
  done: number;
  current: string;
  error: string;
}

const router = useRouter();
const loading = ref(false);
const loadError = ref("");
const stats = ref<LibraryOverview | null>(null);
const jobs = ref<DownloadJob[]>([]);
const concurrency = ref(4); // 预下载并发线程数（可调）
let pollTimer: number | null = null;

const runningJob = computed(() =>
  jobs.value.find((j) => j.status === "queued" || j.status === "running"),
);

async function load() {
  loading.value = true;
  loadError.value = "";
  try {
    const [lib, st] = await Promise.all([api.library(), api.libraryDownloadStatus()]);
    stats.value = lib;
    jobs.value = st.jobs;
  } catch (e) {
    loadError.value = errMsg(e);
  } finally {
    loading.value = false;
  }
}

function startPolling() {
  if (pollTimer !== null) return;
  pollTimer = window.setInterval(async () => {
    try {
      const st = await api.libraryDownloadStatus();
      jobs.value = st.jobs;
      const still = st.jobs.some((j) => j.status === "queued" || j.status === "running");
      if (!still) {
        stopPolling();
        stats.value = await api.library();
      }
    } catch {
      /* 忽略瞬时失败 */
    }
  }, 2000);
}

function stopPolling() {
  if (pollTimer !== null) {
    window.clearInterval(pollTimer);
    pollTimer = null;
  }
}

async function download(book: LibraryBook) {
  try {
    await api.libraryDownload({
      sourceUrl: book.sourceUrl,
      bookUrl: book.bookUrl,
      name: book.name,
      author: book.author,
      cover: book.coverUrl,
      concurrency: concurrency.value,
    });
    startPolling();
  } catch (e) {
    alert(errMsg(e));
  }
}

async function clearLibrary(book: LibraryBook) {
  const n = book.storedChapters;
  if (!confirm(`清除《${book.name}》的全部 ${n} 章缓存？此操作不可撤销。`)) return;
  try {
    await api.libraryClear(book.sourceUrl, book.bookUrl);
    alert(`已清除《${book.name}》的 ${n} 章缓存。`);
    await load();
  } catch (e) {
    alert(errMsg(e));
  }
}

function openBook(b: LibraryBook) {
  const q = new URLSearchParams({
    source_url: b.sourceUrl,
    name: b.name,
    author: b.author,
  });
  router.push(`/book/${encodeURIComponent(b.bookUrl)}?${q.toString()}`);
}

function percent(b: LibraryBook): number {
  if (b.totalChapters <= 0) return 0;
  return Math.min(100, Math.round((b.storedChapters / b.totalChapters) * 100));
}

function jobFor(b: LibraryBook): DownloadJob | undefined {
  return jobs.value.find(
    (j) => j.sourceUrl === b.sourceUrl && j.bookUrl === b.bookUrl,
  );
}

onMounted(() => {
  void load();
});
onBeforeUnmount(() => stopPolling());
</script>

<template>
  <div class="page">
    <div class="head">
      <div>
        <div class="title">本地书库</div>
        <p class="sub">已下载到本机的章节与图片，断网也能读。</p>
      </div>
      <MiuixButton @click="load" :disabled="loading">刷新</MiuixButton>
    </div>

    <template v-if="stats">
      <div class="stats">
        <div class="stat">
          <div class="stat-num">{{ stats.chapters }}</div>
          <div class="stat-label">已下载章节</div>
        </div>
        <div class="stat">
          <div class="stat-num">{{ stats.images }}</div>
          <div class="stat-label">本地图片</div>
        </div>
        <div class="stat">
          <div class="stat-num">{{ stats.booksTotal }}</div>
          <div class="stat-label">本地书目</div>
        </div>
      </div>

      <div class="dl-ctl">
        <label class="dl-ctl-label" for="dl-conc">预下载并发线程</label>
        <input
          id="dl-conc"
          class="dl-ctl-input"
          type="number"
          min="1"
          max="32"
          step="1"
          v-model.number="concurrency"
        >
        <span class="dl-ctl-hint">整本预下载同时抓取的章节数，越大越快、越费流量。</span>
      </div>

      <div v-if="runningJob" class="dl-banner">
        <div class="dl-row">
          <span>正在下载<strong>{{ runningJob.name }}</strong></span>
          <span v-if="runningJob.total">{{ runningJob.done }} / {{ runningJob.total }}</span>
        </div>
        <div v-if="runningJob.current" class="dl-current">当前：{{ runningJob.current }}</div>
        <div class="progress">
          <div
            class="progress-fill"
            :style="{ width: `${runningJob.total ? (runningJob.done / runningJob.total) * 100 : 0}%` }"
          />
        </div>
      </div>

      <div v-if="loadError" class="err">{{ loadError }}</div>

      <MiuixCard
        v-for="b in stats.books"
        :key="b.sourceUrl + b.bookUrl"
        class="book"
        :show-indication="false"
        @click="openBook(b)"
      >
        <div class="row">
          <img
            class="cover"
            :src="b.coverUrl ? coverProxyUrl(b.coverUrl) : FALLBACK_COVER_SVG"
            @error="onCoverError($event, b.coverUrl)"
            @click.stop
          >
          <div class="info">
            <div class="name">{{ b.name }}</div>
            <div class="author">{{ b.author || "佚名" }}</div>
            <div class="meta">
              已下载 {{ b.storedChapters }}
              <template v-if="b.totalChapters"> / 共 {{ b.totalChapters }} 章</template>
              <span v-else>章</span>
            </div>
            <div v-if="b.intro" class="intro">{{ b.intro }}</div>
            <div class="bar" v-if="b.totalChapters">
              <div class="bar-fill" :style="{ width: `${percent(b)}%` }" />
            </div>
          </div>
          <div class="act" @click.stop>
            <MiuixButton
              size="small"
              type="primary"
              :disabled="b.totalChapters && b.storedChapters >= b.totalChapters"
              @click="download(b)"
            >
              {{
                jobFor(b)?.status === "running" || jobFor(b)?.status === "queued"
                  ? "下载中…"
                  : b.totalChapters && b.storedChapters >= b.totalChapters
                    ? "已下载"
                    : "预下载整本"
              }}
            </MiuixButton>
            <MiuixButton
              v-if="b.storedChapters > 0"
              size="small"
              @click="clearLibrary(b)"
            >清除缓存</MiuixButton>
          </div>
        </div>
      </MiuixCard>

      <div v-if="!stats.books.length && !loading" class="empty">
        还没有下载任何章节。去书架/详情页点「预下载整本」就能建起本地书库。
      </div>
    </template>

    <div v-else-if="!loadError && loading" class="center"><MiuixProgressIndicator /></div>
  </div>
</template>

<style scoped>
.page {
  padding: 4px 0;
}
.head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 12px;
  margin-bottom: 14px;
}
.title {
  font-size: 20px;
  font-weight: 600;
}
.sub {
  margin: 4px 0 0;
  font-size: 12px;
  color: var(--m-color-on-background-variant);
}
.stats {
  display: grid;
  grid-template-columns: repeat(3, 1fr);
  gap: 10px;
  margin-bottom: 14px;
}
.stat {
  background: var(--m-color-surface-container);
  border-radius: 14px;
  padding: 12px 14px;
}
.stat-num {
  font-size: 22px;
  font-weight: 700;
  font-variant-numeric: tabular-nums;
}
.stat-label {
  font-size: 12px;
  color: var(--m-color-on-background-variant);
}
.dl-ctl {
  display: flex;
  align-items: center;
  gap: 10px;
  flex-wrap: wrap;
  background: var(--m-color-surface-container);
  border-radius: 12px;
  padding: 10px 14px;
  margin-bottom: 12px;
  font-size: 13px;
}
.dl-ctl-label {
  font-weight: 600;
}
.dl-ctl-input {
  width: 72px;
  padding: 5px 8px;
  border-radius: 8px;
  border: 1px solid var(--m-color-outline-variant);
  background: var(--m-color-surface-container-high);
  color: var(--m-color-on-background);
  font-size: 13px;
  text-align: center;
}
.dl-ctl-hint {
  font-size: 12px;
  color: var(--m-color-on-background-variant);
}
.dl-banner {
  background: var(--m-color-surface-container);
  border-radius: 14px;
  padding: 12px 14px;
  margin-bottom: 14px;
}
.dl-row {
  display: flex;
  justify-content: space-between;
  gap: 8px;
  font-size: 13px;
}
.dl-current {
  font-size: 12px;
  color: var(--m-color-on-background-variant);
  margin: 6px 0 8px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.progress,
.bar {
  height: 6px;
  border-radius: 999px;
  background: var(--m-color-surface-container-high);
  overflow: hidden;
}
.progress-fill,
.bar-fill {
  height: 100%;
  border-radius: 999px;
  background: var(--m-color-primary);
  transition: width 0.3s ease;
}
.book {
  margin-bottom: 10px;
  cursor: pointer;
}
.row {
  display: flex;
  gap: 14px;
}
.cover {
  width: 64px;
  height: 88px;
  border-radius: 10px;
  object-fit: cover;
  background: var(--m-color-surface-container-high);
  flex: none;
}
.info {
  min-width: 0;
  flex: 1;
}
.name {
  font-weight: 600;
  font-size: 15px;
}
.author {
  font-size: 12px;
  color: var(--m-color-on-background-variant);
  margin: 2px 0;
}
.meta {
  font-size: 12px;
  color: var(--m-color-primary);
}
.intro {
  font-size: 12px;
  color: var(--m-color-on-background-variant);
  margin-top: 4px;
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
  overflow: hidden;
}
.bar {
  margin-top: 8px;
}
.act {
  align-self: center;
  flex: none;
  display: flex;
  flex-direction: column;
  gap: 8px;
  align-items: stretch;
}
.center {
  display: flex;
  justify-content: center;
  padding: 40px 0;
}
.empty {
  text-align: center;
  color: var(--m-color-on-background-variant);
  font-size: 13px;
  padding: 40px 0;
}
.err {
  color: var(--m-color-error);
  font-size: 13px;
  margin: 10px 0;
}
</style>