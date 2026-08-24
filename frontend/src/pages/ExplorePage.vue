<script setup lang="ts">
import { onMounted, ref } from "vue";
import { useRouter } from "vue-router";
import {
  MiuixButton,
  MiuixCard,
  MiuixProgressIndicator,
  MiuixText,
} from "miuix-vue";
import { api, errMsg, coverProxyUrl } from "@/api/client";
import type { BookResult, ExploreKind, SourceRow } from "@/api/client";
import { FALLBACK_COVER_SVG, onCoverError } from "@/utils/cover";

const $router = useRouter();

const sources = ref<SourceRow[]>([]);
const activeSource = ref("");
const kinds = ref<ExploreKind[]>([]);
const activeKindUrl = ref("");
const books = ref<BookResult[]>([]);
const page = ref(1);
const loading = ref(false);
const loadingKinds = ref(false);
const error = ref("");
const done = ref(false);

onMounted(async () => {
  try {
    const r = await api.sourcesList();
    sources.value = r.items.filter((s) => s.enabled);
    if (sources.value.length) await pickSource(sources.value[0].sourceUrl);
  } catch (e) {
    error.value = errMsg(e);
  }
});

async function pickSource(url: string) {
  if (activeSource.value === url) return;
  activeSource.value = url;
  kinds.value = [];
  activeKindUrl.value = "";
  books.value = [];
  error.value = "";
  loadingKinds.value = true;
  try {
    kinds.value = (await api.exploreKinds(url)).items;
    if (!kinds.value.length) {
      error.value = "该书源未配置发现（exploreUrl），换一个书源试试。";
      return;
    }
    const first = kinds.value.find((k) => k.url);
    if (first) await pickKind(first.url!);
  } catch (e) {
    error.value = errMsg(e);
  } finally {
    loadingKinds.value = false;
  }
}

async function pickKind(url: string) {
  if (!url || activeKindUrl.value === url) return;
  activeKindUrl.value = url;
  page.value = 1;
  books.value = [];
  done.value = false;
  await fetchBooks();
}

async function fetchBooks() {
  if (!activeSource.value || !activeKindUrl.value || loading.value || done.value)
    return;
  loading.value = true;
  try {
    const r = await api.exploreBooks(activeSource.value, activeKindUrl.value, page.value);
    books.value.push(...r.items);
    done.value = r.items.length === 0;
    page.value += 1;
  } catch (e) {
    error.value = errMsg(e);
    done.value = true;
  } finally {
    loading.value = false;
  }
}

async function openBook(b: BookResult) {
  try {
    const ref = await api.resolveBook({
      sourceUrl: b.origin,
      bookUrl: b.bookUrl,
      name: b.name,
      author: b.author ?? "",
      coverUrl: b.coverUrl ?? "",
      intro: b.intro ?? "",
      kind: b.kind ?? "",
      lastChapter: b.lastChapter ?? "",
      tocUrl: "",
    });
    void $router.push(`/book/ref/${ref.id}`);
  } catch {
    void $router.push(
      `/book/${encodeURIComponent(b.bookUrl)}?origin=${encodeURIComponent(
        b.origin,
      )}&name=${encodeURIComponent(b.name)}&author=${encodeURIComponent(
        b.author ?? "",
      )}&cover=${encodeURIComponent(b.coverUrl ?? "")}`,
    );
  }
}
</script>

<template>
  <div>
    <h2 class="page-title">发现</h2>

    <div class="src-row">
      <button
        v-for="s in sources"
        :key="s.sourceUrl"
        class="chip"
        :class="{ selected: s.sourceUrl === activeSource }"
        @click="pickSource(s.sourceUrl)"
      >
        {{ s.sourceName || s.sourceUrl }}
      </button>
      <span v-if="!sources.length" class="none-src">
        没有启用的书源，请到 管理 → 书源管理 导入。
      </span>
    </div>

    <div v-if="kinds.length" class="kind-row">
      <button
        v-for="k in kinds"
        :key="k.title"
        class="chip small"
        :class="{ selected: k.url === activeKindUrl }"
        :disabled="!k.url"
        @click="pickKind(k.url ?? '')"
      >
        {{ k.title }}
      </button>
    </div>

    <div v-if="loadingKinds" class="center">
      <MiuixProgressIndicator />
    </div>
    <p v-if="error" class="err">{{ error }}</p>

    <div v-if="books.length" class="result-grid">
      <MiuixCard
        v-for="(b, i) in books"
        :key="b.origin + b.bookUrl + i"
        class="book-card"
        @click="openBook(b)"
      >
        <img
          class="cover"
          :src="b.coverUrl ? coverProxyUrl(b.coverUrl) : FALLBACK_COVER_SVG"
          loading="lazy"
          @error="onCoverError($event, b.coverUrl)"
        >
        <div class="info">
          <MiuixText type="title4">{{ b.name }}</MiuixText>
          <div class="meta">{{ b.author }}</div>
          <div class="intro">{{ b.intro }}</div>
        </div>
      </MiuixCard>
    </div>

    <div class="more-row" v-if="books.length || loading">
      <MiuixButton v-if="!done" :disabled="loading" @click="fetchBooks">
        {{ loading ? "加载中…" : "加载更多" }}
      </MiuixButton>
      <span v-else-if="books.length" class="none-src">没有更多了</span>
    </div>

    <div
      v-if="!loading && !loadingKinds && !error && activeKindUrl && !books.length"
      class="center empty"
    >
      这个分类暂时没有内容。
    </div>
  </div>
</template>

<style scoped>
.src-row,
.kind-row {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  margin-bottom: 14px;
}
.kind-row {
  margin-top: -4px;
}
.none-src {
  color: var(--m-color-on-background-variant);
  font-size: 13px;
}
.chip {
  border: 1px solid var(--m-color-outline);
  background: var(--m-color-surface-container);
  color: var(--m-color-on-surface);
  border-radius: 999px;
  padding: 7px 16px;
  font-size: 13px;
  cursor: pointer;
}
.chip.small {
  padding: 5px 13px;
  font-size: 12px;
}
.chip.selected {
  background: var(--m-color-secondary-container);
  border-color: transparent;
  color: var(--m-color-on-secondary-container);
  font-weight: 600;
}
.chip:disabled {
  opacity: 0.55;
  cursor: default;
}
.err {
  color: var(--m-color-error);
  font-size: 13px;
  margin: 6px 0 12px;
}
.result-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(300px, 1fr));
  gap: 14px;
}
.book-card {
  cursor: pointer;
}
.book-card :deep(.m-card) {
  flex-direction: row;
  gap: 12px;
}
.cover {
  width: 84px;
  height: 112px;
  object-fit: cover;
  border-radius: 10px;
  background: var(--m-color-surface-container-high);
  flex: none;
}
.info {
  min-width: 0;
  flex: 1;
  display: flex;
  flex-direction: column;
  gap: 4px;
}
.meta {
  font-size: 13px;
  color: var(--m-color-on-surface-secondary);
}
.intro {
  font-size: 12px;
  color: var(--m-color-on-background-variant);
  display: -webkit-box;
  -webkit-line-clamp: 3;
  -webkit-box-orient: vertical;
  overflow: hidden;
}
.more-row {
  display: flex;
  justify-content: center;
  margin: 18px 0 6px;
}
</style>
