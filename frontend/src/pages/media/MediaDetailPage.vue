<script setup lang="ts">
import { onMounted, ref } from "vue";
import { useRouter } from "vue-router";
import { MiuixButton, MiuixProgressIndicator } from "miuix-vue";
import { api, errMsg, coverProxyUrl } from "@/api/client";
import type { MediaLibraryItem, MediaUnit } from "@/api/client";
import { FALLBACK_COVER_SVG, onCoverError } from "@/utils/cover";

const props = defineProps<{ id: string }>();
const router = useRouter();

const item = ref<MediaLibraryItem | null>(null);
const units = ref<MediaUnit[]>([]);
const loading = ref(true);
const error = ref("");
const refreshing = ref(false);
const notice = ref("");

const KIND_LABEL: Record<string, string> = { comic: "漫画", audio: "音频", video: "视频" };

async function load() {
  try {
    item.value = await api.mediaLibraryGet(Number(props.id));
    error.value = "";
    try {
      units.value = await api.mediaLibraryUnits(Number(props.id));
    } catch {
      units.value = [];
    }
  } catch (e) {
    error.value = errMsg(e);
  } finally {
    loading.value = false;
  }
}
onMounted(load);

function openUnit(u?: MediaUnit) {
  if (!item.value) return;
  const q = u ? `?unit=${encodeURIComponent(u.unitKey)}` : "";
  if (item.value.mediaKind === "comic") router.push(`/media/comic-read/${item.value.id}${q}`);
  else router.push(`/media/play/${item.value.id}${q}`);
}

async function refreshItem() {
  if (!item.value || refreshing.value) return;
  refreshing.value = true;
  try {
    const r = await api.mediaLibraryRefresh(item.value.id);
    notice.value = r.changed ? "检测到内容更新" : "已是最新";
    await load();
  } catch (e) {
    notice.value = errMsg(e);
  } finally {
    refreshing.value = false;
  }
}

function openSources() {
  router.push({ path: "/media/discover" });
}
</script>

<template>
  <div class="media-detail">
    <div v-if="loading" class="center"><MiuixProgressIndicator /></div>
    <div v-else-if="error" class="center err">{{ error }}</div>
    <template v-else-if="item">
      <div class="hero">
        <div class="hero-cover">
          <img :src="item.coverUrl ? coverProxyUrl(item.coverUrl) : FALLBACK_COVER_SVG"
               alt="" @error="onCoverError($event, item.coverUrl)" />
        </div>
        <div class="hero-info">
          <div class="hero-tags">
            <span class="kind-badge" :data-kind="item.mediaKind">{{ KIND_LABEL[item.mediaKind] }}</span>
            <span v-if="item.hasUpdate" class="upd-badge chip">有更新</span>
          </div>
          <h1 class="hero-title">{{ item.title }}</h1>
          <p v-if="item.creator" class="hero-creator">{{ item.creator }} · {{ item.sourceName }}</p>
          <p v-else class="hero-creator">{{ item.sourceName }}</p>
          <p v-if="item.intro" class="hero-intro">{{ item.intro }}</p>
          <div class="hero-actions">
            <MiuixButton type="primary" @click="openUnit()">
              {{ units.length ? (item.mediaKind === 'comic' ? '开始阅读' : '开始播放') : '兼容播放器' }}
            </MiuixButton>
            <MiuixButton :disabled="refreshing" @click="refreshItem">
              {{ refreshing ? "检查中…" : "检查更新" }}
            </MiuixButton>
            <MiuixButton variant="text" @click="openSources">去发现更多</MiuixButton>
          </div>
        </div>
      </div>

      <div v-if="units.length" class="units">
        <h3 class="units-title">{{ item.mediaKind === "comic" ? "章节" : "分集" }}</h3>
        <div class="unit-list">
          <button
            v-for="u in units"
            :key="u.unitKey"
            type="button"
            class="unit-btn"
            :class="{ done: item.progress?.unitKey === u.unitKey && item.progress?.completed }"
            @click="openUnit(u)"
          >
            <span class="unit-idx">{{ String(u.index + 1).padStart(2, "0") }}</span>
            <span class="unit-name">{{ u.title || `第 ${u.index + 1} 集` }}</span>
            <svg v-if="item.progress?.unitKey === u.unitKey" viewBox="0 0 24 24" class="ti-check"
                 fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"
                 stroke-linejoin="round" aria-hidden="true"><path d="M5 13l4 4L19 7"/></svg>
          </button>
        </div>
      </div>

      <div v-else class="legacy-note">
        <p>该媒体源使用<b>旧式 HTML 播放器</b>。分集与播放都发生在隔离的兼容播放器里，
          主站登录信息不会被该源读取。点击「兼容播放器」开始。</p>
      </div>
    </template>

    <MiuixDialog :model-value="!!notice" title="提示" @update:model-value="(v:boolean)=>{ if(!v) notice='' }">
      <div class="dlg"><p class="dlg-text">{{ notice }}</p></div>
      <div class="dlg-actions"><MiuixButton type="primary" @click="notice=''">我知道了</MiuixButton></div>
    </MiuixDialog>
  </div>
</template>

<style scoped>
.hero { display: flex; gap: 20px; margin-bottom: 26px; }
.hero-cover { width: 180px; height: 252px; flex: 0 0 auto; border-radius: var(--app-shape-lg, 16px); overflow: hidden;
  box-shadow: var(--app-shadow-1, 0 2px 10px rgba(0,0,0,.12)); }
.hero-cover img { width: 100%; height: 100%; object-fit: cover; display: block; }
.hero-info { min-width: 0; flex: 1; }
.hero-tags { display: flex; gap: 8px; margin-bottom: 10px; }
.kind-badge { padding: 2px 10px; border-radius: var(--app-shape-full, 999px); font-size: 12px; font-weight: 600; }
.kind-badge[data-kind="comic"] { background: rgba(214,156,246,.3); color: #7c3aed; }
.kind-badge[data-kind="audio"] { background: rgba(110,203,245,.3); color: #0369a1; }
.kind-badge[data-kind="video"] { background: rgba(246,168,168,.35); color: #b91c1c; }
.hero-title { font-size: 26px; font-weight: 750; margin: 0 0 6px; }
.hero-creator { color: var(--app-color-text-muted, #777); margin: 0 0 10px; font-size: 14px; }
.hero-intro { color: var(--app-color-text-secondary, #444); font-size: 14px; line-height: 1.6;
  max-height: 96px; overflow: auto; margin: 0 0 16px; }
.hero-actions { display: flex; flex-wrap: wrap; gap: 8px; }
.units-title { font-size: 15px; font-weight: 650; margin: 0 0 10px; }
.unit-list { display: flex; flex-direction: column; gap: 6px; }
.unit-btn { display: flex; align-items: center; gap: 10px; padding: 10px 14px;
  border-radius: var(--app-shape-md, 10px); border: 1px solid var(--app-color-outline-variant, #e0e0e0);
  background: var(--app-color-surface-2, #fff); cursor: pointer; text-align: left; }
.unit-btn:active { transform: scale(.995); }
.unit-btn.done { color: var(--app-color-primary, #6750a4); }
.unit-idx { font-weight: 700; color: var(--app-color-text-muted, #999); width: 26px; }
.unit-name { flex: 1; min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.ti-check { width: 16px; height: 16px; color: var(--app-color-primary, #6750a4); }
.legacy-note { padding: 14px 16px; border-radius: var(--app-shape-md, 12px);
  background: var(--app-color-surface-variant, #f5f5f7); font-size: 13.5px; line-height: 1.6; }
</style>