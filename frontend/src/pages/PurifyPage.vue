<script setup lang="ts">
import { computed, onMounted, reactive, ref } from "vue";
import {
  MiuixButton,
  MiuixCard,
  MiuixCheckbox,
  MiuixInput,
  MiuixText,
} from "miuix-vue";
import { api, errMsg } from "@/api/client";
import type {
  PurifyCatalogItem,
  PurifyPack,
  PurifyRuleItem,
} from "@/api/client";
import { useAuth } from "@/stores/auth";

/**
 * 正文净化插件管理页。三个规则来源：
 * 1. 内置净化 · MD3 版（引擎内置层，默认生效）；
 * 2. 乌云净化（3f067eb2.json 成包，安装后选择性开启）；
 * 3. 自定义规则（上传 JSON / URL 拉取 / 粘贴导入）。
 * 阅读管线（/books/content）在插件启用时自动走「获取 → 净化 → 存库 → 调用」。
 */

const auth = useAuth();
const canManage = () => auth.can("purify.manage");
const canClearCache = () =>
  auth.can("purify.cache.manage") || auth.isSuperuser;

const catalog = ref<PurifyCatalogItem[]>([]);
const packs = ref<PurifyPack[]>([]);
const stats = ref<{
  chapters: number;
  rawBytes: number;
  contentBytes: number;
  booksTotal: number;
  books: PurifyCacheBook[];
} | null>(null);
const loading = ref(true);
const error = ref("");
const msg = ref("");

interface PurifyCacheBook {
  sourceUrl: string;
  bookUrl: string;
  name: string;
  chapters: number;
}

const builtinItem = computed(() =>
  catalog.value.find((c) => c.key === "builtin-md3"),
);
const wuyunItem = computed(() =>
  catalog.value.find((c) => c.key === "wuyun"),
);

function fmtBytes(n: number): string {
  if (!n) return "0";
  if (n < 1024) return `${n} 字`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} K字`;
  return `${(n / 1024 / 1024).toFixed(2)} M字`;
}

async function load() {
  loading.value = true;
  error.value = "";
  try {
    const [cat, p] = await Promise.all([api.purifyCatalog(), api.purifyPacks()]);
    catalog.value = cat.items;
    packs.value = p.items;
    try {
      stats.value = await api.purifyCacheStats();
    } catch {
      stats.value = null; // 无权限时静默
    }
  } catch (e) {
    error.value = errMsg(e);
  } finally {
    loading.value = false;
  }
}
onMounted(load);

/* ------------------------------------------------------ 乌云净化：装/开 */
async function installWuyun() {
  msg.value = "";
  try {
    const r = await api.purifyInstallPreset("wuyun");
    msg.value = r.installed
      ? `已安装 ${r.rules ?? "?"} 条，点击「开启」生效`
      : "乌云净化已安装过";
    await load();
  } catch (e) {
    error.value = errMsg(e);
  }
}

async function toggleWuyunPack() {
  const item = wuyunItem.value;
  if (!item?.packId) return;
  try {
    const r = await api.purifyTogglePack(item.packId);
    await load();
    msg.value = r.enabled ? "乌云净化已开启" : "乌云净化已停用";
  } catch (e) {
    error.value = errMsg(e);
  }
}

async function removeWuyunPack() {
  const item = wuyunItem.value;
  if (!item?.packId) return;
  if (!confirm("卸载乌云净化规则包？（缓存会在下次阅读时自动重建）")) return;
  try {
    await api.purifyDeletePack(item.packId);
    msg.value = "乌云净化已卸载";
    await load();
  } catch (e) {
    error.value = errMsg(e);
  }
}

/* --------------------------------------------------- 自定义：文件/URL/粘贴 */
const importText = ref("");
const importName = ref("");
const importUrl = ref("");
const importUrlName = ref("");
const fileEl = ref<HTMLInputElement | null>(null);

function pickFile() {
  fileEl.value?.click();
}

async function onFileChosen(e: Event) {
  const input = e.target as HTMLInputElement;
  const f = input.files?.[0];
  if (!f) return;
  msg.value = "";
  try {
    const r = await api.purifyImportFile(f, importName.value);
    msg.value = `已从文件导入「${r.imported}」条规则`;
    await load();
  } catch (e) {
    error.value = errMsg(e);
  } finally {
    input.value = "";
  }
}

async function doImportUrl() {
  msg.value = "";
  if (!importUrl.value.trim()) return;
  try {
    const r = await api.purifyImportUrl(importUrl.value.trim(), importUrlName.value);
    msg.value = `已从 URL 导入 ${r.imported} 条规则`;
    importUrl.value = "";
    importUrlName.value = "";
    await load();
  } catch (e) {
    error.value = errMsg(e);
  }
}

async function doImportPaste() {
  msg.value = "";
  if (!importText.value.trim()) return;
  try {
    const r = await api.purifyImportPack(importText.value, importName.value);
    msg.value = `已导入为规则包，共 ${r.imported} 条规则`;
    importText.value = "";
    importName.value = "";
    await load();
  } catch (e) {
    error.value = errMsg(e);
  }
}

/* ------------------------------------------------------------ 包操作 */
function originLabel(origin: string): string {
  if (origin.startsWith("preset:")) return "内置来源";
  if (origin === "import") return "自定义导入";
  if (origin.startsWith("import:")) return "URL 导入";
  if (origin === "manual") return "自建";
  return origin || "未知";
}

async function togglePack(p: PurifyPack) {
  try {
    const r = await api.purifyTogglePack(p.id);
    p.enabled = r.enabled;
  } catch (e) {
    error.value = errMsg(e);
  }
}

async function removePack(p: PurifyPack) {
  if (!confirm(`删除规则包「${p.name}」及其全部规则？`)) return;
  try {
    await api.purifyDeletePack(p.id);
    packs.value = packs.value.filter((x) => x.id !== p.id);
    await load();
  } catch (e) {
    error.value = errMsg(e);
  }
}

/* ------------------------------------------------------------ 规则编辑 */
const expandedId = ref<number | null>(null);
const rulesByPack = ref<Record<number, PurifyRuleItem[]>>({});
const editingRuleId = ref<number | null>(null);
const draft = reactive({
  name: "", order: 0, pattern: "",
  replacement: "", scope: "", regex: true, caseSensitive: true,
});
const addingFor = ref<number | null>(null);
const addDraft = reactive({
  name: "", order: 0, pattern: "",
  replacement: "", scope: "", regex: true, caseSensitive: true,
});

async function expand(p: PurifyPack) {
  if (expandedId.value === p.id) {
    expandedId.value = null;
    return;
  }
  expandedId.value = p.id;
  if (!rulesByPack.value[p.id]) {
    try {
      rulesByPack.value[p.id] = (await api.purifyRules(p.id)).items;
    } catch (e) {
      error.value = errMsg(e);
    }
  }
}

async function reloadRules(packId: number) {
  rulesByPack.value[packId] = (await api.purifyRules(packId)).items;
}

async function toggleRule(r: PurifyRuleItem) {
  try {
    const res = await api.purifyToggleRule(r.id);
    r.isActive = res.isActive;
  } catch (e) {
    error.value = errMsg(e);
  }
}

async function removeRule(r: PurifyRuleItem) {
  if (!confirm(`删除规则「${r.name || r.pattern.slice(0, 20)}」？`)) return;
  try {
    await api.purifyDeleteRules([r.id]);
    await reloadRules(r.packId);
  } catch (e) {
    error.value = errMsg(e);
  }
}

function startEdit(r: PurifyRuleItem) {
  editingRuleId.value = r.id;
  Object.assign(draft, {
    name: r.name, order: r.order, pattern: r.pattern,
    replacement: r.replacement, scope: r.scope,
    regex: r.regex, caseSensitive: r.caseSensitive,
  });
}

async function saveEdit() {
  if (editingRuleId.value == null) return;
  try {
    await api.purifyUpdateRule(editingRuleId.value, { ...draft });
    const pid = rulesByPack.value[editingRuleId.value]?.[0]?.packId;
    editingRuleId.value = null;
    if (pid != null) await reloadRules(pid);
  } catch (e) {
    error.value = errMsg(e);
  }
}

function openAdd(packId: number) {
  addingFor.value = packId;
  Object.assign(addDraft, {
    name: "", order: 0, pattern: "",
    replacement: "", scope: "", regex: true, caseSensitive: true,
  });
}

async function submitAdd() {
  if (addingFor.value == null || !addDraft.pattern.trim()) return;
  try {
    await api.purifyAddRule(addingFor.value, { ...addDraft });
    const pid = addingFor.value;
    addingFor.value = null;
    await reloadRules(pid);
    await load();
  } catch (e) {
    error.value = errMsg(e);
  }
}

/* ---------------------------------------------------------------- 试一试 */
const testIn = ref(
  "　　本章未做任何修改。天才一秒记住本站地址：www.example.com 阅读最新章节。",
);
const testOut = ref("");
const testApplied = ref<string[]>([]);

async function runTest() {
  try {
    const r = await api.purifyTest(testIn.value);
    testOut.value = r.content;
    testApplied.value = r.applied;
  } catch (e) {
    error.value = errMsg(e);
  }
}

/* ---------------------------------------------------------------- 缓存 */
const clearing = ref(false);
async function clearCache(bookUrl = "") {
  const label = bookUrl ? "该书" : "全部";
  if (!confirm(`确定清空${label}的净化缓存？下次阅读将重新获取并净化。`)) return;
  clearing.value = true;
  try {
    await api.purifyClearCache("", bookUrl);
    await load();
  } catch (e) {
    error.value = errMsg(e);
  } finally {
    clearing.value = false;
  }
}

const activeCount = computed(() => packs.value.filter((p) => p.enabled).length);
</script>

<template>
  <div>
    <h2 class="page-title">正文净化</h2>
    <p class="sub">
      章节正文先净化再入库，阅读时直接调用缓存；规则修改后缓存自动重建。
    </p>

    <!-- 统计 -->
    <MiuixCard v-if="stats" class="card" :show-indication="false">
      <div class="stat-row">
        <div class="stat"><b>{{ stats.chapters }}</b><span>缓存章节</span></div>
        <div class="stat"><b>{{ packs.length }}</b><span>规则包</span></div>
        <div class="stat"><b>{{ activeCount }}</b><span>启用中</span></div>
        <div class="stat"><b>{{ fmtBytes(stats.contentBytes) }}</b><span>净化文本</span></div>
        <MiuixButton
          v-if="canClearCache()"
          class="stat-clear"
          :disabled="clearing || !stats.chapters"
          @click="clearCache()"
        >清空缓存</MiuixButton>
      </div>
      <ul v-if="stats.books.length" class="cache-books">
        <li v-for="b in stats.books" :key="b.bookUrl + b.sourceUrl">
          <span class="cb-name">{{ b.name }}</span>
          <span class="cb-count">{{ b.chapters }} 章</span>
          <button
            v-if="canClearCache()"
            class="mini"
            @click="clearCache(b.bookUrl)"
          >清除</button>
        </li>
      </ul>
    </MiuixCard>

    <!-- 来源一：内置净化（MD3 版） -->
    <MiuixCard v-if="builtinItem" class="card source-card" :show-indication="false">
      <div class="src-head">
        <MiuixText type="title3">{{ builtinItem.title }}</MiuixText>
        <span class="badge on">默认启用</span>
      </div>
      <p class="hint">{{ builtinItem.description }}</p>
    </MiuixCard>

    <!-- 来源二：乌云净化（选择性开启） -->
    <MiuixCard v-if="wuyunItem" class="card source-card" :show-indication="false">
      <div class="src-head">
        <MiuixText type="title3">{{ wuyunItem.title }}</MiuixText>
        <span v-if="!wuyunItem.installed" class="badge">未安装</span>
        <span v-else-if="wuyunItem.packEnabled" class="badge on">已开启</span>
        <span v-else class="badge off">已停用</span>
      </div>
      <p class="hint">{{ wuyunItem.description }}</p>
      <div class="meta-row" v-if="wuyunItem.ruleCount">
        <span class="badge">{{ wuyunItem.ruleCount }} 条规则</span>
        <span class="badge">正文适用 {{ wuyunItem.contentRules }}</span>
        <span class="badge">JS 替换 {{ wuyunItem.jsRules }}</span>
        <span
          v-if="wuyunItem.jsRules && !wuyunItem.jsEngine"
          class="badge warn"
        >缺少 JS 引擎，JS 规则将跳过</span>
        <span
          v-for="(cnt, g) in wuyunItem.groups || {}"
          :key="g"
          class="badge grp"
        >{{ g }} × {{ cnt }}</span>
      </div>
      <div class="row" v-if="canManage()">
        <template v-if="!wuyunItem.installed">
          <MiuixButton type="primary" @click="installWuyun">安装规则包</MiuixButton>
        </template>
        <template v-else>
          <MiuixButton :type="wuyunItem.packEnabled ? 'default' : 'primary'"
                       @click="toggleWuyunPack">
            {{ wuyunItem.packEnabled ? "停用" : "开启" }}
          </MiuixButton>
          <MiuixButton @click="removeWuyunPack">卸载</MiuixButton>
        </template>
      </div>
    </MiuixCard>

    <!-- 来源三：自定义规则 -->
    <MiuixCard class="card" :show-indication="false">
      <MiuixText type="title3">自定义规则</MiuixText>
      <p class="hint">
        支持新旧两种 legado 替换规则导出格式；三种方式任选其一，导入为一个独立规则包。
      </p>

      <!-- 文件上传 -->
      <div class="custom-row">
        <span class="lbl">上传文件</span>
        <input
          ref="fileEl"
          type="file"
          accept=".json,application/json"
          style="display: none"
          @change="onFileChosen"
        />
        <MiuixButton size="small" :disabled="!canManage()" @click="pickFile">
          选择 .json 文件…
        </MiuixButton>
      </div>

      <!-- URL 拉取 -->
      <div class="custom-row">
        <span class="lbl">URL 拉取</span>
        <MiuixInput
          v-model="importUrl"
          single-line
          placeholder="https://example.com/wuyun.json"
          class="grow"
        />
        <MiuixButton size="small" :disabled="!canManage() || !importUrl.trim()"
                     @click="doImportUrl">
          拉取导入
        </MiuixButton>
      </div>

      <!-- 粘贴 -->
      <details class="paste-box">
        <summary>或直接粘贴 JSON…</summary>
        <textarea
          v-model="importText"
          rows="4"
          placeholder='[{"name":"去广告","group":"我的净化","pattern":"广告.*?结束","replacement":"","isRegex":true}]'
        />
        <div class="row">
          <MiuixButton type="primary" size="small"
                       :disabled="!canManage() || !importText.trim()"
                       @click="doImportPaste">
            导入为规则包
          </MiuixButton>
        </div>
      </details>
    </MiuixCard>

    <!-- 试一试 -->
    <MiuixCard class="card" :show-indication="false">
      <MiuixText type="title3">试一试</MiuixText>
      <textarea v-model="testIn" rows="2" />
      <div class="row">
        <MiuixButton @click="runTest">应用启用中的规则预览</MiuixButton>
        <span v-if="testApplied.length" class="applied">
          命中：{{ testApplied.join("、") }}
        </span>
      </div>
      <pre v-if="testOut" class="preview">{{ testOut }}</pre>
    </MiuixCard>

    <!-- 规则包列表 -->
    <MiuixCard class="card" :show-indication="false">
      <div class="head-row">
        <MiuixText type="title3">规则包（{{ packs.length }}）</MiuixText>
        <MiuixButton :disabled="loading" @click="load">刷新</MiuixButton>
      </div>
      <p v-if="error" class="err">{{ error }}</p>
      <span v-if="msg" class="ok">{{ msg }}</span>
      <div v-if="loading" class="center"><span class="spin" /></div>
      <p v-else-if="!packs.length" class="center dim">
        还没有规则包 —— 安装乌云净化或导入自定义规则开始。
      </p>

      <ul v-else class="packs">
        <li v-for="p in packs" :key="p.id">
          <div class="pack-main">
            <button class="pack-row" type="button" @click="expand(p)">
              <span class="caret" :class="{ open: expandedId === p.id }">›</span>
              <span class="name">{{ p.name }}</span>
              <span class="badge">{{ originLabel(p.origin) }}</span>
              <span class="badge">{{ p.ruleCount ?? 0 }} 条</span>
              <span v-if="!p.enabled" class="badge off">已停用</span>
            </button>
            <p v-if="p.description" class="p-desc">{{ p.description }}</p>

            <div v-if="expandedId === p.id" class="rules">
              <p
                v-if="!(rulesByPack[p.id] || []).length"
                class="dim center"
              >包内暂无规则。</p>
              <ul>
                <li v-for="r in rulesByPack[p.id] || []" :key="r.id">
                  <template v-if="editingRuleId === r.id">
                    <div class="edit-grid">
                      <label>名称<MiuixInput v-model="draft.name" single-line /></label>
                      <label>序号<MiuixInput v-model.number="draft.order" single-line /></label>
                      <label>作用范围(scope)<MiuixInput v-model="draft.scope" single-line /></label>
                      <label class="wide">匹配内容<textarea v-model="draft.pattern" rows="2" /></label>
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
                      <MiuixButton type="primary" size="small" @click="saveEdit">保存</MiuixButton>
                      <MiuixButton size="small" @click="editingRuleId = null">取消</MiuixButton>
                    </div>
                  </template>
                  <template v-else>
                    <div class="rule-main">
                      <div class="rule-top">
                        <span class="rname">{{ r.name || `#${r.id}` }}</span>
                        <span class="badge">#{{ r.order }}</span>
                        <span class="badge">{{ r.regex ? "正则" : "文本" }}</span>
                        <span v-if="r.replacement.toLowerCase().startsWith('@js:')"
                              class="badge">JS</span>
                        <span v-if="!r.isActive" class="badge off">已停用</span>
                        <span v-if="r.scope" class="scope" :title="r.scope">范围: {{ r.scope }}</span>
                      </div>
                      <code class="pat">{{ r.pattern }}</code>
                      <code v-if="r.replacement" class="rep">→ {{ r.replacement.slice(0, 160) }}{{ r.replacement.length > 160 ? "…" : "" }}</code>
                    </div>
                    <div class="rule-ops" v-if="canManage()">
                      <button class="mini" @click="toggleRule(r)">
                        {{ r.isActive ? "停用" : "启用" }}
                      </button>
                      <button class="mini" @click="startEdit(r)">编辑</button>
                      <button class="mini danger" @click="removeRule(r)">删除</button>
                    </div>
                  </template>
                </li>
              </ul>

              <!-- 新增规则 -->
              <div v-if="addingFor === p.id && canManage()" class="add-box">
                <div class="edit-grid">
                  <label>名称<MiuixInput v-model="addDraft.name" single-line /></label>
                  <label>序号<MiuixInput v-model.number="addDraft.order" single-line /></label>
                  <label>作用范围(scope)<MiuixInput v-model="addDraft.scope" single-line /></label>
                  <label class="wide">匹配内容<textarea v-model="addDraft.pattern" rows="2" /></label>
                  <label class="wide">替换为<textarea v-model="addDraft.replacement" rows="2" /></label>
                  <label class="chk">
                    <MiuixCheckbox :model-value="addDraft.regex"
                                   @update:model-value="addDraft.regex = $event" />
                    正则替换
                  </label>
                  <label class="chk">
                    <MiuixCheckbox :model-value="addDraft.caseSensitive"
                                   @update:model-value="addDraft.caseSensitive = $event" />
                    区分大小写
                  </label>
                </div>
                <div class="row">
                  <MiuixButton type="primary" size="small" @click="submitAdd">添加</MiuixButton>
                  <MiuixButton size="small" @click="addingFor = null">取消</MiuixButton>
                </div>
              </div>
              <div v-else-if="canManage()" class="row">
                <MiuixButton size="small" @click="openAdd(p.id)">＋ 新增规则</MiuixButton>
              </div>
            </div>
          </div>
          <div class="rule-ops" v-if="canManage()">
            <button class="mini" @click="togglePack(p)">
              {{ p.enabled ? "停用包" : "启用包" }}
            </button>
            <button class="mini danger" @click="removePack(p)">删除包</button>
          </div>
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
.row {
  display: flex;
  align-items: center;
  gap: 12px;
  margin-top: 10px;
  flex-wrap: wrap;
}
.ok { color: var(--m-color-primary); font-size: 13px; margin-left: 4px; }
.err { color: var(--m-color-error); font-size: 13px; display: block; margin-bottom: 8px; }
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

/* 统计条 */
.stat-row {
  display: flex;
  align-items: center;
  gap: 26px;
  flex-wrap: wrap;
}
.stat { display: grid; gap: 2px; }
.stat b { font-size: 20px; font-weight: 700; }
.stat span { font-size: 11px; color: var(--m-color-on-background-variant); }
.stat-clear { margin-left: auto; }
.cache-books {
  list-style: none;
  margin: 14px 0 0;
  padding: 0;
  border-top: 1px solid color-mix(in srgb, var(--m-color-outline) 45%, transparent);
}
.cache-books li {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 8px 2px;
  font-size: 13px;
}
.cb-name { flex: 1; min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.cb-count { color: var(--m-color-on-background-variant); font-size: 12px; }

/* 来源卡 */
.src-head {
  display: flex;
  align-items: center;
  gap: 10px;
  flex-wrap: wrap;
}
.meta-row {
  display: flex;
  align-items: center;
  gap: 6px;
  flex-wrap: wrap;
  margin: 4px 0 2px;
}
.badge {
  font-size: 11px;
  background: var(--m-color-surface-container-high);
  border-radius: 6px;
  padding: 1px 7px;
  color: var(--m-color-on-surface-secondary);
}
.badge.on { color: var(--m-color-primary); }
.badge.off { color: var(--m-color-error); }
.badge.warn { color: #b36b00; }

/* 自定义导入 */
.custom-row {
  display: flex;
  align-items: center;
  gap: 10px;
  margin: 10px 0;
}
.custom-row .lbl {
  font-size: 12px;
  color: var(--m-color-on-background-variant);
  width: 64px;
  flex: none;
}
.grow { flex: 1; min-width: 200px; }
.paste-box summary {
  font-size: 12px;
  color: var(--m-color-primary);
  cursor: pointer;
  user-select: none;
}
.paste-box textarea { margin-top: 8px; }

/* 规则包 */
.packs { list-style: none; margin: 0; padding: 0; }
.packs > li {
  padding: 12px 4px;
  border-bottom: 1px solid var(--m-color-outline-variant, rgba(0, 0, 0, 0.07));
  display: flex;
  gap: 10px;
  align-items: flex-start;
}
.pack-main { flex: 1; min-width: 0; }
.pack-row {
  display: flex;
  align-items: center;
  gap: 8px;
  width: 100%;
  border: 0;
  background: none;
  cursor: pointer;
  text-align: left;
  font-family: inherit;
  padding: 0;
  color: inherit;
}
.caret {
  display: inline-block;
  transition: transform 0.2s ease;
  color: var(--m-color-on-background-variant);
}
.caret.open { transform: rotate(90deg); }
.name { font-weight: 600; font-size: 14px; }

.rules {
  margin-top: 10px;
  border-top: 1px dashed color-mix(in srgb, var(--m-color-outline) 60%, transparent);
  padding-top: 4px;
}
.rules ul { list-style: none; margin: 0; padding: 0; }
.rules li {
  padding: 10px 2px;
  border-bottom: 1px solid color-mix(in srgb, var(--m-color-outline) 35%, transparent);
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
.rname { font-weight: 600; font-size: 13px; }
.scope {
  font-size: 11px;
  color: var(--m-color-on-background-variant);
  max-width: 220px;
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
.mini.danger { color: var(--m-color-error); border-color: var(--m-color-error); }
.edit-grid, .add-box {
  flex: 1;
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 10px;
  font-size: 12px;
  color: var(--m-color-on-background-variant);
}
.add-box { padding: 10px; border: 1px dashed var(--m-color-outline); border-radius: 12px; margin-top: 8px; }
.edit-grid .wide { grid-column: 1 / -1; }
.chk { display: flex; align-items: center; gap: 6px; }
</style>
