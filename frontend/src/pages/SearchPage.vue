<script setup lang="ts">
import { computed, onMounted, ref, watch } from "vue";
import { useRoute, useRouter } from "vue-router";
import { MiuixButton, MiuixProgressIndicator } from "miuix-vue";
import { api, errMsg, coverProxyUrl } from "@/api/client";
import type { BookResult, SourceRow } from "@/api/client";
import { FALLBACK_COVER_SVG, onCoverError } from "@/utils/cover";
import LoadingImage from "@/components/LoadingImage.vue";
import { collectGroups, splitGroups } from "@/utils/sourceGroups";
import { openDetail } from "@/utils/reader";

const router = useRouter();
const route = useRoute();

const key = ref("");
const sources = ref<SourceRow[]>([]);
const selected = ref<number[]>([]);
/** 当前浏览的分组；空串 = 全部分组。 */
const activeGroup = ref("");
const results = ref<BookResult[]>([]);
const errors = ref<{ message: string; originName?: string }[]>([]);
const searching = ref(false);
const searchedKey = ref("");

/** 发现页控件的「搜索」按钮会 push /search?q=关键词，这里接住并自动搜。 */
onMounted(async () => {
  const q = route.query.q;
  if (typeof q === "string" && q.trim()) key.value = q.trim();
  try {
    const r = await api.sourcesList();
    sources.value = r.items.filter((s) => s.enabled);
    selected.value = []; // empty = all
  } catch (e) {
    alert(errMsg(e));
  }
  // 书源列表就绪后再发起（doSearch 依赖它算范围；空列表=搜全部，也能搜）
  if (key.value.trim()) await doSearch();
});

// 已在本页时再次收到 ?q=（发现页又点了一次搜索按钮）
watch(
  () => route.query.q,
  (q) => {
    if (typeof q === "string" && q.trim() && q !== searchedKey.value) {
      key.value = q.trim();
      void doSearch();
    }
  },
);

const groups = computed(() => collectGroups(sources.value));

const visibleSources = computed(() =>
  activeGroup.value
    ? sources.value.filter((s) =>
        splitGroups(s.sourceGroup).includes(activeGroup.value),
      )
    : sources.value,
);

function pickGroup(g: string) {
  if (activeGroup.value === g) return;
  activeGroup.value = g;
  selected.value = []; // 切换分组后回到「本组全部」语义
}

/** 搜索范围：显式勾选 > 当前分组的全部源 > 全部来源（undefined）。 */
function scopeIds(): number[] | undefined {
  if (selected.value.length) return selected.value;
  if (
    activeGroup.value &&
    visibleSources.value.length > 0 &&
    visibleSources.value.length < sources.value.length
  )
    return visibleSources.value.map((s) => s.id);
  return undefined;
}

function toggleSource(id: number) {
  const i = selected.value.indexOf(id);
  if (i >= 0) selected.value.splice(i, 1);
  else selected.value.push(id);
}

/** 打开详情页：统一入口（解析成书籍档案后跳短链 /book/ref/:id）。 */
function openBook(it: BookResult) {
  void openDetail(router, {
    sourceUrl: it.origin,
    bookUrl: it.bookUrl,
    name: it.name,
    author: it.author ?? "",
    coverUrl: it.coverUrl ?? "",
    intro: it.intro ?? "",
    kind: it.kind ?? "",
    lastChapter: it.lastChapter ?? "",
  });
}

async function doSearch() {
  if (!key.value.trim()) return;
  searching.value = true;
  searchedKey.value = key.value.trim();
  try {
    const r = await api.searchBooks(key.value, 1, scopeIds());
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
    <div class="page-head">
      <div>
        <h2 class="page-title">搜索</h2>
        <p class="page-sub">跨书源聚合搜索，同名书目自动去重</p>
      </div>
    </div>

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

    <div v-if="groups.length" class="src-row grp-row" role="group" aria-label="书源分组">
      <button
        class="chip"
        :class="{ selected: !activeGroup }"
        @click="pickGroup('')"
      >全部分组</button>
      <button
        v-for="g in groups"
        :key="g.name"
        class="chip"
        :class="{ selected: activeGroup === g.name }"
        @click="pickGroup(g.name)"
      >{{ g.name }}<span class="g-count">{{ g.count }}</span></button>
    </div>

    <div class="src-row">
      <button
        class="chip"
        :class="{ selected: !selected.length }"
        @click="selected = []"
      >{{ activeGroup ? `本组全部（${visibleSources.length}）` : "全部来源" }}</button>
      <button
        v-for="s in visibleSources"
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

    <div class="cover-grid">
      <button
        v-for="(it, i) in results"
        :key="i"
        type="button"
        class="ctile"
        @click="openBook(it)"
      >
        <span class="ctile-cover">
          <LoadingImage
            :src="it.coverUrl ? coverProxyUrl(it.coverUrl) : FALLBACK_COVER_SVG"
            @error="onCoverError($event, it.coverUrl)"
          />
        </span>
        <span class="ctile-name">{{ it.name }}</span>
        <span class="ctile-meta">{{ it.author || "佚名" }}</span>
        <span v-if="it.kind" class="ctile-sub">{{ it.kind }}</span>
      </button>
    </div>

    <div
      v-if="!searching && searchedKey && !results.length"
      class="empty-state"
    >
      <span class="es-icon">
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.4"
             stroke-linecap="round" aria-hidden="true">
          <circle cx="11" cy="11" r="6.25"/><path d="m15.8 15.8 4.2 4.2"/>
        </svg>
      </span>
      <span class="es-title">「{{ searchedKey }}」没有结果</span>
      <span>换个关键词，或勾选更多书源再试。</span>
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
/* 分组行紧跟来源行上方，间距收紧 */
.grp-row {
  margin-bottom: 8px;
}
.g-count {
  margin-left: 5px;
  font-size: 11px;
  opacity: 0.55;
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
</style>
