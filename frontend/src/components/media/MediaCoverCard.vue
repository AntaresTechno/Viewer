<script setup lang="ts">
import { computed } from "vue";
import { MiuixProgressIndicator } from "miuix-vue";
import { coverProxyUrl } from "@/api/client";
import type { MediaLibraryItem, MediaCatalogItem } from "@/api/client";
import { FALLBACK_COVER_SVG, onCoverError } from "@/utils/cover";

/** 统一媒体瓷砖：封面优先，复用全局 .ctile 样式；可切换目录/媒体库两种模式。 */
const props = defineProps<{
  item: MediaLibraryItem | MediaCatalogItem;
  mode?: "catalog" | "library";
  loading?: boolean;
}>();

const emit = defineEmits<{
  (e: "open"): void;
  (e: "detail"): void;
  (e: "remove"): void;
  (e: "add"): void;
  (e: "read"): void;
}>();

const title = computed(() => props.item.title || "未命名");
const cover = computed(() => coverProxyUrl(props.item.coverUrl || ""));

const isLibrary = computed(() => props.mode === "library");

/** 媒体库进度文本（不同种类的进度语义不同）。 */
function progressText(it: MediaLibraryItem): string {
  const p = it.progress;
  if (!p) return it.latestUnit ? `更新到 ${it.latestUnit}` : "未开始";
  if (it.mediaKind === "comic") {
    if (p.pageCount > 0) return `读到第 ${p.pageIndex + 1} 页 / ${p.pageCount}`;
    return p.pageIndex > 0 ? `已读 ${p.pageIndex + 1} 页` : "未开始";
  }
  if (p.durationMs > 0) {
    const pct = Math.min(100, Math.round((p.positionMs / p.durationMs) * 100));
    return p.completed ? "看完" : `进度 ${pct}%`;
  }
  return p.completed ? "看完" : "未开始";
}

function percent(it: MediaLibraryItem): number | null {
  const p = it.progress;
  if (!p) return null;
  if (it.mediaKind === "comic" && p.pageCount > 0) {
    return Math.min(100, Math.round(((p.pageIndex + 1) / p.pageCount) * 100));
  }
  if (p.durationMs > 0) return Math.min(100, Math.round((p.positionMs / p.durationMs) * 100));
  return null;
}
</script>

<template>
  <div class="ctile media-tile" role="button" tabindex="0" @click="emit('open')">
    <span class="ctile-cover">
      <span v-if="loading" class="ctile-loading"><MiuixProgressIndicator /></span>
      <img
        v-else
        class="media-cover-img"
        :src="cover || FALLBACK_COVER_SVG"
        loading="lazy"
        alt=""
        @error="onCoverError($event, props.item.coverUrl)"
      />
      <span v-if="isLibrary && (item as MediaLibraryItem).hasUpdate" class="ctile-badge upd-badge"
        >有更新</span
      >
    </span>

    <span class="ctile-name">{{ title }}</span>
    <span class="ctile-sub">
      <template v-if="mode === 'library'">
        {{ progressText(item as MediaLibraryItem) }}
      </template>
      <template v-else>
        {{ (item as MediaCatalogItem).creator || (item as MediaCatalogItem).mediaKind }}
      </template>
    </span>
    <span v-if="mode === 'library'" class="ctile-progress" :class="{ idle: percent(item as MediaLibraryItem) == null }">
      <i :style="{ width: `${percent(item as MediaLibraryItem) ?? 0}%` }" />
    </span>

    <span class="tile-ops">
      <span class="tile-ops-left">
        <button v-if="isLibrary" type="button" class="tbtn" @click.stop="emit('read')">
          继续
        </button>
        <button type="button" class="tbtn" @click.stop="emit('detail')">详情</button>
      </span>
      <button
        v-if="isLibrary"
        type="button"
        class="tbtn danger"
        @click.stop="emit('remove')"
      >移出</button>
      <button v-else type="button" class="tbtn" @click.stop="emit('add')">收藏</button>
    </span>
  </div>
</template>

<style scoped>
.media-tile {
  isolation: isolate;
}
.ctile-loading {
  position: absolute;
  inset: 0;
  display: grid;
  place-items: center;
  background: color-mix(in srgb, var(--app-color-surface-2, #fff) 60%, transparent);
}
.media-cover-img {
  width: 100%;
  height: 100%;
  object-fit: cover;
  display: block;
}
</style>