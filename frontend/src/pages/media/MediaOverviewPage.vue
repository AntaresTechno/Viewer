<script setup lang="ts">
import { onMounted, ref } from "vue";
import { useRouter } from "vue-router";
import { MiuixButton, MiuixProgressIndicator } from "miuix-vue";
import { api, errMsg } from "@/api/client";
import type { MediaKind, MediaOverview } from "@/api/client";
import MediaCoverCard from "@/components/media/MediaCoverCard.vue";

const router = useRouter();
const loading = ref(true);
const error = ref("");
const data = ref<MediaOverview | null>(null);

const KIND_META: { key: MediaKind; label: string; to: string }[] = [
  { key: "comic", label: "漫画", to: "/media/comic" },
  { key: "audio", label: "音频", to: "/media/audio" },
  { key: "video", label: "视频", to: "/media/video" },
];

onMounted(load);

async function load() {
  try {
    data.value = await api.mediaOverview();
    error.value = "";
  } catch (e) {
    error.value = errMsg(e);
  } finally {
    loading.value = false;
  }
}

function openItem(id: number, kind: MediaKind) {
  if (kind === "comic") router.push(`/media/comic-read/${id}`);
  else router.push(`/media/play/${id}`);
}
function openDetail(id: number) {
  router.push(`/media/detail/${id}`);
}
</script>

<template>
  <div class="media-ov">
    <div class="page-head">
      <div>
        <h2 class="page-title">媒体库</h2>
        <p v-if="data" class="page-sub">共 {{ data.counts.total }} 部 · 分类浏览与最近阅览</p>
      </div>
      <MiuixButton @click="$router.push('/media/discover')">去发现添加</MiuixButton>
    </div>

    <div v-if="loading" class="center"><MiuixProgressIndicator /></div>
    <div v-else-if="error" class="center err">{{ error }}</div>

    <div v-else-if="data" class="ov-body">
      <!-- 三类统计 -->
      <div class="stat-row">
        <button
          v-for="k in KIND_META"
          :key="k.key"
          type="button"
          class="stat-card"
          @click="$router.push(k.to)"
        >
          <span class="stat-num">{{ data.counts[k.key] }}</span>
          <span class="stat-label">{{ k.label }}</span>
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6"
               stroke-linecap="round" stroke-linejoin="round" class="stat-arrow" aria-hidden="true">
            <path d="M9 6l6 6-6 6"/>
          </svg>
        </button>
      </div>

      <!-- 最近阅览 -->
      <h3 class="ov-sec">最近阅览</h3>
      <div v-if="data.recent.length" class="cover-grid">
        <MediaCoverCard
          v-for="it in data.recent"
          :key="it.id"
          :item="it"
          mode="library"
          @open="openItem(it.id, it.mediaKind)"
          @read="openItem(it.id, it.mediaKind)"
          @detail="openDetail(it.id)"
          @remove="openDetail(it.id)"
        />
      </div>
      <div v-else class="empty-state small">
        <span class="es-title">还没有阅览记录</span>
        <span class="es-act"><MiuixButton @click="$router.push('/media/discover')">去发现添加</MiuixButton></span>
      </div>

      <!-- 有更新 -->
      <h3 v-if="data.updates.length" class="ov-sec">有更新</h3>
      <div v-if="data.updates.length" class="cover-grid">
        <MediaCoverCard
          v-for="it in data.updates"
          :key="it.id"
          :item="it"
          mode="library"
          @open="openItem(it.id, it.mediaKind)"
          @read="openItem(it.id, it.mediaKind)"
          @detail="openDetail(it.id)"
          @remove="openDetail(it.id)"
        />
      </div>

      <!-- 最近添加 -->
      <h3 v-if="data.recentlyAdded.length" class="ov-sec">最近添加</h3>
      <div v-if="data.recentlyAdded.length" class="cover-grid">
        <MediaCoverCard
          v-for="it in data.recentlyAdded"
          :key="it.id"
          :item="it"
          mode="library"
          @open="openItem(it.id, it.mediaKind)"
          @read="openItem(it.id, it.mediaKind)"
          @detail="openDetail(it.id)"
          @remove="openDetail(it.id)"
        />
      </div>
    </div>
  </div>
</template>

<style scoped>
.stat-row {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(150px, 1fr));
  gap: 12px;
  margin: 4px 0 20px;
}
.stat-card {
  position: relative;
  display: flex;
  flex-direction: column;
  gap: 4px;
  padding: 16px 18px;
  border-radius: var(--app-shape-lg, 16px);
  background: var(--app-color-surface-2, #fff);
  border: 1px solid var(--app-color-outline-variant, #e0e0e0);
  text-align: left;
  cursor: pointer;
}
.stat-num {
  font-size: 28px;
  font-weight: 700;
  color: var(--app-color-primary, #6750a4);
}
.stat-label {
  font-size: 13px;
  color: var(--app-color-text-muted, #666);
}
.stat-arrow {
  position: absolute;
  right: 12px;
  top: 50%;
  transform: translateY(-50%);
  width: 18px;
  height: 18px;
  color: var(--app-color-text-muted, #999);
}
.ov-sec {
  margin: 22px 0 12px;
  font-size: 15px;
  font-weight: 650;
}
.empty-state.small {
  padding: 22px;
}
</style>