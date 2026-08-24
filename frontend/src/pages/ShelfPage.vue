<script setup lang="ts">
import { onMounted, ref } from "vue";
import { useRouter } from "vue-router";
import { MiuixCard, MiuixButton, MiuixProgressIndicator, MiuixText } from "miuix-vue";
import { api, errMsg, coverProxyUrl } from "@/api/client";
import type { ShelfEntry } from "@/api/client";
import { FALLBACK_COVER_SVG, onCoverError } from "@/utils/cover";
import { openReader } from "@/utils/reader";

const $router = useRouter();

const items = ref<ShelfEntry[]>([]);
const loading = ref(true);
const error = ref("");

onMounted(load);

async function load() {
  loading.value = true;
  try {
    items.value = (await api.shelf()).items;
  } catch (e) {
    error.value = errMsg(e);
  } finally {
    loading.value = false;
  }
}

async function remove(it: ShelfEntry) {
  if (!confirm(`把《${it.name}》移出书架？`)) return;
  try {
    await api.shelfRemove(it.id);
    await load();
  } catch (e) {
    alert(errMsg(e));
  }
}

function openReaderPage(it: ShelfEntry) {
  void openReader($router, {
    sourceUrl: it.sourceUrl,
    bookUrl: it.bookUrl,
    name: it.name ?? "",
    author: it.author ?? "",
    coverUrl: it.coverUrl ?? "",
    intro: it.intro ?? "",
    lastChapter: it.lastChapter ?? "",
    tocUrl: it.tocUrl ?? "",
  });
}

async function refreshToc(it: ShelfEntry) {
  try {
    const r = await api.shelfRefreshToc(it.id);
    if (!r.ok && r.message) alert(r.message);
    await load();
  } catch (e) {
    alert(errMsg(e));
  }
}
</script>

<template>
  <div>
    <div class="bar">
      <h2 class="page-title">我的书架</h2>
      <MiuixButton @click="$router.push('/search')">去搜索添加</MiuixButton>
    </div>

    <div v-if="loading" class="center"><MiuixProgressIndicator /></div>
    <div v-else-if="error" class="center err">{{ error }}</div>
    <div v-else-if="!items.length" class="center empty">
      书架空空如也，先去搜索一本吧。
    </div>

    <div class="grid" v-else>
      <MiuixCard
        v-for="it in items"
        :key="it.id"
        class="entry"
        @click="openReaderPage(it)"
      >
        <img
          class="cover"
          :src="it.coverUrl ? coverProxyUrl(it.coverUrl) : FALLBACK_COVER_SVG"
          loading="lazy"
          @error="onCoverError($event, it.coverUrl)"
        >
        </img>
        <div class="meta">
          <MiuixText type="title4">{{ it.name }}</MiuixText>
          <div class="author">{{ it.author }}</div>
          <div v-if="it.toc && it.toc.status === 'running'" class="toc st-wait">
            目录抓取中…
          </div>
          <div v-else-if="it.toc && it.toc.status === 'queued'" class="toc st-wait">
            目录排队中…
          </div>
          <div
            v-else-if="it.toc && it.toc.status === 'error'"
            class="toc st-err"
            :title="it.toc.error"
          >
            目录抓取失败
            <button class="retry" @click.stop="refreshToc(it)">重试</button>
          </div>
          <div v-else-if="it.toc?.chapters" class="toc dim">
            共 {{ it.toc.chapters }} 章
          </div>
          <div v-if="it.progress?.chapterTitle" class="prog">
            读到：{{ it.progress.chapterTitle }}
            <span v-if="it.progress.updatedAt" class="time">
              · {{ it.progress.updatedAt.slice(5, 16).replace("T", " ") }}
            </span>
          </div>
          <button class="rm" @click.stop="remove(it)">移出书架</button>
        </div>
      </MiuixCard>
    </div>
  </div>
</template>

<style scoped>
/* MiuixCard 结构：.entry 是透明壳，可见卡片是内层 .m-card */
.entry {
  cursor: pointer;
}
.entry :deep(.m-card) {
  flex-direction: row;
  gap: 12px;
}
.grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(260px, 1fr));
  gap: 14px;
}
.cover {
  width: 80px;
  height: 108px;
  border-radius: 10px;
  object-fit: cover;
  background: var(--m-color-surface-container-high);
  flex: none;
}
.meta {
  display: flex;
  flex-direction: column;
  gap: 4px;
  min-width: 0;
  flex: 1;
}
.meta :deep(.m-text--title4) {
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
  overflow: hidden;
}
.author {
  font-size: 13px;
  color: var(--m-color-on-surface-secondary);
}
.prog {
  font-size: 12px;
  color: var(--m-color-on-background-variant);
}
.toc {
  font-size: 12px;
}
.st-wait {
  color: var(--m-color-primary);
}
.st-err {
  color: var(--m-color-error);
}
.st-err .retry {
  border: 0;
  background: none;
  color: var(--m-color-primary);
  cursor: pointer;
  font-size: 12px;
  padding: 0;
  text-decoration: underline;
}
.dim {
  color: var(--m-color-on-background-variant);
}
.time {
  font-size: 11px;
}
.rm {
  margin-top: auto;
  align-self: flex-start;
  border: 0;
  background: none;
  color: var(--m-color-error);
  font-size: 12px;
  cursor: pointer;
  padding: 0;
}
</style>
