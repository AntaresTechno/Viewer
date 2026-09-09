<script setup lang="ts">
import { computed, onMounted, ref } from "vue";
import { MiuixButton, MiuixProgressIndicator } from "miuix-vue";
import { api, errMsg } from "@/api/client";
import type { MediaLibraryItem, MediaResolveResult, MediaUnit } from "@/api/client";
import LegacyMediaFrame from "@/components/media/LegacyMediaFrame.vue";

const props = defineProps<{ id: string }>();

const item = ref<MediaLibraryItem | null>(null);
const units = ref<MediaUnit[]>([]);
const loading = ref(true);
const error = ref("");

const resolved = ref<MediaResolveResult | null>(null);
const images = ref<{ url: string; headers?: Record<string, string> }[]>([]);
const pageIndex = ref(0);
const mode = ref<"native" | "legacy">("native");
const legacyDoc = ref<{ html: string; iframeKey: string; warning: string; restore: Record<string, unknown> } | null>(null);

const currentUnitKey = ref("");
const totalPages = computed(() => images.value.length);
const isFirst = computed(() => pageIndex.value === 0);
const isLast = computed(() => pageIndex.value >= totalPages.value - 1);

async function load() {
  loading.value = true;
  try {
    item.value = await api.mediaLibraryGet(Number(props.id));
    try { units.value = await api.mediaLibraryUnits(Number(props.id)); } catch { units.value = []; }
    const routeUnit = new URLSearchParams(location.search).get("unit") || item.value.progress?.unitKey || units.value[0]?.unitKey || "";
    currentUnitKey.value = routeUnit || units.value[0]?.unitKey || "";
    if (currentUnitKey.value) await openUnit(currentUnitKey.value);
    else { loading.value = false; error.value = "没有可读章节"; }
  } catch (e) {
    error.value = errMsg(e);
    loading.value = false;
  }
}
onMounted(load);

async function openUnit(unitKey: string) {
  currentUnitKey.value = unitKey;
  loading.value = true;
  mode.value = "native";
  images.value = [];
  pageIndex.value = 0;
  try {
    const res = await api.mediaResolve(Number(props.id), unitKey);
    resolved.value = res;
    if (res.mode === "legacy") {
      mode.value = "legacy";
      legacyDoc.value = await api.mediaLegacyDoc(Number(props.id));
      return;
    }
    images.value = res.images || [];
    if (item.value?.progress && item.value.progress.unitKey === unitKey) {
      pageIndex.value = Math.min(item.value.progress.pageIndex || 0, Math.max(0, images.value.length - 1));
    }
    error.value = res.warning || "";
  } catch (e) {
    error.value = errMsg(e);
  } finally {
    loading.value = false;
  }
}

function flip(delta: number) {
  pageIndex.value = Math.max(0, Math.min(totalPages.value - 1, pageIndex.value + delta));
  void saveProgress();
}
async function saveProgress() {
  if (!item.value) return;
  try {
    await api.mediaProgressPut(item.value.id, {
      unitKey: currentUnitKey.value,
      pageIndex: pageIndex.value,
      pageCount: totalPages.value,
      completed: pageIndex.value >= totalPages.value - 1,
    } as never);
  } catch { /* 静默 */ }
}
function openUnitByClick(u: MediaUnit) { void openUnit(u.unitKey); }
const imgUrl = computed(() => images.value[pageIndex.value]?.url || "");
</script>

<template>
  <div class="comic-reader">
    <div v-if="loading" class="center"><MiuixProgressIndicator /></div>
    <div v-else-if="error && !images.length && mode !== 'legacy'" class="center err">{{ error }}</div>
    <template v-else-if="item">
      <div class="cr-top">
        <span class="cr-title">{{ item.title }}</span>
        <span class="cr-page" v-if="mode === 'native' && totalPages">{{ pageIndex + 1 }} / {{ totalPages }}</span>
        <MiuixButton variant="text" @click="$router.push(`/media/detail/${item.id}`)">详情</MiuixButton>
      </div>

      <!-- 原创漫画阅读 -->
      <div v-if="mode === 'native' && images.length" class="comic-stage">
        <button type="button" class="page-btn prev" :disabled="isFirst" aria-label="上一页" @click="flip(-1)">
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M15 6l-6 6 6 6"/></svg>
        </button>
        <div class="comic-view" @click="flip(1)">
          <img :src="imgUrl" :alt="`第 ${pageIndex + 1} 页`" class="comic-img" />
        </div>
        <button type="button" class="page-btn next" :disabled="isLast" aria-label="下一页" @click="flip(1)">
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M9 6l6 6-6 6"/></svg>
        </button>
      </div>

      <!-- 兼容阅读 -->
      <div v-else-if="mode === 'legacy' && legacyDoc" class="stage-legacy">
        <LegacyMediaFrame :html="legacyDoc.html" :iframe-key="legacyDoc.iframeKey" :restore="legacyDoc.restore" />
        <p v-if="legacyDoc.warning" class="warn">{{ legacyDoc.warning }}</p>
      </div>

      <div v-else-if="!images.length && !loading" class="empty-state">
        <span class="es-title">这一话没有可读页面</span>
        <span>可能是源不支持原生漫画阅读。</span>
      </div>

      <!-- 章节条 -->
      <div v-if="units.length" class="unit-wrap">
        <button
          v-for="u in units"
          :key="u.unitKey"
          type="button"
          class="unit-chip"
          :class="{ active: u.unitKey === currentUnitKey }"
          @click="openUnitByClick(u)"
        >{{ u.title || `第 ${u.index + 1} 话` }}</button>
      </div>
    </template>
  </div>
</template>

<style scoped>
.comic-reader { display: flex; flex-direction: column; gap: 12px; }
.cr-top { display: flex; align-items: center; gap: 12px; }
.cr-title { font-weight: 700; font-size: 16px; flex: 1; min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.cr-page { color: var(--app-color-text-muted, #777); font-size: 13px; }
.comic-stage { display: flex; align-items: center; gap: 8px; }
.comic-view { flex: 1; display: grid; place-items: center; background: #121212; border-radius: var(--app-shape-lg, 14px); overflow: hidden; }
.comic-img { max-width: 100%; max-height: 70vh; object-fit: contain; }
.page-btn { width: 40px; height: 40px; border-radius: 50%; display: grid; place-items: center;
  border: 1px solid var(--app-color-outline-variant, #e0e0e0); background: var(--app-color-surface-2, #fff);
  cursor: pointer; color: var(--app-color-text, #111); }
.page-btn:disabled { opacity: .3; cursor: default; }
.page-btn svg { width: 20px; height: 20px; }
.stage-legacy { height: 70vh; border-radius: var(--app-shape-lg, 14px); overflow: hidden; }
.warn { padding: 8px 12px; font-size: 12.5px; color: var(--app-color-text-muted, #888); }
.unit-wrap { display: flex; flex-wrap: wrap; gap: 8px; }
.unit-chip { padding: 6px 11px; border-radius: var(--app-shape-full, 999px); border: 1px solid var(--app-color-outline-variant, #e0e0e0);
  background: var(--app-color-surface-2, #fff); cursor: pointer; font-size: 12.5px; }
.unit-chip.active { background: var(--app-color-primary, #6750a4); color: #fff; border-color: transparent; }
</style>