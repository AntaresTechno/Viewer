<script setup lang="ts">
import { computed, onMounted, ref } from "vue";
import {
  MiuixButton,
  MiuixCard,
  MiuixCheckbox,
  MiuixDialog,
  MiuixInput,
  MiuixProgressIndicator,
  MiuixSwitch,
} from "miuix-vue";
import { api, errMsg } from "@/api/client";
import type { MediaImportResult, MediaKind, MediaSource } from "@/api/client";
import { collectGroups, splitGroups } from "@/utils/sourceGroups";
import FileDropOverlay from "@/components/admin/FileDropOverlay.vue";
import BatchActionBar from "@/components/admin/BatchActionBar.vue";
import { showAlert, showConfirm } from "@/services/appDialog";

const items = ref<MediaSource[]>([]);
const loading = ref(true);
const error = ref("");
const keyword = ref("");
const activeGroup = ref("");
const notice = ref("");
const selectedIds = ref<number[]>([]);
const batchBusy = ref(false);

const KIND_LABEL: Record<string, string> = { comic: "漫画", audio: "音频", video: "视频" };
const KIND_SHORT: Record<string, string> = { comic: "漫", audio: "音", video: "视" };
const KINDS: MediaKind[] = ["comic", "audio", "video"];

const groups = computed(() => collectGroups(items.value));

const filtered = computed(() => {
  const kw = keyword.value.trim().toLowerCase();
  return items.value.filter((s) => {
    if (activeGroup.value && !splitGroups(s.sourceGroup).includes(activeGroup.value)) return false;
    if (!kw) return true;
    return (
      s.sourceName.toLowerCase().includes(kw) ||
      s.sourceKey.toLowerCase().includes(kw) ||
      s.sourceFormat.includes(kw)
    );
  });
});

onMounted(load);
async function load() {
  loading.value = true;
  try {
    items.value = await api.mediaSources();
    const known = new Set(items.value.map((source) => source.id));
    selectedIds.value = selectedIds.value.filter((id) => known.has(id));
    error.value = "";
  } catch (e) {
    error.value = errMsg(e);
  } finally {
    loading.value = false;
  }
}

/* ---- 导入 ---- */
const showImport = ref(false);
const importUrl = ref("");
const importJson = ref("");
const importKind = ref<MediaKind | "">("");
const importBusy = ref(false);
const importErr = ref("");
const importFileName = ref("");
const jsonFileInput = ref<HTMLInputElement | null>(null);
const result = ref<MediaImportResult | null>(null);
const dropping = ref(false);

function pickJson() {
  jsonFileInput.value?.click();
}
function onJsonPicked(e: Event) {
  const file = (e.target as HTMLInputElement).files?.[0];
  if (file) {
    importFileName.value = file.name;
    file.text().then((text) => { importJson.value = text; }).catch(() => {});
  }
  (e.target as HTMLInputElement).value = "";
}
function openDrop() {
  showImport.value = true;
  importJson.value = "";
  importFileName.value = "";
  result.value = null;
}
function onDropPicked(text: string, name: string) {
  importJson.value = text;
  importFileName.value = name;
  result.value = null;
  showImport.value = true;
}

async function doImport() {
  if (importBusy.value) return;
  importBusy.value = true;
  result.value = null;
  const text = (importJson.value ?? "").trim();
  try {
    const res = await api.mediaImportSources({
      data: text || undefined,
      mediaKind: importKind.value || undefined,
      url: importUrl.value || undefined,
    });
    result.value = res;
    if (!res.needsKind && !res.undecided.length) {
      importJson.value = "";
      importFileName.value = "";
      notice.value = `导入完成：新增 ${res.added}，更新 ${res.updated}，跳过 ${res.skipped}`;
    }
    await load();
  } catch (e) {
    importErr.value = errMsg(e);
  } finally {
    importBusy.value = false;
  }
}

async function setKindUndecided(src: { sourceKey: string }, kind: MediaKind) {
  try {
    const hit = items.value.find((s) => s.sourceKey === src.sourceKey);
    if (hit) await api.mediaSetSourceKind(hit.id, kind);
    if (result.value) {
      result.value.needsKind = false;
      result.value.undecided = result.value.undecided.filter((u) => u.sourceKey !== src.sourceKey);
    }
    await load();
  } catch (e) {
    notice.value = errMsg(e);
  }
}

/* ---- 行操作 ---- */
async function toggle(s: MediaSource) {
  try {
    s.enabled = (await api.mediaToggleSource(s.id)).enabled;
  } catch (e) {
    notice.value = errMsg(e);
  }
}
async function setKind(s: MediaSource, kind: MediaKind) {
  try {
    await api.mediaSetSourceKind(s.id, kind);
    await load();
  } catch (e) {
    notice.value = errMsg(e);
  }
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
    await api.mediaSetSourcesEnabled(ids, enabled);
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
    `删除选中的 ${ids.length} 个媒体源及其媒体库条目？`,
    { title: "批量删除媒体源", confirmText: "删除", danger: true },
  )) return;
  batchBusy.value = true;
  try {
    await api.mediaDeleteSources(ids);
    selectedIds.value = [];
    await load();
  } catch (e) {
    await showAlert(errMsg(e));
  } finally {
    batchBusy.value = false;
  }
}

const delTarget = ref<MediaSource | null>(null);
async function doDelete() {
  const t = delTarget.value;
  if (!t) return;
  try {
    await api.mediaDeleteSources([t.id]);
    delTarget.value = null;
    await load();
  } catch (e) {
    notice.value = errMsg(e);
    delTarget.value = null;
  }
}
async function doExport() {
  try {
    const data = await api.mediaExportSources();
    const blob = new Blob([JSON.stringify(data, null, 2)], { type: "application/json" });
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    a.download = "media-sources.json";
    a.click();
    URL.revokeObjectURL(a.href);
  } catch (e) {
    notice.value = errMsg(e);
  }
}
function kindLabel(k: string): string {
  return KIND_LABEL[k] || "未分类";
}
</script>

<template>
  <div>
    <div class="bar">
      <h2 class="page-title">媒体源管理</h2>
      <div class="bar-btns">
        <MiuixButton @click="doExport">导出</MiuixButton>
        <MiuixButton @click="$router.push('/admin/rss-sources')">订阅源</MiuixButton>
        <MiuixButton @click="$router.push('/admin/sources')">书源</MiuixButton>
        <MiuixButton type="primary" @click="openDrop">导入媒体源</MiuixButton>
      </div>
    </div>

    <MiuixInput v-model="keyword" label="搜索媒体源" single-line class="search" />

    <div v-if="groups.length" class="grp-row" role="group" aria-label="媒体源分组">
      <button class="chip" :class="{ selected: !activeGroup }" @click="activeGroup = ''">
        全部分组
      </button>
      <button
        v-for="g in groups"
        :key="g.name"
        class="chip"
        :class="{ selected: activeGroup === g.name }"
        @click="activeGroup = activeGroup === g.name ? '' : g.name"
      >{{ g.name }}<span class="g-count">{{ g.count }}</span></button>
    </div>

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

    <div v-if="loading" class="center"><MiuixProgressIndicator /></div>
    <div v-else-if="error" class="center err">{{ error }}</div>
    <MiuixCard v-else :show-indication="false" class="tbl-card">
      <table class="md-table">
        <thead>
          <tr><th class="select-cell"><MiuixCheckbox :model-value="allVisibleSelected" aria-label="全选当前媒体源" @update:model-value="toggleAllVisible" /></th><th>名称</th><th>分组</th><th>类型</th><th>格式</th><th>启用</th><th></th></tr>
        </thead>
        <tbody>
          <tr v-for="s in filtered" :key="s.id">
            <td class="select-cell"><MiuixCheckbox :model-value="selectedIds.includes(s.id)" :aria-label="`选择媒体源 ${s.sourceName || s.sourceKey}`" @update:model-value="toggleSelected(s.id)" /></td>
            <td class="name-cell">
              <span class="kind-dot" :data-kind="s.mediaKind || ''"></span>
              {{ s.sourceName || "（未命名）" }}
            </td>
            <td class="grp-cell">
              <template v-if="s.sourceGroup">
                <span v-for="g in splitGroups(s.sourceGroup)" :key="g" class="eng-chip">{{ g }}</span>
              </template>
              <span v-else>—</span>
            </td>
            <td>
              <span class="kind-pill" :data-kind="s.mediaKind || ''">
                {{ kindLabel(s.mediaKind || "") }}
              </span>
            </td>
            <td><code class="src-url">{{ s.sourceFormat === "book" ? "书源" : "RSS" }}</code></td>
            <td><MiuixSwitch :model-value="s.enabled" @update:model-value="toggle(s)" /></td>
            <td>
              <div class="row-ops">
                <button
                  v-for="k in KINDS"
                  :key="k"
                  type="button"
                  class="kind-mini"
                  :class="{ on: s.mediaKind === k }"
                  :title="`设为${KIND_LABEL[k]}`"
                  @click="setKind(s, k)"
                >{{ KIND_SHORT[k] }}</button>
                <button class="linkbtn danger" @click="delTarget = s">删除</button>
              </div>
            </td>
          </tr>
          <tr v-if="!filtered.length">
            <td colspan="7" class="empty">暂无媒体源，点击右上角「导入媒体源」。</td>
          </tr>
        </tbody>
      </table>
    </MiuixCard>

    <MiuixDialog v-model="showImport" title="导入媒体源">
      <div class="dlg">
        <div class="kind-pick">
          <span class="pick-label">类型</span>
          <button
            type="button"
            class="chip"
            :class="{ selected: !importKind }"
            @click="importKind = ''"
          >自动</button>
          <button
            v-for="k in KINDS"
            :key="k"
            type="button"
            class="chip"
            :class="{ selected: importKind === k }"
            @click="importKind = importKind === k ? '' : k"
          >{{ KIND_LABEL[k] }}</button>
        </div>

        <MiuixInput v-model="importUrl" label="从 URL 导入" single-line />

        <div class="json-head">
          <span class="or">或导入 JSON 文件：</span>
          <MiuixButton @click="pickJson">选择文件…</MiuixButton>
          <span v-if="importFileName" class="fname">{{ importFileName }}</span>
        </div>
        <input
          ref="jsonFileInput"
          type="file"
          accept=".json,.txt,application/json,text/plain"
          hidden
          @change="onJsonPicked"
        />
        <textarea
          v-model="importJson"
          rows="8"
          placeholder='粘贴或拖入 JSON：可数组 [ ... ] 或单个源。如 {"sourceUrl":"…","ruleContent":"<video>…"}'
          :class="{ drop: dropping }"
        ></textarea>
        <p class="hint">
          接受 Legado RssSource（sourceUrl）与 BookSource（bookSourceUrl）；未声明 type 的会按
          <code>ruleContent</code> 是否含 <code>&lt;video&gt;</code> / <code>&lt;audio&gt;</code> 自动识别为视频/音频。
        </p>
        <div v-if="importErr" class="err">{{ importErr }}</div>

        <div v-if="result?.needsKind && result?.undecided.length" class="uncard">
          <div class="un-title">以下源未能自动识别类型，请指定：</div>
          <div v-for="u in result!.undecided" :key="u.sourceKey" class="un-row">
            <span class="un-name">{{ u.sourceName || u.sourceKey }}</span>
            <div class="un-kinds">
              <button
                v-for="k in KINDS"
                :key="k"
                type="button"
                class="chip small"
                @click="setKindUndecided(u, k)"
              >{{ KIND_LABEL[k] }}</button>
            </div>
          </div>
        </div>
      </div>
      <div class="dlg-actions">
        <MiuixButton @click="showImport = false">取消</MiuixButton>
        <MiuixButton type="primary" :disabled="importBusy" @click="doImport">
          {{ importBusy ? "导入中…" : "导入" }}
        </MiuixButton>
      </div>
    </MiuixDialog>

    <MiuixDialog :model-value="!!notice" title="提示" @update:model-value="(v:boolean)=>{ if(!v) notice='' }">
      <div class="dlg"><p class="dlg-text">{{ notice }}</p></div>
      <div class="dlg-actions"><MiuixButton type="primary" @click="notice=''">我知道了</MiuixButton></div>
    </MiuixDialog>
    <MiuixDialog :model-value="!!delTarget" title="删除媒体源？" @update:model-value="(v:boolean)=>{ if(!v) delTarget=null }">
      <div class="dlg"><p class="dlg-text">将删除源<b>「{{ delTarget?.sourceName }}」</b>及其相关媒体库条目。</p></div>
      <div class="dlg-actions">
        <MiuixButton @click="delTarget=null">取消</MiuixButton>
        <MiuixButton class="del-btn" @click="doDelete">删除</MiuixButton>
      </div>
    </MiuixDialog>

    <!-- 全窗口拖入提示（带动画） -->
    <FileDropOverlay v-model="dropping" @picked="onDropPicked" />
  </div>
</template>

<style scoped>
.bar { display: flex; align-items: center; justify-content: space-between; gap: 12px; flex-wrap: wrap; }
.bar-btns { display: flex; gap: 8px; }
.search { margin-bottom: 12px; max-width: 360px; }
.grp-row { display: flex; flex-wrap: wrap; gap: 8px; margin-bottom: 12px; }
.g-count { margin-left: 5px; font-size: 11px; opacity: 0.55; }
.tbl-card { --app-card-pad: 4px 12px; }
.select-cell { width: 38px; text-align: center; }
.name-cell { font-weight: 600; }
.kind-dot { display: inline-block; width: 8px; height: 8px; border-radius: 50%; margin-right: 6px; vertical-align: middle; }
.kind-dot[data-kind="comic"] { background: #c98cf4; }
.kind-dot[data-kind="audio"] { background: #67c2ef; }
.kind-dot[data-kind="video"] { background: #ef7d7d; }
.kind-dot[data-kind=""] { background: #bbb; }
.grp-cell .eng-chip { margin: 1px 6px 1px 0; }
.src-url { font-size: 12px; color: var(--m-color-on-surface-secondary); }
.kind-pill { padding: 2px 9px; border-radius: 999px; font-size: 12px; font-weight: 600; white-space: nowrap; }
.kind-pill[data-kind="comic"] { background: rgba(214,156,246,.3); color: #7c3aed; }
.kind-pill[data-kind="audio"] { background: rgba(110,203,245,.3); color: #0369a1; }
.kind-pill[data-kind="video"] { background: rgba(246,168,168,.35); color: #b91c1c; }
.kind-pill[data-kind=""] { background: #eee; color: #777; }
.row-ops { display: flex; align-items: center; gap: 4px; white-space: nowrap; }
.kind-mini { width: 26px; height: 24px; border-radius: 7px; border: 1px solid var(--app-color-outline-variant,#e0e0e0);
  background: var(--app-color-surface-2,#fff); color: var(--app-color-text-muted,#777); font-size: 12px; cursor: pointer; }
.kind-mini.on { background: var(--app-color-primary,#6750a4); color: #fff; border-color: transparent; }
.linkbtn { background: none; border: 0; color: var(--app-color-primary,#6750a4); cursor: pointer; font-size: 13px; padding: 2px 4px; }
.linkbtn.danger { color: #d93025; }

.kind-pick { display: flex; flex-wrap: wrap; align-items: center; gap: 6px; margin-bottom: 8px; }
.pick-label { font-size: 13px; color: var(--app-color-text-muted,#777); margin-right: 2px; }
.chip { padding: 5px 12px; border-radius: 999px; border: 1px solid var(--app-color-outline-variant,#e0e0e0);
  background: var(--app-color-surface-2,#fff); color: var(--app-color-text,#333); cursor: pointer; font-size: 13px; }
.chip.selected { background: var(--app-color-primary-container,#eaddff); border-color: transparent;
  color: var(--app-color-on-primary-container,#21005d); font-weight: 600; }
.chip.small { padding: 3px 10px; font-size: 12px; }
.json-head { display: flex; align-items: center; gap: 10px; margin: 6px 0; flex-wrap: wrap; }
.or { font-size: 13px; color: var(--app-color-text-muted,#777); }
.fname { font-size: 12px; color: var(--app-color-primary,#6750a4); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; max-width: 40%; }
textarea { width: 100%; box-sizing: border-box; font-family: var(--app-font-mono, monospace); font-size: 12.5px;
  padding: 10px; border-radius: var(--app-shape-md,10px); border: 1px solid var(--app-color-outline-variant,#e0e0e0);
  background: var(--app-color-surface-2,#fff); color: var(--app-color-text,#111); resize: vertical;
  transition: border-color .2s ease, box-shadow .2s ease; }
textarea.drop { border-color: var(--app-color-primary,#6750a4); box-shadow: 0 0 0 3px color-mix(in srgb, var(--app-color-primary,#6750a4) 22%, transparent); }
.hint { font-size: 12px; color: var(--app-color-text-muted,#888); margin-top: 6px; }
.hint code { font-size: 11px; }
.err { margin-top: 8px; font-size: 12.5px; color: #d93025; }
.uncard { margin-top: 12px; padding: 12px; border-radius: var(--app-shape-md,10px); background: var(--app-color-surface-variant,#f5f5f7); }
.un-title { font-size: 13px; font-weight: 600; margin-bottom: 8px; }
.un-row { display: flex; align-items: center; justify-content: space-between; gap: 8px; padding: 4px 0; }
.un-name { font-size: 13px; min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.un-kinds { display: flex; gap: 6px; }
</style>
