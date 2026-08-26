<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from "vue";
import { api, errMsg, coverProxyUrl } from "@/api/client";
import { FALLBACK_COVER_SVG, onCoverError } from "@/utils/cover";
import LoadingImage from "@/components/LoadingImage.vue";

/**
 * 统一详情头部：书架/搜索/发现进入的详情页与阅读器内「书籍信息」弹层
 * 共用这一个组件 —— 两处入口看到的是同一个详情视图。
 * 布局用容器查询（container query）自适应宿主宽度：
 * 在 340px 抽屉里自动收窄堆叠，在整页里横向展开。
 */
const props = defineProps<{
  origin: string;
  bookUrl: string;
  name: string;
  author?: string;
  kind?: string;
  intro?: string;
  coverUrl?: string;
  lastChapter?: string;
  /** 已知章节数（目录就绪后传入）；空则不展示该项 */
  chapterCount?: number | null;
  /** 章节数备注（如「缓存」），紧跟在「共 N 章」后展示 */
  chapterCountNote?: string;
}>();

/* ---- 展示值：props 优先，缺简介时允许组件内补抓后覆盖 ---- */
const fetched = ref<{ intro?: string; kind?: string; lastChapter?: string }>({});
const displayIntro = computed(() => fetched.value.intro || props.intro || "");
const displayKind = computed(() => props.kind || fetched.value.kind || "");
const displayLast = computed(() => props.lastChapter || fetched.value.lastChapter || "");

/* ---- 背景虚化铺底淡入：缓存命中的 load 可能早于挂载，需同步探测 ---- */
const bgRef = ref<HTMLImageElement | null>(null);
const bgLoaded = ref(false);
function syncBgLoaded(): void {
  if (bgRef.value?.complete && bgRef.value.naturalWidth > 0) bgLoaded.value = true;
}
const kindChips = computed(() =>
  displayKind.value.split(/[,,、]/).map((s) => s.trim()).filter(Boolean).slice(0, 4),
);

/* ---- 简介缺失时按需从书源补一次详情，并写回本地缓存档案 ---- */
const introLoading = ref(false);
async function fetchIntro() {
  if (introLoading.value || !props.origin || !props.bookUrl) return;
  introLoading.value = true;
  try {
    const info = await api.bookInfo(
      props.origin, props.bookUrl, props.name, props.author ?? "", props.coverUrl ?? "",
    );
    const intro = (info.intro ?? "").trim();
    fetched.value = {
      intro,
      kind: info.kind?.trim() || "",
      lastChapter: info.lastChapter?.trim() || "",
    };
    // 持久化到书籍档案，下次直接缓存展示
    await api.resolveBook({
      sourceUrl: props.origin,
      bookUrl: props.bookUrl,
      name: props.name,
      author: props.author ?? "",
      coverUrl: info.coverUrl || props.coverUrl || "",
      intro,
      kind: fetched.value.kind,
      lastChapter: fetched.value.lastChapter,
      tocUrl: info.tocUrl?.trim() || "",
    });
  } catch (e) {
    alert(`简介获取失败：${errMsg(e)}`);
  } finally {
    introLoading.value = false;
  }
}

/* ---- 长简介折叠：被截断时给「展开全文 / 收起」 ---- */
const introEl = ref<HTMLElement | null>(null);
const introExpanded = ref(false);
const introClamped = ref(false);

function measureClamp() {
  const el = introEl.value;
  if (!el || introExpanded.value) {
    if (!introExpanded.value) introClamped.value = false;
    return;
  }
  introClamped.value = el.scrollHeight > el.clientHeight + 1;
}
watch([displayIntro, introExpanded], () => void nextTick(measureClamp));
onMounted(() => {
  measureClamp();
  syncBgLoaded();
  window.addEventListener("resize", measureClamp);
});
onBeforeUnmount(() => window.removeEventListener("resize", measureClamp));
</script>

<template>
  <section class="bd-hero">
    <img
      v-if="coverUrl"
      ref="bgRef"
      class="bd-bg"
      :class="{ 'is-loaded': bgLoaded }"
      :src="coverProxyUrl(coverUrl)"
      alt=""
      aria-hidden="true"
      @load="bgLoaded = true"
      @error="onCoverError($event, coverUrl)"
    >
    <div class="bd-scrim" aria-hidden="true"></div>

    <!-- 封面浮起带投影；大标题始终完整可读，不压入封面底下 -->
    <div class="bd-overlap">
      <LoadingImage
        class="bd-cover"
        :src="coverUrl ? coverProxyUrl(coverUrl) : FALLBACK_COVER_SVG"
        :alt="name"
        @error="onCoverError($event, coverUrl)"
      />
      <div class="bd-side">
        <h1 class="bd-title">{{ name }}</h1>
        <div class="bd-byline">{{ author || "佚名" }}</div>
        <div v-if="kindChips.length" class="bd-kinds">
          <span v-for="(k, i) in kindChips" :key="i" class="bd-chip">{{ k }}</span>
        </div>
        <div class="bd-stats">
          <span v-if="displayLast" class="bd-last">最新 {{ displayLast }}</span>
          <span v-if="chapterCount != null && chapterCount > 0">共 {{ chapterCount }} 章{{ chapterCountNote ? `（${chapterCountNote}）` : "" }}</span>
        </div>
      </div>
    </div>

    <h4 class="bd-sec">简介</h4>
    <p ref="introEl" class="bd-intro" :class="{ open: introExpanded }">
      {{ displayIntro || "暂无简介" }}
    </p>
    <div v-if="!displayIntro || introClamped || introExpanded" class="bd-intro-ops">
      <button
        v-if="!displayIntro"
        type="button"
        class="bd-mini-btn"
        :disabled="introLoading"
        @click="fetchIntro"
      >{{ introLoading ? "获取中…" : "从书源获取简介" }}</button>
      <button
        v-else
        type="button"
        class="bd-mini-btn"
        @click="introExpanded = !introExpanded"
      >{{ introExpanded ? "收起" : "展开全文" }}</button>
    </div>
  </section>
</template>

<style scoped>
.bd-hero {
  position: relative;
  overflow: hidden;
  isolation: isolate;
  container-type: inline-size;
  border-radius: var(--app-radius-card, 20px);
  background: var(--m-color-surface-container);
  padding: var(--bd-pad, 26px 30px 22px);
}

/* ---- 封面虚化铺底 + 渐变遮罩（Apple Music 式层次） ---- */
.bd-bg {
  position: absolute;
  inset: -70px;
  z-index: -2;
  width: calc(100% + 140px);
  height: calc(100% + 140px);
  object-fit: cover;
  filter: blur(58px) saturate(150%);
  opacity: 0;
  transition: opacity 0.6s ease;
}
.bd-bg.is-loaded {
  opacity: 0.42;
}
.bd-scrim {
  position: absolute;
  inset: 0;
  z-index: -1;
  background: linear-gradient(
    to right,
    color-mix(in srgb, var(--m-color-surface-container) 96%, transparent) 30%,
    color-mix(in srgb, var(--m-color-surface-container) 55%, transparent)
  );
}
@media (prefers-reduced-transparency: reduce) {
  .bd-bg,
  .bd-scrim {
    display: none;
  }
}

/* ---- 封面 × 大标题 ---- */
.bd-overlap {
  /* 封面宽随宿主宽度伸缩 */
  --_cvw: clamp(96px, 26cqw, 148px);
  display: flex;
  align-items: flex-end;
  gap: 18px;
}
.bd-cover {
  width: var(--_cvw);
  aspect-ratio: 27 / 38;
  border-radius: 12px;
  object-fit: cover;
  background: var(--m-color-surface-container-high);
  flex: none;
  box-shadow:
    0 4px 10px rgba(0, 0, 0, 0.18),
    0 18px 36px -14px rgba(0, 0, 0, 0.4);
}
.bd-side {
  min-width: 0;
  flex: 1;
}
.bd-title {
  /* 标题完整可见：不做负缩进，避免行首字符被封面盖住 */
  margin: 0 0 8px;
  font-size: clamp(24px, 7.4cqw, 46px);
  line-height: 1.08;
  letter-spacing: -0.02em;
  font-weight: 800;
  color: var(--m-color-on-surface);
  overflow-wrap: anywhere;
  display: -webkit-box;
  -webkit-line-clamp: 3;
  -webkit-box-orient: vertical;
  overflow: hidden;
}
.bd-byline {
  color: var(--m-color-on-surface-secondary);
  font-size: 14px;
  margin-bottom: 8px;
}
.bd-kinds {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  margin-bottom: 10px;
}
.bd-chip {
  font-size: 11px;
  padding: 2.5px 10px;
  border-radius: 999px;
  background: var(--m-color-tertiary-container);
  color: var(--m-color-on-tertiary-container);
}
.bd-stats {
  display: flex;
  flex-wrap: wrap;
  gap: 4px 14px;
  font-size: 12px;
  color: var(--m-color-outline);
}
.bd-stats span {
  min-width: 0;
  max-width: 100%;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

/* ---- 简介 ---- */
.bd-sec {
  margin: 16px 0 6px;
  font-size: 13px;
  font-weight: 600;
  color: var(--m-color-on-surface);
}
.bd-intro {
  margin: 0;
  color: var(--m-color-on-background-variant);
  font-size: 13px;
  line-height: 1.7;
  white-space: pre-wrap;
  word-break: break-word;
  display: -webkit-box;
  -webkit-line-clamp: 3;
  -webkit-box-orient: vertical;
  overflow: hidden;
}
.bd-intro.open {
  display: block;
}
.bd-intro-ops {
  margin-top: 8px;
}
.bd-mini-btn {
  border: 0;
  background: var(--m-color-surface-container-high);
  color: var(--m-color-primary);
  border-radius: 999px;
  padding: 5px 14px;
  font-size: 12px;
  cursor: pointer;
  font-family: inherit;
  transition: background 0.15s ease;
}
.bd-mini-btn:hover:not(:disabled) {
  background: var(--m-color-surface-container-highest, var(--m-color-surface-container-high));
}
.bd-mini-btn:disabled {
  opacity: 0.5;
  cursor: default;
}

/* ---- 窄宿主（阅读器抽屉 ~340px、手机整页）：纵向堆叠 ---- */
@container (max-width: 479px) {
  .bd-scrim {
    background: linear-gradient(
      to bottom,
      color-mix(in srgb, var(--m-color-surface-container) 96%, transparent) 20%,
      color-mix(in srgb, var(--m-color-surface-container) 55%, transparent)
    );
  }
  .bd-overlap {
    flex-direction: column;
    align-items: center;
    gap: 12px;
    text-align: center;
  }
  .bd-title {
    margin: 0 0 6px;
  }
  .bd-side {
    display: flex;
    flex-direction: column;
    align-items: center;
  }
  .bd-kinds {
    justify-content: center;
  }
  .bd-stats {
    justify-content: center;
  }
}
</style>
