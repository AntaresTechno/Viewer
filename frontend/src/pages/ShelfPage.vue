<script setup lang="ts">
import { onMounted, ref, watch } from "vue";
import { useRouter } from "vue-router";
import { MiuixButton, MiuixDialog, MiuixProgressIndicator } from "miuix-vue";
import { api, errMsg, coverProxyUrl } from "@/api/client";
import type { ShelfEntry, ShelfSort } from "@/api/client";
import { FALLBACK_COVER_SVG, onCoverError } from "@/utils/cover";
import { openDetail, openReader } from "@/utils/reader";

const $router = useRouter();

const items = ref<ShelfEntry[]>([]);
const loading = ref(true);
const error = ref("");

/* 书架排序：加入时间 / 最近更新 / 最后阅读（记忆上次选择） */
const SORTS: { key: ShelfSort; label: string; hint: string }[] = [
  { key: "added", label: "加入时间", hint: "最近加入书架的在前" },
  { key: "updated", label: "最近更新", hint: "书源检测到新章的在前" },
  { key: "read", label: "最后阅读", hint: "最近读过的在前" },
];
const storedSort = localStorage.getItem("shelf_sort") as ShelfSort | null;
const sortKey = ref<ShelfSort>(
  storedSort && SORTS.some((s) => s.key === storedSort) ? storedSort : "added",
);

watch(sortKey, (v) => {
  localStorage.setItem("shelf_sort", v);
  void load();
});

onMounted(load);

async function load() {
  loading.value = true;
  try {
    items.value = (await api.shelf(sortKey.value)).items;
  } catch (e) {
    error.value = errMsg(e);
  } finally {
    loading.value = false;
  }
}

/* 删除确认 / 错误提示：一律走主题化界面内对话框，不用浏览器 confirm/alert */
const delTarget = ref<ShelfEntry | null>(null);
const removing = ref(false);
const notice = ref("");

function askRemove(it: ShelfEntry) {
  delTarget.value = it;
}

async function doRemove() {
  const t = delTarget.value;
  if (!t || removing.value) return;
  removing.value = true;
  try {
    await api.shelfRemove(t.id);
    delTarget.value = null;
    await load();
  } catch (e) {
    notice.value = errMsg(e);
    delTarget.value = null;
  } finally {
    removing.value = false;
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

/** 详情页（书籍档案 / 目录 / 加入书架），与阅读器入口共用同一套参数。 */
function openDetails(it: ShelfEntry) {
  void openDetail($router, {
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
    if (!r.ok && r.message) notice.value = r.message;
    await load();
  } catch (e) {
    notice.value = errMsg(e);
  }
}

/** 阅读进度百分比（有章节总数与章节下标才可算）。 */
function percent(it: ShelfEntry): number | null {
  if (!it.progress || !it.toc?.chapters || it.progress.chapterIndex < 0) return null;
  return Math.min(100, Math.round(((it.progress.chapterIndex + 1) / it.toc.chapters) * 100));
}

/** 副标题一行：进度 > 章节数 > 最近更新；空串时仍占位，保证卡片等高。 */
function subText(it: ShelfEntry): string {
  if (percent(it) != null) return `读到 ${percent(it)!}% · ${it.progress?.chapterTitle ?? ""}`;
  if (it.toc?.chapters) return `共 ${it.toc.chapters} 章 · 未读`;
  return it.lastChapter ? `更新到 ${it.lastChapter}` : "";
}
</script>

<template>
  <div>
    <div class="page-head">
      <div>
        <h2 class="page-title">书架</h2>
        <p v-if="items.length" class="page-sub">{{ items.length }} 本 · 点击封面继续阅读</p>
      </div>
      <MiuixButton @click="$router.push('/search')">去搜索添加</MiuixButton>
    </div>

    <!-- 排序 -->
    <div class="sort-row" role="tablist" aria-label="书架排序">
      <button
        v-for="s in SORTS"
        :key="s.key"
        type="button"
        class="chip small sort-chip"
        :class="{ selected: sortKey === s.key }"
        :title="s.hint"
        @click="sortKey = s.key"
      >{{ s.label }}</button>
    </div>

    <div v-if="loading" class="center"><MiuixProgressIndicator /></div>
    <div v-else-if="error" class="center err">{{ error }}</div>

    <div v-else-if="!items.length" class="empty-state">
      <span class="es-icon">
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.4"
             stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
          <path d="M5.5 20V7.5"/><path d="M10 20V4.5"/><path d="M14.5 20V7.5"/>
          <path d="m18.9 18.6-2.2-11"/><path d="M3.8 20h16.4"/>
        </svg>
      </span>
      <span class="es-title">书架还是空的</span>
      <span>搜一本书加进来，随时接着读。</span>
      <span class="es-act">
        <MiuixButton type="primary" @click="$router.push('/search')">去搜索添加</MiuixButton>
      </span>
    </div>

    <!-- 封面优先的瓷砖网格：点击即续读；详情/删除固定在卡片底部操作排。
         :key 绑定排序键 —— 切换排序时整组重挂载，重放全局瓷砖入场
         错帧，避免几十个封面瞬间大挪移。 -->
    <div class="cover-grid" v-else :key="sortKey">
      <div
        v-for="it in items"
        :key="it.id"
        class="ctile shelf-tile"
        role="button"
        tabindex="0"
        @click="openReaderPage(it)"
        @keydown.enter.prevent="openReaderPage(it)"
      >
        <span class="ctile-cover">
          <img
            :src="it.coverUrl ? coverProxyUrl(it.coverUrl) : FALLBACK_COVER_SVG"
            loading="lazy"
            @error="onCoverError($event, it.coverUrl)"
          >
          <span
            v-if="it.hasUpdate"
            class="ctile-badge upd-badge"
            title="目录刷新检测到新章节"
          >有更新</span>
          <span
            v-else-if="it.toc && it.toc.status === 'running'"
            class="ctile-badge"
          >目录抓取中…</span>
          <span
            v-else-if="it.toc && it.toc.status === 'queued'"
            class="ctile-badge"
          >排队中…</span>
          <span
            v-else-if="it.toc && it.toc.status === 'error'"
            class="ctile-badge err"
            :title="it.toc.error"
          >目录失败</span>
        </span>

        <span class="ctile-name">{{ it.name }}</span>
        <span class="ctile-sub">{{ subText(it) }}</span>
        <span
          class="ctile-progress"
          :class="{ idle: percent(it) == null }"
        ><i :style="{ width: `${percent(it) ?? 0}%` }" /></span>
        <span class="ctile-meta">{{ it.author || "佚名" }}</span>

        <span class="tile-ops">
          <span class="tile-ops-left">
            <button
              v-if="it.toc?.status === 'error'"
              type="button"
              class="tbtn"
              title="上次目录抓取失败，点击重试"
              @click.stop="refreshToc(it)"
            >重试目录</button>
            <button
              type="button"
              class="tbtn"
              @click.stop="openDetails(it)"
            >详情</button>
          </span>
          <button
            type="button"
            class="tbtn danger"
            @click.stop="askRemove(it)"
          >删除</button>
        </span>
      </div>
    </div>

    <!-- 删除确认：界面内主题化对话框（替代浏览器 confirm） -->
    <MiuixDialog
      :model-value="!!delTarget"
      title="删除这本书？"
      :close-on-click-modal="!removing"
      @update:model-value="(v: boolean) => { if (!v && !removing) delTarget = null }"
    >
      <div class="dlg">
        <p class="dlg-text">
          将把<b>《{{ delTarget?.name }}》</b>从书架里删除，阅读进度不再保留。之后可重新搜索添加。
        </p>
      </div>
      <div class="dlg-actions">
        <MiuixButton :disabled="removing" @click="delTarget = null">取消</MiuixButton>
        <MiuixButton class="del-btn" :disabled="removing" @click="doRemove">删除</MiuixButton>
      </div>
    </MiuixDialog>

    <!-- 操作失败提示：同样走主题化对话框（替代浏览器 alert） -->
    <MiuixDialog
      :model-value="!!notice"
      title="提示"
      @update:model-value="(v: boolean) => { if (!v) notice = '' }"
    >
      <div class="dlg">
        <p class="dlg-text">{{ notice }}</p>
      </div>
      <div class="dlg-actions">
        <MiuixButton type="primary" @click="notice = ''">我知道了</MiuixButton>
      </div>
    </MiuixDialog>
  </div>
</template>

<style scoped>
.sort-row {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  margin: -6px 0 16px;
}
.sort-chip {
  padding: 5px 13px;
  font-size: 12px;
}
.upd-badge {
  background: color-mix(in srgb, #e5484d 90%, transparent);
  color: #fff;
}

/* ---- 卡片尺寸统一（与首页同构：一行标题 → 进度 → 作者） ----
 * 文字区行数钉死（书名/进度/作者各一行，超出省略），进度条槽常驻占位，
 * 底部操作排固定高度 —— 无论书处于什么状态，每张卡片等高等宽。 */
.shelf-tile .ctile-name,
.shelf-tile .ctile-meta,
.shelf-tile .ctile-sub {
  height: 1.35em;
  line-height: 1.35;
}
/* 未开始阅读：隐藏轨道但保留占位，卡片高度不变 */
.shelf-tile .ctile-progress.idle {
  visibility: hidden;
}

/* ---- 卡片底部操作排：详情在左，删除在右 ---- */
.tile-ops {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  min-height: 27px;
}
.tile-ops-left {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  min-width: 0;
}
.tbtn {
  border: 1px solid var(--m-color-outline);
  background: transparent;
  color: var(--m-color-on-surface-secondary);
  border-radius: 999px;
  padding: 3px 12px;
  font-size: 12px;
  line-height: 19px;
  cursor: pointer;
  font-family: inherit;
  white-space: nowrap;
  user-select: none;
}
@media (prefers-reduced-motion: no-preference) {
  /* 底色响应要快，形变走弹簧 —— 与全局 chip 同一套手感 */
  .tbtn {
    transition:
      background 0.12s ease-out,
      border-color 0.12s ease-out,
      color 0.12s ease-out,
      transform 0.35s var(--app-ease-spring, cubic-bezier(0.3, 1.12, 0.4, 1));
  }
}
.tbtn:hover {
  background: var(--m-color-secondary-container);
  border-color: transparent;
  color: var(--m-color-on-secondary-container);
}
.tbtn:active {
  transform: scale(0.94);
}
.tbtn.danger {
  color: var(--m-color-error);
  border-color: color-mix(in srgb, var(--m-color-error) 45%, transparent);
}
.tbtn.danger:hover {
  background: color-mix(in srgb, var(--m-color-error) 14%, transparent);
  color: var(--m-color-error);
}

/* ---- 对话框内容 ---- */
.dlg {
  display: flex;
  flex-direction: column;
  gap: 10px;
  max-width: 340px;
}
.dlg-text {
  margin: 0;
  font-size: 13.5px;
  line-height: 1.65;
  color: var(--m-color-on-surface-secondary);
  overflow-wrap: anywhere;
}
.dlg-text b {
  color: var(--m-color-on-surface);
  font-weight: 600;
}
/* 危险动作确认键：沿用 MiuixButton 形制与按压反馈，仅换成主题错误色。
 * 选择器带足权重，盖过 miuix / md3e 对默认按钮的底色覆盖。 */
.dlg-actions button.m-button.m-button--default.del-btn {
  background: var(--m-color-error);
  color: var(--m-color-on-error);
}
</style>
