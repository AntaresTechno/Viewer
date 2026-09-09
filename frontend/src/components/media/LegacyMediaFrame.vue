<script setup lang="ts">
/**
 * 旧式 HTML 播放器沙箱（plan §7.2）。
 * 只在 <iframe srcdoc> 内以 「allow-scripts allow-presentation」运行源 HTML，
 * 不授予 same-origin / top-navigation，主站 JWT 与本地存储不可被源脚本读取。
 * 通过 postMessage 与父页面交换：进度键值(容量受限)/媒体播放事件。
 */
import { onBeforeUnmount, onMounted, ref } from "vue";

const props = defineProps<{
  html: string;
  iframeKey?: string;
  restore?: Record<string, unknown>;
  /** 视频/音频媒体事件上报频率上限（秒）。 */
  reportSeconds?: number;
}>();

const emit = defineEmits<{
  (e: "warn", msg: string): void;
  (e: "media", msg: { kind: string; s: number; d: number; url: string; ep: string }): void;
  (e: "kvchange", kv: Record<string, unknown>): void;
  (e: "ready"): void;
}>();

const frame = ref<HTMLIFrameElement | null>(null);
const kv = ref<Record<string, unknown>>({ ...(props.restore || {}) });
const lastReport = ref(0);

function handleMessage(ev: MessageEvent) {
  const win = frame.value?.contentWindow;
  if (!win || ev.source !== win) return;
  const data = ev.data;
  if (!data || typeof data !== "object" || typeof data.__dsh__ !== "string") return;
  const kind = data.__dsh__;
  if (kind === "boot" || kind === "ready") {
    // 回推恢复状态，让播放器接着上次的位置/集数
    sendRestore();
    emit("ready");
    return;
  }
  if (kind === "kv") {
    if (data.t === "set" && typeof data.k === "string") kv.value = { ...kv.value, [data.k]: data.v };
    else if (data.t === "remove" && typeof data.k === "string") {
      const next = { ...kv.value };
      delete next[data.k];
      kv.value = next;
    } else if (data.t === "clear") {
      kv.value = {};
    }
    onKvChange();
    return;
  }
  if (kind === "media") {
    const now = Date.now();
    if (now - lastReport.value < (props.reportSeconds ?? 5) * 1000) return;
    lastReport.value = now;
    emit("media", {
      kind: String(data.kind || ""),
      s: Number(data.s || 0),
      d: Number(data.d || 0),
      url: String(data.url || ""),
      ep: String(data.ep || ""),
    });
    return;
  }
}

function sendRestore() {
  const win = frame.value?.contentWindow;
  if (!win) return;
  try {
    win.postMessage({ __dsh_restore__: { kv: kv.value } }, "*");
  } catch {
    /* ignore */
  }
}

/* debounce 保存事件，避免高频写库 */
let kvTimer = 0;
function onKvChange() {
  window.clearTimeout(kvTimer);
  kvTimer = window.setTimeout(() => emit("kvchange", kv.value), 300);
}

onMounted(() => window.addEventListener("message", handleMessage));
onBeforeUnmount(() => {
  window.removeEventListener("message", handleMessage);
  window.clearTimeout(kvTimer);
});

defineExpose({ postRestore: sendRestore, snapshot: () => kv.value });
</script>

<template>
  <div class="legacy-wrap">
    <iframe
      ref="frame"
      class="legacy-frame"
      :srcdoc="html"
      sandbox="allow-scripts allow-presentation"
      allow="autoplay; fullscreen; picture-in-picture"
      allowfullscreen
      frameborder="0"
    ></iframe>
  </div>
</template>

<style scoped>
.legacy-wrap {
  position: relative;
  width: 100%;
  height: 100%;
  background: #000;
}
.legacy-frame {
  width: 100%;
  height: 100%;
  border: 0;
  display: block;
}
</style>