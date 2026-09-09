<script setup lang="ts">
/**
 * FileDropOverlay — 把文件拖进整个窗口时，全屏展示一个有动画的“放下导入”提示。
 * 用 CSS spring 弹出中心卡片、缩放环 + 漂浮图标；松开即触发 @picked。
 */
import { onBeforeUnmount, onMounted, ref, watch } from "vue";

const props = defineProps<{
  /** 是否处于拖入状态（由本组件内部维护，v-model 同步给父级以便切换导入面板） */
  modelValue?: boolean;
  /** 接受的 MIME / 扩展名（默认 .json）。 */
  accept?: RegExp;
}>();

const emit = defineEmits<{
  (e: "update:modelValue", v: boolean): void;
  (e: "picked", text: string, fileName: string): void;
}>();

const active = ref(false);
let depth = 0;

function hasFiles(e: DragEvent): boolean {
  return !!e.dataTransfer && [...(e.dataTransfer.types || [])].some((t) => t === "Files");
}

function onDragEnter(e: DragEvent) {
  if (!hasFiles(e)) return;
  depth += 1;
  if (!active.value) {
    active.value = true;
    emit("update:modelValue", true);
  }
}
function onDragOver(e: DragEvent) {
  if (!active.value) return;
  // 必须 preventDefault，否则浏览器会拦截打开文件。
  e.preventDefault();
}
function onDragLeave(e: DragEvent) {
  depth -= 1;
  if (depth <= 0) {
    depth = 0;
    setActive(false);
  }
}
function onDrop(e: DragEvent) {
  depth = 0;
  setActive(false);
  const file = e.dataTransfer?.files?.[0];
  if (!file) return;
  const name = file.name;
  const okType = /\.(json|txt)$/i.test(name) || file.type.includes("json") || file.type.includes("text");
  if (props.accept && !props.accept.test(name)) return;
  if (!okType) return;
  file.text().then((text) => emit("picked", text, name)).catch(() => {});
}
function setActive(v: boolean) {
  active.value = v;
  emit("update:modelValue", v);
}

watch(
  () => props.modelValue,
  (v) => {
    if (v === true) active.value = true;
  },
);

onMounted(() => {
  window.addEventListener("dragenter", onDragEnter);
  window.addEventListener("dragover", onDragOver);
  window.addEventListener("dragleave", onDragLeave);
  window.addEventListener("drop", onDrop);
});
onBeforeUnmount(() => {
  window.removeEventListener("dragenter", onDragEnter);
  window.removeEventListener("dragover", onDragOver);
  window.removeEventListener("dragleave", onDragLeave);
  window.removeEventListener("drop", onDrop);
});

defineExpose({ sync: (v: boolean) => setActive(v) });
</script>

<template>
  <Transition name="fd-overlay">
    <div v-if="active" class="fd-overlay" @dragover.prevent @drop.prevent>
      <Transition name="fd-pop" appear>
        <div class="fd-card">
          <span class="fd-ring"><span class="fd-ring2"></span></span>
          <span class="fd-ico">
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6"
                 stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
              <path d="M12 16V4"/>
              <path d="m6 9 6-6 6 6"/>
              <path d="M4 15v3a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2v-3"/>
            </svg>
          </span>
          <span class="fd-title">松开鼠标，导入媒体源</span>
          <span class="fd-sub">支持 .json 文件（Legado RssSource / BookSource）</span>
        </div>
      </Transition>
    </div>
  </Transition>
</template>

<style scoped>
.fd-overlay {
  position: fixed;
  inset: 0;
  /* 高于 miuix 对话框遮罩的 z-index:1000，保证拖入提示出现在弹窗之上 */
  z-index: 1500;
  display: grid;
  place-items: center;
  background: color-mix(in srgb, var(--app-color-scrim, #000) 45%, transparent);
  backdrop-filter: blur(3px);
}
.fd-card {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 10px;
  padding: 42px 58px;
  border-radius: var(--app-shape-xl, 24px);
  background: var(--app-color-surface-2, #fff);
  box-shadow: var(--app-shadow-2, 0 10px 40px rgba(0,0,0,.25));
  position: relative;
  overflow: hidden;
  text-align: center;
}
.fd-ico {
  width: 64px;
  height: 64px;
  border-radius: 50%;
  display: grid;
  place-items: center;
  color: var(--app-color-primary, #6750a4);
  background: var(--app-color-primary-container, #eaddff);
  animation: fd-float 1.6s ease-in-out infinite;
}
.fd-ico svg { width: 32px; height: 32px; }
.fd-ring { position: absolute; inset: 0; pointer-events: none; }
.fd-ring::before,
.fd-ring2::before {
  content: "";
  position: absolute;
  inset: 18%;
  border: 2px solid var(--app-color-primary, #6750a4);
  border-radius: 18px;
  opacity: 0.45;
  /* 呼吸灯：透明度 + 外发光平滑起伏，不再旋转 */
  animation: fd-breathe 2.8s ease-in-out infinite;
}
.fd-ring2::before {
  border-style: dotted;
  inset: 9%;
  animation-delay: -1.4s;
}
.fd-title { font-size: 18px; font-weight: 700; color: var(--app-color-text, #111); }
.fd-sub { font-size: 13px; color: var(--app-color-text-muted, #777); }

@keyframes fd-float {
  0%, 100% { transform: translateY(0) scale(1); }
  50% { transform: translateY(-8px) scale(1.06); }
}
@keyframes fd-breathe {
  0%, 100% {
    opacity: 0.25;
    box-shadow: 0 0 8px 0 color-mix(in srgb, var(--app-color-primary, #6750a4) 22%, transparent);
  }
  50% {
    opacity: 0.95;
    box-shadow: 0 0 20px 4px color-mix(in srgb, var(--app-color-primary, #6750a4) 42%, transparent);
  }
}

.fd-overlay-enter-active,
.fd-overlay-leave-active { transition: opacity .2s ease; }
.fd-overlay-enter-from,
.fd-overlay-leave-to { opacity: 0; }
.fd-pop-enter-active { transition: transform var(--app-dur-spring, .5s) var(--app-ease-spring, cubic-bezier(.3,1.12,.4,1)), opacity .18s ease; }
.fd-pop-enter-from { transform: scale(.7) translateY(20px); opacity: 0; }

@media (prefers-reduced-motion: reduce) {
  .fd-ico, .fd-ring::before, .fd-ring2::before { animation: none; }
  .fd-pop-enter-active, .fd-overlay-enter-active, .fd-overlay-leave-active { transition: none; }
}
</style>