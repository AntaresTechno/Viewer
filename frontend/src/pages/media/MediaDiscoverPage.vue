<script setup lang="ts">
import { computed, onMounted, ref } from "vue";
import { useRouter } from "vue-router";
import { MiuixButton, MiuixDialog, MiuixProgressIndicator } from "miuix-vue";
import { api, errMsg } from "@/api/client";
import type { MediaCatalogItem, MediaCatalogPageRes, MediaKind, MediaSource, MediaSort } from "@/api/client";
import MediaCoverCard from "@/components/media/MediaCoverCard.vue";

const router = useRouter();
const sources = ref<MediaSource[]>([]);
const activeSource = ref<MediaSource | null>(null);
const sorts = ref<MediaSort[]>([]);
const activeSort = ref<MediaSort | null>(null);
const items = ref<MediaCatalogItem[]>([]);
const loading = ref(true);
const error = ref("");
const warning = ref("");
const searchQ = ref("");
const searching = ref(false);

const KINDS: MediaKind[] = ["comic", "audio", "video"];

const kindFilter = ref<MediaKind | "">("");

const visibleSources = computed(() =>
  kindFilter.value ? sources.value.filter((s) => s.mediaKind === kindFilter.value) : sources.value,
);

onMounted(async () => {
  try {
    sources.value = await api.mediaSources();
    if (sources.value.length) selectSource(sources.value[0]);
    error.value = "";
  } catch (e) {
    error.value = errMsg(e);
  } finally {
    loading.value = false;
  }
});

async function selectSource(s: MediaSource) {
  activeSource.value = s;
  activeSort.value = null;
  items.value = [];
  warning.value = "";
  loading.value = true;
  try {
    sorts.value = await api.mediaCatalogSorts(s.id);
    if (sorts.value.length) {
      // 默认选中「搜索」分类前的第一个真实分类
      activeSort.value = sorts.value[0];
      await browse(activeSort.value, 1);
    }
  } catch (e) {
    error.value = errMsg(e);
  } finally {
    loading.value = false;
  }
}

async function browse(sort: MediaSort, page = 1) {
  activeSort.value = sort;
  searching.value = false;
  loading.value = true;
  try {
    const res: MediaCatalogPageRes = await api.mediaCatalogPage(activeSource.value!.id, sort, page);
    items.value = res.items;
    warning.value = res.warning;
    error.value = "";
  } catch (e) {
    error.value = errMsg(e);
  } finally {
    loading.value = false;
  }
}

async function doSearch() {
  const q = searchQ.value.trim();
  if (!activeSource.value || !q) return;
  searching.value = true;
  loading.value = true;
  try {
    const res = await api.mediaCatalogSearch(activeSource.value.id, q, 1);
    items.value = res.items;
    warning.value = res.warning;
    error.value = "";
  } catch (e) {
    error.value = errMsg(e);
  } finally {
    loading.value = false;
  }
}

const notice = ref("");
const savingKey = ref("");
async function addItem(it: MediaCatalogItem) {
  if (!activeSource.value) return;
  savingKey.value = it.itemKey;
  try {
    const r = await api.mediaLibraryAdd({
      sourceId: activeSource.value.id,
      item: it as unknown as Record<string, unknown>,
      mediaKind: it.mediaKind,
    });
    notice.value = r.existed ? "已在媒体库中" : "已加入媒体库";
  } catch (e) {
    notice.value = errMsg(e);
  } finally {
    savingKey.value = "";
  }
}

function openDetail(it: MediaCatalogItem) {
  router.push({ path: "/media/discover", query: { s: it.sourceId } });
}
</script>

<template>
  <div class="media-discover">
    <div class="page-head">
      <div>
        <h2 class="page-title">媒体发现</h2>
        <p class="page-sub">选择媒体源，浏览分类并收藏到媒体库</p>
      </div>
    </div>

    <div v-if="loading && !sources.length" class="center"><MiuixProgressIndicator /></div>
    <div v-else-if="error && !sources.length" class="center err">{{ error }}</div>

    <template v-else>
      <!-- 分类过滤 -->
      <div class="sort-keys kind-filter">
        <button type="button" class="chip small sort-chip" :class="{ selected: kindFilter === '' }"
          @click="kindFilter = ''">全部</button>
        <button v-for="k in KINDS" :key="k" type="button" class="chip small sort-chip"
          :class="{ selected: kindFilter === k }" @click="kindFilter = k">
          {{ k === "comic" ? "漫画" : k === "audio" ? "音频" : "视频" }}
        </button>
      </div>

      <!-- 搜索框 -->
      <div class="search-row">
        <input v-model="searchQ" class="search-input" placeholder="搜索媒体标题…" @keydown.enter="doSearch" />
        <MiuixButton type="primary" @click="doSearch">搜索</MiuixButton>
      </div>

      <!-- 源选择 -->
      <div v-if="visibleSources.length" class="src-list">
        <button
          v-for="s in visibleSources"
          :key="s.id"
          type="button"
          class="src-chip"
          :class="{ active: activeSource?.id === s.id }"
          @click="selectSource(s)"
        >
          <span class="src-dot" :data-kind="s.mediaKind || ''"></span>{{ s.sourceName }}
        </button>
      </div>
      <div v-else class="empty-state">
        <span class="es-title">还没有媒体源</span>
        <span>媒体源由管理员在「管理 → 媒体源」导入，导入后即可浏览收藏。</span>
      </div>

      <!-- 源内分类 -->
      <div v-if="activeSource && sorts.length" class="sort-keys src-sorts">
        <button
          v-for="srt in sorts"
          :key="srt.url"
          type="button"
          class="chip small sort-chip"
          :class="{ selected: activeSort?.url === srt.url }"
          @click="browse(srt)"
        >{{ srt.name || "全部" }}</button>
      </div>

      <div v-if="loading && sources.length" class="center"><MiuixProgressIndicator /></div>
      <div v-else-if="error && sources.length" class="center err">{{ error }}</div>
      <div v-else-if="warning" class="hint">{{ warning }}</div>
      <div v-else-if="!items.length" class="empty-state">
        <span class="es-title">{{ searching ? "没有搜索结果" : "这个分类是空的" }}</span>
        <span>{{ searching ? "换个关键词再试试。" : "换个分类看看。" }}</span>
      </div>
      <div v-else class="cover-grid">
        <MediaCoverCard
          v-for="it in items"
          :key="it.itemKey"
          :item="it"
          mode="catalog"
          @open="addItem(it)"
          @add="addItem(it)"
          @detail="openDetail(it)"
        />
      </div>
    </template>

    <MiuixDialog :model-value="!!notice" title="提示" @update:model-value="(v:boolean)=>{ if(!v) notice='' }">
      <div class="dlg"><p class="dlg-text">{{ notice }}</p></div>
      <div class="dlg-actions"><MiuixButton type="primary" @click="notice=''">我知道了</MiuixButton></div>
    </MiuixDialog>
  </div>
</template>

<style scoped>
.kind-filter { margin-bottom: 12px; display: flex; flex-wrap: wrap; gap: 8px; }
.sort-keys { display: flex; flex-wrap: wrap; gap: 8px; }
.sort-chip { padding: 5px 13px; font-size: 12px; }
.search-row { display: flex; gap: 8px; margin: 4px 0 14px; }
.search-input {
  flex: 1; min-width: 0; padding: 8px 12px; border-radius: var(--app-shape-md, 10px);
  border: 1px solid var(--app-color-outline-variant, #e0e0e0);
  background: var(--app-color-surface-2, #fff); color: var(--app-color-text, #111);
}
.src-list { display: flex; flex-wrap: wrap; gap: 8px; margin-bottom: 14px; }
.src-chip {
  display: inline-flex; align-items: center; gap: 6px; padding: 6px 12px;
  border-radius: var(--app-shape-full, 999px); border: 1px solid var(--app-color-outline-variant, #e0e0e0);
  background: var(--app-color-surface-2, #fff); cursor: pointer; font-size: 13px;
}
.src-chip.active {
  background: var(--app-color-primary-container, #eaddff); border-color: transparent;
  color: var(--app-color-on-primary-container, #21005d); font-weight: 600;
}
.src-dot { width: 7px; height: 7px; border-radius: 50%; }
.src-dot[data-kind="comic"] { background: #d69cf6; }
.src-dot[data-kind="audio"] { background: #6ecbf5; }
.src-dot[data-kind="video"] { background: #f6a8a8; }
.src-sorts { margin-bottom: 14px; }
.hint { padding: 10px 14px; border-radius: var(--app-shape-md, 10px);
  background: var(--app-color-surface-variant, #f5f5f7); font-size: 13px;
  color: var(--app-color-text-muted, #777); }
</style>