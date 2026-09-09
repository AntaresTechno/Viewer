<script setup lang="ts">
import { computed, onMounted, ref } from "vue";
import { MiuixButton, MiuixCard, MiuixCheckbox, MiuixDialog, MiuixInput, MiuixSwitch } from "miuix-vue";
import { api, errMsg } from "@/api/client";
import type { RssSource } from "@/api/client";
import { showAlert, showConfirm } from "@/services/appDialog";
import BatchActionBar from "@/components/admin/BatchActionBar.vue";

const items = ref<RssSource[]>([]);
const keyword = ref("");
const loading = ref(true);
const error = ref("");
const showImport = ref(false);
const importUrl = ref("");
const importJson = ref("");
const importBusy = ref(false);
const importError = ref("");
const fileInput = ref<HTMLInputElement | null>(null);
const selectedIds = ref<number[]>([]);
const batchBusy = ref(false);

const filtered = computed(() => {
  const q = keyword.value.trim().toLowerCase();
  if (!q) return items.value;
  return items.value.filter((s) => [s.sourceName, s.sourceUrl, s.sourceGroup]
    .some((value) => value.toLowerCase().includes(q)));
});

onMounted(load);

async function load() {
  loading.value = true;
  try {
    items.value = (await api.rssSources()).items;
    const known = new Set(items.value.map((source) => source.id));
    selectedIds.value = selectedIds.value.filter((id) => known.has(id));
  }
  catch (e) { error.value = errMsg(e); }
  finally { loading.value = false; }
}

async function doImport() {
  if (!importUrl.value.trim() && !importJson.value.trim()) {
    importError.value = "填写网络地址、选择文件或粘贴 JSON";
    return;
  }
  importBusy.value = true;
  importError.value = "";
  try {
    const result = await api.rssImport({
      url: importUrl.value.trim() || undefined,
      data: importJson.value.trim() || undefined,
    });
    await showAlert(`导入完成：新增 ${result.added}，更新 ${result.updated}，跳过 ${result.skipped}`);
    showImport.value = false;
    importUrl.value = "";
    importJson.value = "";
    await load();
  } catch (e) { importError.value = errMsg(e); }
  finally { importBusy.value = false; }
}

async function readFile(file: File) {
  try {
    const text = await file.text();
    JSON.parse(text);
    importJson.value = text;
    importError.value = "";
  } catch { importError.value = "文件不是有效的 JSON"; }
}

function picked(event: Event) {
  const input = event.target as HTMLInputElement;
  if (input.files?.[0]) void readFile(input.files[0]);
  input.value = "";
}

async function toggle(source: RssSource) {
  try { source.enabled = (await api.rssToggle(source.id)).enabled; }
  catch (e) { await showAlert(errMsg(e)); }
}

async function remove(source: RssSource) {
  if (!await showConfirm(
    `删除订阅源“${source.sourceName || source.sourceUrl}”及其文章缓存？`,
    { title: "删除订阅源", confirmText: "删除", danger: true },
  )) return;
  try { await api.rssDelete([source.id]); await load(); }
  catch (e) { await showAlert(errMsg(e)); }
}

const visibleIds = computed(() => filtered.value.map((source) => source.id));
const allVisibleSelected = computed(() =>
  visibleIds.value.length > 0
  && visibleIds.value.every((id) => selectedIds.value.includes(id)),
);

function toggleSelected(id: number) {
  selectedIds.value = selectedIds.value.includes(id)
    ? selectedIds.value.filter((value) => value !== id)
    : [...selectedIds.value, id];
}

function toggleAllVisible() {
  const visible = new Set(visibleIds.value);
  selectedIds.value = allVisibleSelected.value
    ? selectedIds.value.filter((id) => !visible.has(id))
    : [...new Set([...selectedIds.value, ...visibleIds.value])];
}

async function batchSetEnabled(enabled: boolean) {
  const ids = [...selectedIds.value];
  if (!ids.length || batchBusy.value) return;
  batchBusy.value = true;
  try {
    await api.rssSetEnabled(ids, enabled);
    const selected = new Set(ids);
    items.value.forEach((source) => {
      if (selected.has(source.id)) source.enabled = enabled;
    });
    selectedIds.value = [];
  } catch (e) {
    await showAlert(errMsg(e));
  } finally {
    batchBusy.value = false;
  }
}

async function batchDelete() {
  const ids = [...selectedIds.value];
  if (!ids.length || batchBusy.value) return;
  if (!await showConfirm(
    `删除选中的 ${ids.length} 个订阅源及其文章缓存？`,
    { title: "批量删除订阅源", confirmText: "删除", danger: true },
  )) return;
  batchBusy.value = true;
  try {
    await api.rssDelete(ids);
    selectedIds.value = [];
    await load();
  } catch (e) {
    await showAlert(errMsg(e));
  } finally {
    batchBusy.value = false;
  }
}

function exportAll() {
  const token = localStorage.getItem("viewer_token") ?? "";
  window.open(`/api/rss/sources/export?token=${encodeURIComponent(token)}`, "_blank", "noopener,noreferrer");
}
</script>

<template>
  <div>
    <div class="bar">
      <div><h2 class="page-title">订阅源管理</h2><p class="page-sub">兼容 md3-legado 的 RssSource JSON</p></div>
      <div class="actions"><MiuixButton @click="$router.push('/admin/sources')">书源</MiuixButton><MiuixButton @click="$router.push('/admin/media-sources')">媒体源</MiuixButton><MiuixButton @click="$router.push('/rss')">返回订阅</MiuixButton><MiuixButton @click="exportAll">导出</MiuixButton><MiuixButton type="primary" @click="showImport = true">导入订阅源</MiuixButton></div>
    </div>
    <MiuixInput v-model="keyword" label="搜索名称、地址或分组" single-line class="search" />
    <BatchActionBar
      :selected-count="selectedIds.length"
      :total-count="visibleIds.length"
      :all-selected="allVisibleSelected"
      :busy="batchBusy"
      @toggle-all="toggleAllVisible"
      @clear="selectedIds = []"
      @enable="batchSetEnabled(true)"
      @disable="batchSetEnabled(false)"
      @delete="batchDelete"
    />
    <p v-if="error" class="err">{{ error }}</p>
    <div v-if="loading" class="center">加载中…</div>
    <MiuixCard v-else :show-indication="false" class="table-card">
      <table class="md-table">
        <thead><tr><th class="select-cell"><MiuixCheckbox :model-value="allVisibleSelected" aria-label="全选当前订阅源" @update:model-value="toggleAllVisible" /></th><th>名称</th><th>分组</th><th>地址</th><th>类型</th><th>启用</th><th></th></tr></thead>
        <tbody>
          <tr v-for="source in filtered" :key="source.id">
            <td class="select-cell"><MiuixCheckbox :model-value="selectedIds.includes(source.id)" :aria-label="`选择订阅源 ${source.sourceName || source.sourceUrl}`" @update:model-value="toggleSelected(source.id)" /></td>
            <td><div class="source-name"><img v-if="source.sourceIcon" :src="source.sourceIcon" alt="" /><span>{{ source.sourceName || "（未命名）" }}</span></div></td>
            <td>{{ source.sourceGroup || "—" }}</td>
            <td><code>{{ source.sourceUrl }}</code></td>
            <td>{{ source.singleUrl ? "网页" : "文章" }}<span v-if="source.hasSearch" class="tag">可搜索</span></td>
            <td><MiuixSwitch :model-value="source.enabled" @update:model-value="toggle(source)" /></td>
            <td><button class="linkbtn danger" @click="remove(source)">删除</button></td>
          </tr>
          <tr v-if="!filtered.length"><td colspan="7" class="empty">暂无订阅源。</td></tr>
        </tbody>
      </table>
    </MiuixCard>

    <MiuixDialog v-model="showImport" title="导入 Legado 订阅源">
      <div class="dialog-body">
        <MiuixInput v-model="importUrl" label="从 URL 导入" single-line />
        <div class="file-row"><span>或选择本地 JSON</span><MiuixButton @click="fileInput?.click()">选择文件…</MiuixButton></div>
        <input ref="fileInput" hidden type="file" accept=".json,.txt,application/json" @change="picked" />
        <textarea v-model="importJson" rows="10" placeholder='粘贴包含 "sourceUrl" 和 "sourceName" 的对象或数组' @dragover.prevent @drop.prevent="($event.dataTransfer?.files[0]) && readFile($event.dataTransfer.files[0])"></textarea>
        <p class="hint">支持普通 RSS/Atom（无需规则）以及 ruleArticles、ruleTitle、ruleLink、ruleContent 等 Legado 规则字段。</p>
        <p v-if="importError" class="err">{{ importError }}</p>
      </div>
      <div class="dlg-actions"><MiuixButton @click="showImport = false">取消</MiuixButton><MiuixButton type="primary" :disabled="importBusy" @click="doImport">{{ importBusy ? "导入中…" : "导入" }}</MiuixButton></div>
    </MiuixDialog>
  </div>
</template>

<style scoped>
.bar { align-items:flex-start; gap:16px; }.page-title { margin-bottom:3px; }.page-sub { margin-top:0; }.actions,.file-row { display:flex; align-items:center; flex-wrap:wrap; gap:8px; }.search { max-width:420px; margin-bottom:14px; }.table-card { --app-card-pad:4px 10px; overflow-x:auto; }.select-cell { width:38px; text-align:center; }.source-name { display:flex; align-items:center; gap:9px; min-width:140px; }.source-name img { width:28px; height:28px; border-radius:7px; object-fit:cover; }code { display:block; max-width:360px; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; font-size:12px; }.tag { margin-left:6px; padding:2px 7px; border-radius:999px; background:var(--m-color-secondary-container); color:var(--m-color-on-secondary-container); font-size:11px; }.dialog-body { display:grid; gap:12px; min-width:min(560px,75vw); }.file-row { justify-content:space-between; color:var(--m-color-on-surface-secondary); font-size:13px; }.hint { margin:0; color:var(--m-color-on-surface-secondary); font-size:12px; line-height:1.5; }@media(max-width:680px){.bar { flex-direction:column; }.dialog-body { min-width:0; }.actions { width:100%; }}
</style>
