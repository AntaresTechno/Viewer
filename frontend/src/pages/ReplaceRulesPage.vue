<script setup lang="ts">
import { onMounted, reactive, ref } from "vue";
import {
  MiuixButton,
  MiuixCard,
  MiuixCheckbox,
  MiuixInput,
  MiuixText,
} from "miuix-vue";
import { api, errMsg } from "@/api/client";
import type { ReplaceRuleItem } from "@/api/client";
import { useAuth } from "@/stores/auth";

const auth = useAuth();
const canManage = () => auth.can("books.replace.manage");

const items = ref<ReplaceRuleItem[]>([]);
const loading = ref(true);
const error = ref("");
const msg = ref("");

/* import */
const importText = ref("");

/* test drive */
const testIn = ref(
  "　　本章未做任何修改。广告内容请关注公众号xxx获取更多精彩内容。",
);
const testOut = ref("");
const testApplied = ref<string[]>([]);

/* editing */
const editingId = ref<number | null>(null);
const draft = reactive({
  name: "", group: "", order: 0, pattern: "",
  replacement: "", scope: "", regex: true, caseSensitive: true,
});

async function load() {
  loading.value = true;
  error.value = "";
  try {
    items.value = (await api.replaceList()).items;
  } catch (e) {
    error.value = errMsg(e);
  } finally {
    loading.value = false;
  }
}
onMounted(load);

async function doImport() {
  msg.value = "";
  if (!importText.value.trim()) return;
  try {
    const r = await api.replaceImport(importText.value);
    msg.value = `已导入 ${r.imported} 条规则`;
    importText.value = "";
    await load();
  } catch (e) {
    error.value = errMsg(e);
  }
}

async function toggle(r: ReplaceRuleItem) {
  try {
    await api.replaceToggle(r.id);
    r.isActive = !r.isActive;
  } catch (e) {
    error.value = errMsg(e);
  }
}

async function remove(r: ReplaceRuleItem) {
  if (!confirm(`删除规则「${r.name || r.pattern.slice(0, 20)}」？`)) return;
  try {
    await api.replaceDelete([r.id]);
    items.value = items.value.filter((x) => x.id !== r.id);
  } catch (e) {
    error.value = errMsg(e);
  }
}

function startEdit(r: ReplaceRuleItem) {
  editingId.value = r.id;
  Object.assign(draft, {
    name: r.name, group: r.group, order: r.order,
    pattern: r.pattern, replacement: r.replacement, scope: r.scope,
    regex: r.regex, caseSensitive: r.caseSensitive,
  });
}

async function saveEdit() {
  if (editingId.value == null) return;
  try {
    await api.replaceUpdate(editingId.value, { ...draft });
    editingId.value = null;
    await load();
  } catch (e) {
    error.value = errMsg(e);
  }
}

async function runTest() {
  try {
    const r = await api.replaceTest(testIn.value);
    testOut.value = r.content;
    testApplied.value = r.applied;
  } catch (e) {
    error.value = errMsg(e);
  }
}
</script>

<template>
  <div>
    <h2 class="page-title">净化规则</h2>
    <p class="sub">
      按分组与序号依次应用到所有章节正文（全局生效）。支持从「阅读 · legado」导出的替换规则 JSON 直接导入。
    </p>

    <MiuixCard class="card" :show-indication="false">
      <MiuixText type="title3">导入规则</MiuixText>
      <p class="hint">
        粘贴 legado 替换规则 JSON（对象或数组）。scope 写法：留空=全部书籍；
        多个条件用换行 / 分号 / || 分隔；子串匹配书名、源名或源地址；前缀 “-” 表示排除。
      </p>
      <textarea
        v-model="importText"
        rows="5"
        placeholder='[{"name":"去广告","group":"基础","groupOrder":0,"order":1,"isActive":true,"pattern":"广告.*?结束","replacement":"","regex":true,"scope":""}]'
      />
      <div class="row">
        <MiuixButton
          type="primary"
          :disabled="!canManage() || !importText.trim()"
          @click="doImport"
        >导入</MiuixButton>
        <span v-if="msg" class="ok">{{ msg }}</span>
      </div>
    </MiuixCard>

    <MiuixCard class="card" :show-indication="false">
      <MiuixText type="title3">试一试</MiuixText>
      <textarea v-model="testIn" rows="2" />
      <div class="row">
        <MiuixButton @click="runTest">应用规则预览</MiuixButton>
        <span v-if="testApplied.length" class="applied">
          命中：{{ testApplied.join("、") }}
        </span>
      </div>
      <pre v-if="testOut" class="preview">{{ testOut }}</pre>
    </MiuixCard>

    <MiuixCard class="card" :show-indication="false">
      <div class="head-row">
        <MiuixText type="title3">规则列表（{{ items.length }}）</MiuixText>
        <MiuixButton :disabled="loading" @click="load">刷新</MiuixButton>
      </div>
      <p v-if="error" class="err">{{ error }}</p>
      <div v-if="loading" class="center"><span class="spin" /></div>
      <p v-else-if="!items.length" class="center dim">还没有净化规则。</p>

      <ul v-else class="rules">
        <li v-for="r in items" :key="r.id">
          <template v-if="editingId === r.id">
            <div class="edit-grid">
              <label>名称<MiuixInput v-model="draft.name" single-line /></label>
              <label>分组<MiuixInput v-model="draft.group" single-line /></label>
              <label>序号<MiuixInput v-model.number="draft.order" single-line /></label>
              <label>作用范围(scope)<MiuixInput v-model="draft.scope" single-line /></label>
              <label class="wide">匹配内容
                <textarea v-model="draft.pattern" rows="2" />
              </label>
              <label class="wide">替换为<textarea v-model="draft.replacement" rows="2" /></label>
              <label class="chk">
                <MiuixCheckbox :model-value="draft.regex"
                               @update:model-value="draft.regex = $event" />
                正则替换
              </label>
              <label class="chk">
                <MiuixCheckbox :model-value="draft.caseSensitive"
                               @update:model-value="draft.caseSensitive = $event" />
                区分大小写
              </label>
            </div>
            <div class="row">
              <MiuixButton type="primary" @click="saveEdit">保存</MiuixButton>
              <MiuixButton @click="editingId = null">取消</MiuixButton>
            </div>
          </template>
          <template v-else>
            <div class="rule-main">
              <div class="rule-top">
                <span class="name">{{ r.name || `#${r.id}` }}</span>
                <span v-if="r.group" class="grp">{{ r.group }}</span>
                <span class="badge">{{ r.regex ? "正则" : "文本" }}</span>
                <span v-if="!r.caseSensitive" class="badge">忽略大小写</span>
                <span v-if="r.scope" class="scope" :title="r.scope">范围: {{ r.scope }}</span>
                <span class="order">#{{ r.groupOrder }}/{{ r.order }}</span>
              </div>
              <code class="pat">{{ r.pattern }}</code>
              <code v-if="r.replacement" class="rep">→ {{ r.replacement }}</code>
            </div>
            <div class="rule-ops" v-if="canManage()">
              <button class="mini" @click="toggle(r)">
                {{ r.isActive ? "停用" : "启用" }}
              </button>
              <button class="mini" @click="startEdit(r)">编辑</button>
              <button class="mini danger" @click="remove(r)">删除</button>
            </div>
          </template>
        </li>
      </ul>
    </MiuixCard>
  </div>
</template>

<style scoped>
.sub {
  color: var(--m-color-on-background-variant);
  font-size: 13px;
  margin: -6px 0 14px;
  line-height: 1.6;
}
.card {
  margin-bottom: 14px;
}
.hint {
  color: var(--m-color-on-background-variant);
  font-size: 12px;
  line-height: 1.7;
  margin: 6px 0 8px;
}
textarea {
  width: 100%;
  border: 1px solid var(--m-color-outline);
  border-radius: 10px;
  background: var(--m-color-surface-container-lowest);
  color: var(--m-color-on-surface);
  padding: 10px 12px;
  font-size: 13px;
  font-family: ui-monospace, monospace;
  outline: none;
  resize: vertical;
}
.row {
  display: flex;
  align-items: center;
  gap: 12px;
  margin-top: 10px;
}
.ok { color: var(--m-color-primary); font-size: 13px; }
.err { color: var(--m-color-error); font-size: 13px; }
.applied { font-size: 12px; color: var(--m-color-primary); }
.preview {
  background: var(--m-color-surface-container-lowest);
  border-radius: 10px;
  padding: 10px 12px;
  font-size: 12px;
  white-space: pre-wrap;
  word-break: break-all;
  margin: 10px 0 0;
}
.head-row {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 8px;
}
.center {
  display: grid;
  place-items: center;
  padding: 22px 0;
}
.dim { color: var(--m-color-on-background-variant); font-size: 13px; }
.spin {
  width: 24px;
  height: 24px;
  border-radius: 50%;
  border: 3px solid rgba(0, 0, 0, 0.12);
  border-top-color: var(--m-color-primary);
  animation: spin 0.9s linear infinite;
}
@keyframes spin { to { transform: rotate(360deg); } }

.rules {
  list-style: none;
  margin: 0;
  padding: 0;
}
.rules li {
  padding: 12px 4px;
  border-bottom: 1px solid var(--m-color-outline-variant, rgba(0, 0, 0, 0.07));
  display: flex;
  gap: 10px;
  align-items: flex-start;
}
.rule-main { flex: 1; min-width: 0; }
.rule-top {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  align-items: center;
  margin-bottom: 5px;
}
.name { font-weight: 600; font-size: 13px; }
.grp, .badge, .order {
  font-size: 11px;
  background: var(--m-color-surface-container-high);
  border-radius: 6px;
  padding: 1px 7px;
  color: var(--m-color-on-surface-secondary);
}
.scope {
  font-size: 11px;
  color: var(--m-color-on-background-variant);
  max-width: 240px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.pat, .rep {
  display: block;
  font-size: 12px;
  color: var(--m-color-on-background-variant);
  white-space: pre-wrap;
  word-break: break-all;
}
.rep { color: var(--m-color-primary); }
.rule-ops { display: flex; gap: 6px; flex: none; }
.mini {
  border: 1px solid var(--m-color-outline);
  background: transparent;
  color: var(--m-color-on-surface);
  border-radius: 8px;
  font-size: 12px;
  padding: 4px 10px;
  cursor: pointer;
}
.mini.danger {
  color: var(--m-color-error);
  border-color: var(--m-color-error);
}
.edit-grid {
  flex: 1;
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 10px;
  font-size: 12px;
  color: var(--m-color-on-background-variant);
}
.edit-grid .wide { grid-column: 1 / -1; }
.chk { display: flex; align-items: center; gap: 6px; }
</style>
