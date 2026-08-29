<script setup lang="ts">
import { computed, onMounted, ref } from "vue";
import {
  MiuixButton,
  MiuixCard,
  MiuixDialog,
  MiuixInput,
  MiuixSwitch,
} from "miuix-vue";
import { api, errMsg } from "@/api/client";
import type { EngineInfo, SourceRow } from "@/api/client";
import { collectGroups, splitGroups } from "@/utils/sourceGroups";
import SourceLoginDialog from "@/components/SourceLoginDialog.vue";

const items = ref<SourceRow[]>([]);
const engines = ref<EngineInfo[]>([]);
const keyword = ref("");
/** 分组筛选；空串 = 全部分组。 */
const activeGroup = ref("");
const loading = ref(true);
const error = ref("");

/* import dialog */
const showImport = ref(false);
const importUrl = ref("");
const importJson = ref("");
const importEngine = ref("legado");
const importBusy = ref(false);
const importErr = ref("");
const importFileName = ref("");
const jsonFileInput = ref<HTMLInputElement | null>(null);

function pickJson() {
  jsonFileInput.value?.click();
}

async function readJsonFile(file: File) {
  if (!/\.(json|txt)$/i.test(file.name) && file.type !== "application/json") {
    importErr.value = "请选择 .json 文件";
    return;
  }
  try {
    const text = await file.text();
    JSON.parse(text); // 仅做快速校验，原始文本原样送后端
    importJson.value = text;
    importFileName.value = file.name;
    importErr.value = "";
  } catch {
    importErr.value = `文件 ${file.name} 不是有效的 JSON`;
  }
}

function onJsonPicked(e: Event) {
  const input = e.target as HTMLInputElement;
  const file = input.files?.[0];
  input.value = ""; // 允许重复选择同一文件
  if (file) void readJsonFile(file);
}

function onJsonDrop(e: DragEvent) {
  const file = e.dataTransfer?.files?.[0];
  if (file) void readJsonFile(file);
}

/* raw view */
const rawOpen = ref<Record<string, unknown> | null>(null);
const rawName = ref("");

/* source login dialog */
const loginTarget = ref<SourceRow | null>(null);
const showLogin = ref(false);

function openLogin(s: SourceRow) {
  loginTarget.value = s;
  showLogin.value = true;
}

async function load() {
  loading.value = true;
  error.value = "";
  try {
    const [s, e] = await Promise.all([api.sourcesList(), api.enginesList()]);
    items.value = s.items;
    engines.value = e.items;
  } catch (e) {
    error.value = errMsg(e);
  } finally {
    loading.value = false;
  }
}
onMounted(load);

function engineTitle(key: string | null): string {
  const k = key || "legado";
  return engines.value.find((x) => x.key === k)?.title ?? k;
}

async function doImport() {
  importErr.value = "";
  if (!importUrl.value && !importJson.value.trim()) {
    importErr.value = "填写 URL、上传 JSON 文件或粘贴 JSON";
    return;
  }
  importBusy.value = true;
  try {
    const res = await api.sourcesImport({
      url: importUrl.value || undefined,
      data: importJson.value.trim() || undefined,
      engine: importEngine.value,
    });
    alert(`导入完成：新增 ${res.added}，更新 ${res.updated}，跳过 ${res.skipped}`);
    showImport.value = false;
    importUrl.value = "";
    importJson.value = "";
    importFileName.value = "";
    await load();
  } catch (e) {
    importErr.value = errMsg(e);
  } finally {
    importBusy.value = false;
  }
}

async function toggle(s: SourceRow) {
  try {
    const r = await api.sourceToggle(s.id);
    s.enabled = r.enabled;
  } catch (e) {
    alert(errMsg(e));
  }
}

async function removeSelected(s: SourceRow) {
  if (!confirm(`删除书源 ${s.sourceName || s.sourceUrl}？`)) return;
  try {
    await api.sourcesDelete([s.id]);
    await load();
  } catch (e) {
    alert(errMsg(e));
  }
}

const groups = computed(() => collectGroups(items.value));

function filtered() {
  const kw = keyword.value.trim().toLowerCase();
  let list = items.value;
  if (activeGroup.value)
    list = list.filter((s) =>
      splitGroups(s.sourceGroup).includes(activeGroup.value),
    );
  if (!kw) return list;
  return list.filter(
    (s) =>
      (s.sourceName ?? "").toLowerCase().includes(kw) ||
      s.sourceUrl.toLowerCase().includes(kw) ||
      (s.sourceGroup ?? "").toLowerCase().includes(kw),
  );
}
</script>

<template>
  <div>
    <div class="bar">
      <h2 class="page-title">书源管理</h2>
      <MiuixButton type="primary" @click="showImport = true">导入书源</MiuixButton>
    </div>

    <MiuixInput v-model="keyword" label="搜索书源" single-line class="search" />

    <div v-if="groups.length" class="grp-row" role="group" aria-label="书源分组">
      <button
        class="chip"
        :class="{ selected: !activeGroup }"
        @click="activeGroup = ''"
      >全部分组</button>
      <button
        v-for="g in groups"
        :key="g.name"
        class="chip"
        :class="{ selected: activeGroup === g.name }"
        @click="activeGroup = activeGroup === g.name ? '' : g.name"
      >{{ g.name }}<span class="g-count">{{ g.count }}</span></button>
    </div>

    <MiuixCard :show-indication="false" class="tbl-card">
      <table class="md-table">
        <thead>
          <tr><th>名称</th><th>分组</th><th>URL</th><th>引擎</th><th>启用</th><th></th></tr>
        </thead>
        <tbody>
          <tr v-for="s in filtered()" :key="s.id">
            <td>{{ s.sourceName || "（未命名）" }}</td>
            <td class="grp-cell">
              <template v-if="s.sourceGroup">
                <span
                  v-for="g in splitGroups(s.sourceGroup)"
                  :key="g"
                  class="eng-chip"
                >{{ g }}</span>
              </template>
              <span v-else>—</span>
            </td>
            <td><code class="src-url">{{ s.sourceUrl }}</code></td>
            <td><span class="eng-chip">{{ engineTitle(s.engine) }}</span></td>
            <td>
              <MiuixSwitch :model-value="s.enabled" @update:model-value="toggle(s)" />
            </td>
            <td>
              <button
                v-if="s.engine === 'legado'"
                class="linkbtn"
                @click="openLogin(s)"
              >登录</button>
              <button
                class="linkbtn danger"
                @click="removeSelected(s)"
              >删除</button>
            </td>
          </tr>
          <tr v-if="!filtered().length">
            <td colspan="6" class="empty">暂无书源，点击右上角导入。</td>
          </tr>
        </tbody>
      </table>
    </MiuixCard>

    <SourceLoginDialog
      v-model="showLogin"
      v-if="loginTarget"
      :source-url="loginTarget.sourceUrl"
      :source-name="loginTarget.sourceName"
    />

    <MiuixDialog v-model="showImport" title="导入书源">
      <div class="dlg">
        <div class="eng-pick">
          <label class="eng-label" for="import-engine">规则引擎</label>
          <div class="eng-select-wrap">
            <select id="import-engine" v-model="importEngine" class="eng-select">
              <option v-for="e in engines" :key="e.key" :value="e.key">
                {{ e.title }}{{ e.sources ? `（${e.sources} 个源）` : "" }}
              </option>
            </select>
            <span class="eng-caret" aria-hidden="true"></span>
          </div>
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
          placeholder='粘贴或拖入 {"bookSourceName": ...} 的 JSON'
          @dragover.prevent
          @drop.prevent="onJsonDrop"
        ></textarea>
        <p class="hint">
          源 JSON 内也可写 "viewEngine": "…" 指定单个源的引擎（优先于上面的选择）。
        </p>
        <div v-if="importErr" class="err">{{ importErr }}</div>
      </div>
      <div class="dlg-actions">
        <MiuixButton @click="showImport = false">取消</MiuixButton>
        <MiuixButton type="primary" :disabled="importBusy" @click="doImport">
          {{ importBusy ? "导入中…" : "导入" }}
        </MiuixButton>
      </div>
    </MiuixDialog>
  </div>
</template>

<style scoped>
.search {
  margin-bottom: 12px;
  max-width: 360px;
}
.grp-row {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  margin-bottom: 12px;
}
.g-count {
  margin-left: 5px;
  font-size: 11px;
  opacity: 0.55;
}
/* 分组单元格保持默认表格单元布局（display:flex 会让
 * vertical-align:middle 失效导致与名称列错位），标签用行内块
 * 自然参与基线对齐；多分组时在单元格内自动换行。 */
.grp-cell .eng-chip {
  margin: 1px 6px 1px 0;
}
.tbl-card {
  --app-card-pad: 4px 10px;
}
.src-url {
  font-size: 12px;
  color: var(--m-color-on-surface-secondary);
}
.eng-chip {
  display: inline-block;
  padding: 2px 10px;
  border-radius: 999px;
  background: var(--m-color-secondary-container);
  color: var(--m-color-on-secondary-container);
  font-size: 12px;
}
.eng-pick {
  display: flex;
  align-items: center;
  gap: 10px;
  margin-bottom: 12px;
}
.eng-label {
  font-size: 13px;
  color: var(--m-color-on-surface-secondary);
  white-space: nowrap;
}
.eng-select-wrap {
  position: relative;
  display: inline-flex;
  align-items: center;
  min-width: 0;
  max-width: 100%;
}
.eng-select {
  appearance: none;
  -webkit-appearance: none;
  border: 1px solid var(--m-color-outline);
  border-radius: var(--app-radius-input, 10px);
  background: transparent;
  color: var(--m-color-on-surface);
  font: inherit;
  font-size: 14px;
  padding: 8px 34px 8px 12px;
  cursor: pointer;
  max-width: 100%;
  text-overflow: ellipsis;
}
.eng-select:focus-visible {
  outline: none;
  border-color: var(--m-color-primary);
  box-shadow: 0 0 0 1px var(--m-color-primary);
}
.eng-caret {
  position: absolute;
  right: 13px;
  width: 8px;
  height: 8px;
  border-right: 2px solid var(--m-color-on-surface-secondary);
  border-bottom: 2px solid var(--m-color-on-surface-secondary);
  transform: translateY(-2px) rotate(45deg);
  pointer-events: none;
}
.json-head {
  display: flex;
  align-items: center;
  gap: 10px;
  margin-top: 12px;
  margin-bottom: 6px;
  flex-wrap: wrap;
}
.fname {
  font-size: 12px;
  color: var(--m-color-on-background-variant);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.hint {
  font-size: 12px;
  color: var(--m-color-on-background-variant);
}
/* 对话框容器只有 ~372px 可用宽（420 - 2×24 padding），
 * 固定宽度会横向溢出被裁切 —— 必须跟随容器自适应。 */
.dlg {
  width: 100%;
}
.or {
  font-size: 13px;
  color: var(--m-color-on-surface-secondary);
}
textarea {
  font-family: Consolas, monospace;
  font-size: 12px;
}
.empty {
  text-align: center;
}
</style>
