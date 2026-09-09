<script setup lang="ts">
import { onBeforeUnmount, onMounted, ref, watch } from "vue";
import { useRouter } from "vue-router";
import { MiuixButton } from "miuix-vue";
import { api, errMsg } from "@/api/client";
import type { PluginUiPayload } from "@/api/client";
import { showAlert, showConfirm } from "@/services/appDialog";

const props = defineProps<{ name: string }>();
const router = useRouter();
const frame = ref<HTMLIFrameElement | null>(null);
const payload = ref<PluginUiPayload | null>(null);
const srcdoc = ref("");
const loading = ref(true);
const error = ref("");

const CHANNEL = "viewer-plugin-ui-v1";

function viewerBaseUrl(): string {
  if (import.meta.env.DEV) return `${location.protocol}//${location.hostname}:8000`;
  return location.origin;
}

function withRuntime(html: string, item: PluginUiPayload): string {
  const context = JSON.stringify({
    name: item.name,
    title: item.title,
    baseUrl: viewerBaseUrl(),
  }).replace(/</g, "\\u003c");
  const runtime = `<script>
    (() => {
      const channel = ${JSON.stringify(CHANNEL)};
      const pending = new Map();
      let sequence = 0;
      addEventListener("message", (event) => {
        const value = event.data;
        if (event.source !== parent || !value || value.channel !== channel || value.type !== "response") return;
        const task = pending.get(value.id);
        if (!task) return;
        pending.delete(value.id);
        clearTimeout(task.timer);
        if (value.ok) task.resolve(value.data);
        else task.reject(new Error(value.error || "插件请求失败"));
      });
      window.viewerPlugin = Object.freeze({
        context: Object.freeze(${context}),
        send(type, payload) {
          return new Promise((resolve, reject) => {
            const id = String(Date.now()) + "-" + String(++sequence);
            const timer = setTimeout(() => {
              pending.delete(id);
              reject(new Error(type === "request" ? "插件请求超时" : "弹窗响应超时"));
            }, 65000);
            pending.set(id, { resolve, reject, timer });
            parent.postMessage({ channel, type, id, ...payload }, "*");
          });
        },
        request(method, path, body) {
          return this.send("request", { method, path, body });
        },
        alert(message, options) {
          return this.send("dialog", { kind: "alert", message, options });
        },
        confirm(message, options) {
          return this.send("dialog", { kind: "confirm", message, options });
        },
        close() { parent.postMessage({ channel, type: "close" }, "*"); },
      });
    })();
  <\/script>`;
  const head = html.match(/<head(?:\s[^>]*)?>/i);
  if (head?.index !== undefined) {
    const at = head.index + head[0].length;
    return html.slice(0, at) + runtime + html.slice(at);
  }
  return runtime + html;
}

async function load() {
  loading.value = true;
  error.value = "";
  payload.value = null;
  srcdoc.value = "";
  try {
    const item = await api.pluginUi(props.name);
    payload.value = item;
    srcdoc.value = withRuntime(item.html, item);
  } catch (e) {
    error.value = errMsg(e);
  } finally {
    loading.value = false;
  }
}

async function onMessage(event: MessageEvent) {
  if (event.source !== frame.value?.contentWindow) return;
  const value = event.data;
  if (!value || value.channel !== CHANNEL) return;
  if (value.type === "close") {
    await router.push("/admin/plugins");
    return;
  }
  if (typeof value.id !== "string") return;
  const target = frame.value?.contentWindow;
  if (value.type === "dialog") {
    const options = value.options && typeof value.options === "object"
      ? value.options
      : {};
    try {
      const confirmed = value.kind === "confirm"
        ? await showConfirm(value.message, options)
        : (await showAlert(value.message, options), true);
      target?.postMessage({
        channel: CHANNEL,
        type: "response",
        id: value.id,
        ok: true,
        data: confirmed,
      }, "*");
    } catch (e) {
      target?.postMessage({
        channel: CHANNEL,
        type: "response",
        id: value.id,
        ok: false,
        error: errMsg(e),
      }, "*");
    }
    return;
  }
  if (value.type !== "request") return;
  try {
    const data = await api.pluginUiRequest(
      payload.value?.apiBase ?? null,
      String(value.method || ""),
      String(value.path || ""),
      value.body,
    );
    target?.postMessage({ channel: CHANNEL, type: "response", id: value.id, ok: true, data }, "*");
  } catch (e) {
    target?.postMessage({
      channel: CHANNEL,
      type: "response",
      id: value.id,
      ok: false,
      error: errMsg(e),
    }, "*");
  }
}

onMounted(() => {
  window.addEventListener("message", onMessage);
  load();
});
onBeforeUnmount(() => window.removeEventListener("message", onMessage));
watch(() => props.name, load);
</script>

<template>
  <div class="plugin-ui-page">
    <div class="bar">
      <div>
        <button class="back" type="button" @click="router.push('/admin/plugins')">← 插件管理</button>
        <h2 class="page-title">{{ payload?.title || "插件界面" }}</h2>
      </div>
      <MiuixButton v-if="error" @click="load">重新加载</MiuixButton>
    </div>
    <p v-if="loading" class="state">正在加载插件界面…</p>
    <p v-else-if="error" class="state error">{{ error }}</p>
    <iframe
      v-else
      ref="frame"
      class="plugin-frame"
      :title="payload?.title || '插件界面'"
      :srcdoc="srcdoc"
      sandbox="allow-scripts allow-forms"
    />
  </div>
</template>

<style scoped>
.plugin-ui-page { min-height: calc(100vh - 72px); }
.bar { display: flex; align-items: end; justify-content: space-between; gap: 16px; margin-bottom: 14px; }
.back { border: 0; padding: 0; color: var(--m-color-primary); background: transparent; font: inherit; cursor: pointer; }
.page-title { margin: 7px 0 0; }
.state { padding: 40px 16px; color: var(--m-color-on-surface-secondary); text-align: center; }
.state.error { color: var(--m-color-error); }
.plugin-frame { display: block; box-sizing: border-box; width: 100%; min-height: calc(100vh - 170px); border: 0; border-radius: 20px; background: var(--m-color-surface); }
</style>
