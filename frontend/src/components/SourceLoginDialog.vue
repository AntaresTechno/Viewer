<script setup lang="ts">
/**
 * 书源登录对话框（legado 书源）。
 *
 * 对齐 legado 的 SourceLogin 流程：
 * - form 模式：按 loginUi 渲染表单（text/password/select/toggle/button），
 *   「登录」= 保存 loginInfo 并执行书源 login() JS，展示 JS 日志；
 * - web 模式：服务端无 WebView，给出登录网址 + 手工粘贴 Cookie；
 * - 登录头 / Cookie 状态展示与清除。
 */
import { reactive, ref, watch } from "vue";
import {
  MiuixButton,
  MiuixDialog,
  MiuixInput,
} from "miuix-vue";
import { api, errMsg } from "@/api/client";
import type { LoginForm, LoginRow } from "@/api/client";
import PasswordField from "@/components/PasswordField.vue";

const props = defineProps<{
  modelValue: boolean;
  sourceUrl: string;
  sourceName: string;
}>();
const emit = defineEmits<{ "update:modelValue": [v: boolean] }>();

const loading = ref(false);
const busy = ref(false);
const error = ref("");
const form = ref<LoginForm | null>(null);
const values = reactive<Record<string, string>>({});
const log = ref<string[]>([]);
const cookieInput = ref("");
const showHeader = ref(false);

watch(
  () => props.modelValue,
  (open) => {
    if (open) void load();
    else closeInner();
  },
);

function closeInner() {
  form.value = null;
  error.value = "";
  log.value = [];
  cookieInput.value = "";
  showHeader.value = false;
}

async function load() {
  loading.value = true;
  error.value = "";
  log.value = [];
  try {
    const f = await api.legadoLoginForm(props.sourceUrl);
    form.value = f;
    for (const k of Object.keys(values)) delete values[k];
    Object.assign(values, f.values);
    cookieInput.value = f.cookie || "";
  } catch (e) {
    error.value = errMsg(e);
  } finally {
    loading.value = false;
  }
}

async function runBusy(fn: () => Promise<void>) {
  busy.value = true;
  error.value = "";
  try {
    await fn();
  } catch (e) {
    error.value = errMsg(e);
  } finally {
    busy.value = false;
  }
}

function applyResult(res: {
  error?: string | null;
  log?: string[];
  values?: Record<string, string>;
  openUrl?: string | null;
}) {
  log.value = res.log ?? [];
  if (res.error) error.value = res.error;
  if (res.values) {
    for (const k of Object.keys(values)) delete values[k];
    Object.assign(values, res.values);
  }
  if (res.openUrl) window.open(res.openUrl, "_blank");
  void refreshStatus();
}

async function refreshStatus() {
  try {
    const f = await api.legadoLoginForm(props.sourceUrl);
    form.value = f;
    if (Object.keys(values).length === 0) Object.assign(values, f.values);
  } catch {
    /* 状态刷新失败不打断主流程 */
  }
}

const doLogin = () =>
  runBusy(async () => {
    applyResult(await api.legadoLoginSubmit(props.sourceUrl, { ...values }));
  });

const doRowAction = (row: LoginRow) =>
  runBusy(async () => {
    applyResult(await api.legadoLoginAction(props.sourceUrl, row.name));
  });

const doSaveCookie = () =>
  runBusy(async () => {
    const r = await api.legadoLoginCookie(
      props.sourceUrl,
      cookieInput.value.trim(),
    );
    cookieInput.value = r.cookie;
  });

const doRemoveHeader = () =>
  runBusy(async () => {
    await api.legadoLoginHeaderRemove(props.sourceUrl);
    await refreshStatus();
    cookieInput.value = form.value?.cookie || "";
  });

const doLogout = () =>
  runBusy(async () => {
    applyResult(await api.legadoLoginSubmit(props.sourceUrl, null));
    await refreshStatus();
  });

function toggleValue(row: LoginRow) {
  const chars = row.chars ?? [];
  const off = chars[0] ?? "false";
  const on = chars[1] ?? "true";
  values[row.name] = values[row.name] === on ? off : on;
}

function openWebUrl() {
  if (form.value?.webUrl) window.open(form.value.webUrl, "_blank");
}
</script>

<template>
  <MiuixDialog
    :model-value="modelValue"
    :title="`登录书源：${sourceName || sourceUrl}`"
    @update:model-value="emit('update:modelValue', $event)"
  >
    <div class="dlg">
      <div v-if="loading" class="empty">加载中…</div>
      <template v-else-if="form">
        <div v-if="form.mode === 'none'" class="empty">
          该书源未配置登录（无 loginUrl / loginUi）。
        </div>

        <!-- 登录表单（loginUi） -->
        <template v-if="form.mode === 'form'">
          <div v-for="row in form.rows" :key="row.name" class="row">
            <MiuixInput
              v-if="row.type === 'text'"
              v-model="values[row.name]"
              :label="row.title"
              single-line
            />
            <PasswordField
              v-else-if="row.type === 'password'"
              v-model="values[row.name]"
              :label="row.title"
            />
            <div v-else-if="row.type === 'select'" class="sel-wrap">
              <label class="row-label" :for="`sel-${row.name}`">{{ row.title }}</label>
              <div class="sel-outer">
                <select
                  :id="`sel-${row.name}`"
                  v-model="values[row.name]"
                  class="sel"
                >
                  <option v-for="c in row.chars ?? []" :key="c" :value="c">{{ c }}</option>
                </select>
                <span class="caret" aria-hidden="true"></span>
              </div>
            </div>
            <div v-else-if="row.type === 'toggle'" class="toggle-row">
              <span class="row-label">{{ row.title }}</span>
              <button type="button" class="tgl" @click="toggleValue(row)">
                {{ values[row.name] || row.chars?.[0] || "false" }}
              </button>
            </div>
            <div v-else-if="row.type === 'button'" class="btn-row">
              <MiuixButton
                type="secondary"
                :disabled="busy"
                @click="doRowAction(row)"
              >{{ row.title }}</MiuixButton>
            </div>
          </div>

          <MiuixButton type="primary" class="login-btn" :disabled="busy" @click="doLogin">
            {{ busy ? "登录中…" : "登 录" }}
          </MiuixButton>
        </template>

        <!-- Web 模式：登录网址 + 手工 Cookie -->
        <template v-else-if="form.mode === 'web'">
          <p class="hint">
            该书源需要网页登录。服务端无法渲染网页，请打开
            <a :href="form.webUrl || '#'" target="_blank" rel="noopener">登录网址</a>
            完成登录后，从浏览器复制 Cookie 粘贴保存。
          </p>
          <div v-if="form.webUrl" class="btn-row">
            <MiuixButton type="secondary" @click="openWebUrl">
              打开登录页
            </MiuixButton>
          </div>
          <textarea
            v-model="cookieInput"
            rows="3"
            placeholder="粘贴 Cookie，如：uid=1; token=abc"
          ></textarea>
          <div class="btn-row">
            <MiuixButton type="primary" :disabled="busy" @click="doSaveCookie">
              保存 Cookie
            </MiuixButton>
          </div>
        </template>

        <!-- 登录头 / Cookie 状态 -->
        <div class="status">
          <span class="chip" :class="{ ok: form.hasInfo }">
            {{ form.hasInfo ? "已保存登录信息" : "未保存登录信息" }}
          </span>
          <span class="chip" :class="{ ok: form.hasLoginHeader }">
            {{ form.hasLoginHeader ? "有登录头" : "无登录头" }}
          </span>
          <button
            v-if="form.hasLoginHeader"
            class="linkbtn"
            type="button"
            @click="showHeader = !showHeader"
          >{{ showHeader ? "隐藏登录头" : "查看登录头" }}</button>
        </div>
        <pre v-if="showHeader && form.loginHeader" class="mono">{{ form.loginHeader }}</pre>

        <div v-if="error" class="err">{{ error }}</div>
        <pre v-if="log.length" class="mono log">{{ log.join("\n") }}</pre>
      </template>
    </div>

    <div class="dlg-actions">
      <button
        v-if="form?.hasLoginHeader"
        class="linkbtn danger"
        type="button"
        :disabled="busy"
        @click="doRemoveHeader"
      >清除登录头</button>
      <button
        v-if="form?.hasInfo"
        class="linkbtn danger"
        type="button"
        :disabled="busy"
        @click="doLogout"
      >退出登录</button>
      <span class="spacer"></span>
      <MiuixButton @click="emit('update:modelValue', false)">关闭</MiuixButton>
    </div>
  </MiuixDialog>
</template>

<style scoped>
.dlg {
  width: 100%;
  display: flex;
  flex-direction: column;
  gap: 10px;
}
.row {
  display: flex;
  flex-direction: column;
}
.row-label {
  font-size: 12px;
  color: var(--m-color-on-surface-secondary);
  margin-bottom: 4px;
}
.sel-wrap {
  display: flex;
  flex-direction: column;
}
.sel-outer {
  position: relative;
  display: inline-flex;
  align-items: center;
  max-width: 100%;
}
.sel {
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
  width: 100%;
}
.sel:focus-visible {
  outline: none;
  border-color: var(--m-color-primary);
  box-shadow: 0 0 0 1px var(--m-color-primary);
}
.caret {
  position: absolute;
  right: 13px;
  width: 8px;
  height: 8px;
  border-right: 2px solid var(--m-color-on-surface-secondary);
  border-bottom: 2px solid var(--m-color-on-surface-secondary);
  transform: translateY(-2px) rotate(45deg);
  pointer-events: none;
}
.toggle-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
}
.tgl {
  border: 1px solid var(--m-color-outline);
  background: transparent;
  color: var(--m-color-on-surface);
  border-radius: 999px;
  font: inherit;
  font-size: 13px;
  padding: 6px 16px;
  cursor: pointer;
}
.btn-row {
  display: flex;
  gap: 8px;
}
.login-btn {
  margin-top: 4px;
}
.status {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 8px;
}
.chip {
  display: inline-block;
  padding: 2px 10px;
  border-radius: 999px;
  background: var(--m-color-secondary-container);
  color: var(--m-color-on-secondary-container);
  font-size: 12px;
  opacity: 0.6;
}
.chip.ok {
  opacity: 1;
}
.hint {
  font-size: 12px;
  color: var(--m-color-on-background-variant);
}
textarea {
  width: 100%;
  font-family: Consolas, monospace;
  font-size: 12px;
}
.mono {
  font-family: Consolas, monospace;
  font-size: 12px;
  background: var(--m-color-surface-container, rgba(127, 127, 127, 0.08));
  border-radius: 8px;
  padding: 8px 10px;
  margin: 0;
  white-space: pre-wrap;
  word-break: break-all;
  max-height: 160px;
  overflow: auto;
}
.log {
  color: var(--m-color-on-surface-secondary);
}
.err {
  font-size: 13px;
  color: var(--m-color-error, #b3261e);
}
.empty {
  text-align: center;
  font-size: 13px;
  color: var(--m-color-on-surface-secondary);
  padding: 12px 0;
}
.dlg-actions {
  display: flex;
  align-items: center;
  gap: 12px;
}
.linkbtn {
  border: 0;
  background: transparent;
  color: var(--m-color-primary);
  font: inherit;
  font-size: 13px;
  cursor: pointer;
  padding: 4px 2px;
}
.linkbtn:hover {
  text-decoration: underline;
}
.linkbtn.danger {
  color: var(--m-color-error, #b3261e);
}
.spacer {
  flex: 1;
}
</style>
