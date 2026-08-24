<script setup lang="ts">
import { onMounted, ref } from "vue";
import { useRouter } from "vue-router";
import { MiuixButton, MiuixCard, MiuixProgressIndicator, MiuixText } from "miuix-vue";
import { api, errMsg, coverProxyUrl } from "@/api/client";
import type { BookResult, SourceRow } from "@/api/client";
import { FALLBACK_COVER_SVG, onCoverError } from "@/utils/cover";

const router = useRouter();

const key = ref("");
const sources = ref<SourceRow[]>([]);
const selected = ref<number[]>([]);
const results = ref<BookResult[]>([]);
const errors = ref<{ message: string; originName?: string }[]>([]);
const searching = ref(false);
const searchedKey = ref("");

onMounted(async () => {
  try {
    const r = await api.sourcesList();
    sources.value = r.items.filter((s) => s.enabled);
    selected.value = []; // empty = all
  } catch (e) {
    alert(errMsg(e));
  }
});

function toggleSource(id: number) {
  const i = selected.value.indexOf(id);
  if (i >= 0) selected.value.splice(i, 1);
  else selected.value.push(id);
}

/** 打开详情页：先把搜索结果 reseolve 成书籍档案（写入缓存），再跳短链。 */
async function openBook(it: BookResult) {
  try {
    const ref = await api.resolveBook({
      sourceUrl: it.origin,
      bookUrl: it.bookUrl,
      name: it.name,
      author: it.author ?? "",
      coverUrl: it.coverUrl ?? "",
      intro: it.intro ?? "",
      kind: it.kind ?? "",
      lastChapter: it.lastChapter ?? "",
      tocUrl: "",
    });
    router.push(`/book/ref/${ref.id}`);
  } catch {
    // 解析失败退回长 URL（详情页仍会用 bookProfile 缓存兜底）
    router.push(
      `/book/${encodeURIComponent(it.bookUrl)}?origin=${encodeURIComponent(it.origin)}&name=${encodeURIComponent(it.name)}&author=${encodeURIComponent(it.author ?? "")}&cover=${encodeURIComponent(it.coverUrl ?? "")}`,
    );
  }
}

async function doSearch() {
  if (!key.value.trim()) return;
  searching.value = true;
  searchedKey.value = key.value.trim();
  try {
    const r = await api.searchBooks(
      key.value,
      1,
      selected.value.length === sources.value.length ? undefined : selected.value,
    );
    // dedupe by name+author across sources
    const seen = new Set<string>();
    results.value = r.items.filter((it) => {
      const k2 = `${it.name}|${it.author}`;
      if (seen.has(k2)) return false;
      seen.add(k2);
      return true;
    });
    errors.value = r.errors;
  } catch (e) {
    alert(errMsg(e));
  } finally {
    searching.value = false;
  }
}
</script>

<template>
  <div>
    <h2 class="page-title">搜索</h2>

    <form class="search-row" @submit.prevent="doSearch">
      <input
        v-model="key"
        class="big-input"
        placeholder="输入书名 / 作者关键词…"
        @keydown.enter.prevent="doSearch"
      />
      <MiuixButton type="primary" :disabled="searching || !key" @click="doSearch">
        {{ searching ? "搜索中…" : "搜 索" }}
      </MiuixButton>
    </form>

    <div class="src-row">
      <button
        class="chip"
        :class="{ selected: !selected.length }"
        @click="selected = []"
      >全部来源</button>
      <button
        v-for="s in sources"
        :key="s.id"
        class="chip"
        :class="{ selected: selected.includes(s.id) }"
        @click="toggleSource(s.id)"
      >{{ s.sourceName || s.sourceUrl }}</button>
      <span v-if="!sources.length" class="none-src">
        没有启用的书源，请到 管理 → 书源管理 导入。
      </span>
    </div>

    <div v-if="searching" class="center"><MiuixProgressIndicator /></div>

    <div v-if="errors.length && !searching" class="src-errors">
      {{ errors.length }} 个书源失败：
      {{ errors.slice(0, 3).map((e) => `${e.originName ?? ""} ${e.message}`).join("；") }}
    </div>

    <div class="result-grid">
      <MiuixCard
        v-for="(it, i) in results"
        :key="i"
        class="book-card"
        @click="openBook(it)"
      >
        <img
          class="cover"
          :src="it.coverUrl ? coverProxyUrl(it.coverUrl) : FALLBACK_COVER_SVG"
          loading="lazy"
          @error="onCoverError($event, it.coverUrl)"
        >
        </img>
        <div class="info">
          <MiuixText type="title4">{{ it.name }}</MiuixText>
          <div class="meta">{{ it.author }}</div>
          <div v-if="it.kind" class="kinds">{{ it.kind }}</div>
          <div class="intro">{{ it.intro }}</div>
          <div class="last">{{ it.lastChapter }}</div>
        </div>
      </MiuixCard>
    </div>

    <div
      v-if="!searching && searchedKey && !results.length"
      class="center empty"
    >
      「{{ searchedKey }}」没有结果。
    </div>
  </div>
</template>

<style scoped>
.search-row {
  display: flex;
  gap: 12px;
  margin-bottom: 12px;
}
.big-input {
  flex: 1;
  border: 1px solid var(--m-color-outline);
  border-radius: 999px;
  background: var(--m-color-surface-container);
  color: var(--m-color-on-surface);
  font-size: 15px;
  padding: 12px 20px;
  outline: none;
}
.big-input:focus {
  border-color: var(--m-color-primary);
}
.src-row {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  margin-bottom: 18px;
}
.none-src {
  color: var(--m-color-on-background-variant);
  font-size: 13px;
}
.src-errors {
  color: var(--m-color-error);
  font-size: 12px;
  margin-bottom: 10px;
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
.kinds {
  font-size: 11px;
  color: var(--m-color-primary);
}
.intro {
  font-size: 12px;
  color: var(--m-color-on-background-variant);
  display: -webkit-box;
  -webkit-line-clamp: 3;
  -webkit-box-orient: vertical;
  overflow: hidden;
}
.last {
  margin-top: auto;
  font-size: 11px;
  color: var(--m-color-outline);
}
</style>
