<script setup lang="ts">
import { nextTick, onMounted, ref, watch } from "vue";

/**
 * 带加载占位的图片（封面等 <img> 的统一替换件）：
 * - 未就绪：占位底色上一道微光扫过（纯 transform 动画，主线程忙也不掉帧）；
 * - 就绪：整图淡入；
 * - 缓存命中：挂载时同步检测 complete，直接就绪，不播占位不装忙；
 * - error 原样上抛，页面里继续走统一的 onCoverError 代理重试兜底。
 */
const props = defineProps<{
  src: string;
  alt?: string;
  /** 首屏关键图可传 true 立即加载；默认懒加载 */
  eager?: boolean;
}>();

const emit = defineEmits<{ (e: "error", evt: Event): void }>();

const el = ref<HTMLImageElement | null>(null);
const ready = ref(false);

/** 缓存命中的图片 load 事件可能早于 Vue 挂载已发生，只能同步探测。 */
function syncComplete(): void {
  if (el.value?.complete && el.value.naturalWidth > 0) ready.value = true;
}

onMounted(syncComplete);

/* 同一组件被复用换 src（如详情页补抓到新封面）时回到未就绪态再探测。 */
watch(
  () => props.src,
  async () => {
    ready.value = false;
    await nextTick();
    syncComplete();
  },
);

function onLoad(): void {
  ready.value = true;
}

function onError(evt: Event): void {
  emit("error", evt);
}
</script>

<template>
  <span class="li-box" :class="{ 'is-ready': ready }">
    <img
      ref="el"
      class="li-img"
      :src="src"
      :alt="alt ?? ''"
      :loading="eager ? 'eager' : 'lazy'"
      decoding="async"
      @load="onLoad"
      @error="onError"
    >
  </span>
</template>
