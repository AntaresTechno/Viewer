<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from "vue";
import { MiuixButton, MiuixProgressIndicator } from "miuix-vue";
import { api, errMsg } from "@/api/client";
import type {
  MediaLibraryItem,
  MediaLegacyDoc,
  MediaResolveResult,
  MediaUnit,
} from "@/api/client";
import LegacyMediaFrame from "@/components/media/LegacyMediaFrame.vue";

const props = defineProps<{ id: string }>();

const item = ref<MediaLibraryItem | null>(null);
const units = ref<MediaUnit[]>([]);
const loading = ref(true);
const error = ref("");

const mode = ref<"native" | "legacy">("native");
const resolved = ref<MediaResolveResult | null>(null);

// native controls
const videoEl = ref<HTMLVideoElement | null>(null);
const audioEl = ref<HTMLAudioElement | null>(null);
const positionMs = ref(0);
const durationMs = ref(0);
const playing = ref(false);

// legacy
const legacyDoc = ref<MediaLegacyDoc | null>(null);
const legacyFrameKey = ref(0);

const currentUnitKey = ref("");
const notice = ref("");

const currentUnitIndex = computed(() =>
  units.value.findIndex((u) => u.unitKey === currentUnitKey.value));

const hasPrev = computed(() => currentUnitIndex.value > 0);
const hasNext = computed(() =>
  currentUnitIndex.value >= 0 && currentUnitIndex.value < units.value.length - 1);

async function load() {
  loading.value = true;
  try {
    const it = await api.mediaLibraryGet(Number(props.id));
    item.value = it;
    try {
      units.value = await api.mediaLibraryUnits(Number(props.id));
    } catch {
      units.value = [];
    }
    const routeUnit = new URLSearchParams(location.search).get("unit") ||
      it.progress?.unitKey || "";
    currentUnitKey.value = routeUnit || units.value[0]?.unitKey || "";
    error.value = "";
  } catch (e) {
    error.value = errMsg(e);
  } finally {
    loading.value = false;
  }
}
onMounted(load);
onBeforeUnmount(() => {
  window.removeEventListener("beforeunload", saveOnUnload);
  document.removeEventListener("visibilitychange", onVisible);
  void saveNow();
});

watch(currentUnitKey, (v) => { if (v) void openUnit(v); });

async function openUnit(unitKey: string) {
  currentUnitKey.value = unitKey;
  loading.value = true;
  mode.value = "native";
  resolved.value = null;
  legacyDoc.value = null;
  positionMs.value = 0;
  durationMs.value = 0;
  try {
    const res = await api.mediaResolve(Number(props.id), unitKey);
    resolved.value = res;
    if (res.mode === "legacy") {
      mode.value = "legacy";
      const doc = await api.mediaLegacyDoc(Number(props.id));
      legacyDoc.value = doc;
      legacyFrameKey.value++;
    } else if (res.kind === "comic") {
      // 误入漫画：跳转漫画阅读器
      location.href = `/media/comic-read/${props.id}?unit=${encodeURIComponent(unitKey)}`;
      return;
    }
    error.value = "";
  } catch (e) {
    error.value = errMsg(e);
  } finally {
    loading.value = false;
  }
}

/* ---- 原声播放器 ---- */
function bindNative() {
  const el = videoEl.value || audioEl.value;
  if (!el) return;
  window.removeEventListener("beforeunload", saveOnUnload);
  window.addEventListener("beforeunload", saveOnUnload);
  document.addEventListener("visibilitychange", onVisible);
  el.ontimeupdate = () => { positionMs.value = Math.floor(el.currentTime * 1000); };
  el.ondurationchange = () => {
    durationMs.value = Math.floor((el.duration || 0) * 1000);
    if (startingSeek.value != null) {
      if (el.duration >= startingSeek.value / 1000) el.currentTime = startingSeek.value / 1000;
      startingSeek.value = null;
    }
  };
  el.onplay = () => { playing.value = true; saveSoon(); };
  el.onpause = () => { playing.value = false; saveNow(); };
  el.onended = () => saveCompleted();
}
const startingSeek = ref<number | null>(null);

function seedFromProgress(
  progress: { positionMs: number; durationMs: number; completed?: boolean } | null,
) {
  if (!progress || progress.durationMs === 0) return;
  if (progress.completed) return;
  if (progress.positionMs > 0 && progress.positionMs < progress.durationMs) {
    startingSeek.value = progress.positionMs;
    positionMs.value = progress.positionMs;
  }
}

async function saveNow() {
  const el = videoEl.value || audioEl.value;
  if (el && Number.isFinite(el.currentTime)) {
    positionMs.value = Math.max(positionMs.value, Math.floor(el.currentTime * 1000));
  }
  if (!item.value) return;
  const body: Record<string, unknown> = {
    unitKey: currentUnitKey.value,
    positionMs: positionMs.value,
    durationMs: durationMs.value,
  };
  if (durationMs.value > 0) body.completed = positionMs.value >= durationMs.value * 0.95;
  try { await api.mediaProgressPut(item.value.id, body as never); } catch { /* 静默 */ }
}
function saveSoon() {
  clearTimeout(saveTimer);
  saveTimer = setTimeout(() => void saveNow(), 800);
}
let saveTimer: ReturnType<typeof setTimeout> | undefined = undefined;
function saveOnUnload() { void saveNow(); }
function onVisible() { if (document.visibilityState === "hidden") void saveNow(); }
async function saveCompleted() {
  positionMs.value = durationMs.value;
  if (item.value) {
    await api.mediaProgressPut(item.value.id, {
      unitKey: currentUnitKey.value, positionMs: durationMs.value,
      durationMs: durationMs.value, completed: true,
    } as never);
  }
}

/* ---- legacy 沙箱进度 ---- */
function onLegacyKv(kv: Record<string, unknown>) {
  if (!item.value) return;
  void api.mediaProgressPut(item.value.id, { legacyState: kv } as never).catch(() => {});
}
function onLegacyMedia(msg: { kind: string; s: number; d: number }) {
  if (!item.value) return;
  if (msg.kind === "ended") {
    void api.mediaProgressPut(item.value.id, {
      unitKey: currentUnitKey.value, positionMs: msg.d * 1000,
      durationMs: msg.d * 1000, completed: true, legacyState: legacyDoc.value?.restore || {},
    } as never).catch(() => {});
    return;
  }
  positionMs.value = msg.s * 1000;
  durationMs.value = msg.d * 1000;
  if (msg.d > 0) {
    void api.mediaProgressPut(item.value.id, {
      unitKey: currentUnitKey.value, positionMs: positionMs.value,
      durationMs: durationMs.value,
      legacyState: legacyDoc.value?.restore || {},
    } as never).catch(() => {});
  }
}

function pickUnit(offset: number) {
  const idx = currentUnitIndex.value;
  const target = units.value[idx + offset];
  if (target) void openUnit(target.unitKey);
}

watch(
  () => resolved.value,
  (res) => {
    if (res && res.mode === "native" && hasStream(res)) {
      void nextTick(() => {
        bindNative();
        seedFromProgress(item.value?.progress || null);
      });
    }
  },
);
function hasStream(res: MediaResolveResult): boolean {
  return res.mode === "native" && !!res.streams?.length;
}

function streamUrl(): string {
  const res = resolved.value;
  if (res?.mode === "native" && res.streams[0]) return res.streams[0].url;
  return "";
}
function posterUrl(): string {
  const res = resolved.value;
  return res?.mode === "native" ? res.posterUrl || "" : "";
}

const currentUnitTitle = computed(() => {
  const idx = currentUnitIndex.value;
  if (idx >= 0 && units.value[idx]) {
    return units.value[idx].title || `第 ${idx + 1} 集`;
  }
  return "";
});
</script>

<template>
  <div class="player-page">
    <div v-if="loading" class="center"><MiuixProgressIndicator /></div>
    <div v-else-if="error" class="center err">{{ error }}</div>
    <template v-else-if="item">
      <!-- 顶部信息条 -->
      <div class="player-top">
        <div class="pt-title">
          <span class="pt-kind">{{ item.mediaKind === "audio" ? "音频" : "视频" }}</span>
          <span class="pt-name">{{ item.title }}</span>
          <span v-if="currentUnitTitle" class="pt-unit">{{ currentUnitTitle }}</span>
        </div>
        <div class="pt-ops">
          <MiuixButton variant="text" :disabled="!hasPrev" @click="pickUnit(-1)">上一集</MiuixButton>
          <MiuixButton variant="text" :disabled="!hasNext" @click="pickUnit(1)">下一集</MiuixButton>
        </div>
      </div>

      <!-- 原声播放器 -->
      <div v-if="mode === 'native' && resolved && resolved.mode === 'native'" class="native-wrap">
        <video
          v-if="item.mediaKind !== 'audio'"
          ref="videoEl"
          class="video-el"
          :src="streamUrl()"
          :poster="posterUrl()"
          controls
          playsinline
          autoplay
        ></video>
        <audio v-else ref="audioEl" class="audio-el" :src="streamUrl()" controls autoplay></audio>
        <p v-if="resolved.warning" class="warn-line">{{ resolved.warning }}</p>
      </div>

      <!-- 兼容播放器 -->
      <div v-else-if="mode === 'legacy' && legacyDoc" class="legacy-wrap">
        <LegacyMediaFrame
          :key="`${legacyFrameKey}-${currentUnitKey}`"
          :html="legacyDoc.html"
          :iframe-key="legacyDoc.iframeKey"
          :restore="legacyDoc.restore"
          @kvchange="onLegacyKv"
          @media="onLegacyMedia"
        />
        <p v-if="legacyDoc.warning" class="warn-line">{{ legacyDoc.warning }}</p>
      </div>

      <!-- 分集列表 -->
      <div v-if="units.length" class="ep-row">
        <button
          v-for="u in units"
          :key="u.unitKey"
          type="button"
          class="ep-chip"
          :class="{ active: u.unitKey === currentUnitKey }"
          @click="openUnit(u.unitKey)"
        >{{ u.title || `第 ${u.index + 1} 集` }}</button>
      </div>
    </template>

    <MiuixDialog :model-value="!!notice" title="提示" @update:model-value="(v:boolean)=>{ if(!v) notice='' }">
      <div class="dlg"><p class="dlg-text">{{ notice }}</p></div>
      <div class="dlg-actions"><MiuixButton type="primary" @click="notice=''">我知道了</MiuixButton></div>
    </MiuixDialog>
  </div>
</template>

<style scoped>
.player-page { display: flex; flex-direction: column; gap: 14px; }
.player-top { display: flex; align-items: center; justify-content: space-between; gap: 12px; flex-wrap: wrap; }
.pt-title { display: flex; align-items: baseline; gap: 10px; min-width: 0; }
.pt-kind { font-size: 12px; font-weight: 600; padding: 2px 9px; border-radius: 999px;
  background: rgba(246,168,168,.35); color: #b91c1c; }
.pt-name { font-weight: 700; font-size: 17px; }
.pt-unit { color: var(--app-color-text-muted, #777); font-size: 13.5px; }
.pt-ops { display: flex; gap: 4px; }
.native-wrap { background: #000; border-radius: var(--app-shape-lg, 16px); overflow: hidden; }
.video-el { width: 100%; max-height: 66vh; aspect-ratio: 16/9; background: #000; display: block; }
.audio-el { width: 100%; }
.warn-line { padding: 8px 12px; font-size: 12.5px; color: var(--app-color-text-muted, #888); }
.legacy-wrap { height: 72vh; border-radius: var(--app-shape-lg, 16px); overflow: hidden; }
.ep-row { display: flex; flex-wrap: wrap; gap: 8px; }
.ep-chip { padding: 6px 12px; border-radius: var(--app-shape-full, 999px);
  border: 1px solid var(--app-color-outline-variant, #e0e0e0);
  background: var(--app-color-surface-2, #fff); cursor: pointer; font-size: 13px; }
.ep-chip.active { background: var(--app-color-primary, #6750a4); color: #fff; border-color: transparent; }
</style>