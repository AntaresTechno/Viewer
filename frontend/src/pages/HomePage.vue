<script setup lang="ts">
import { computed, onMounted, ref } from "vue";
import { useRouter } from "vue-router";
import { MiuixProgressIndicator } from "miuix-vue";
import { api, errMsg, coverProxyUrl } from "@/api/client";
import type { DailyPoint, HomeSummary } from "@/api/client";
import { FALLBACK_COVER_SVG, onCoverError } from "@/utils/cover";
import { openReader } from "@/utils/reader";
import { useAuth } from "@/stores/auth";

const $router = useRouter();
const auth = useAuth();

const loading = ref(true);
const error = ref("");
const summary = ref<HomeSummary | null>(null);
const daily = ref<DailyPoint[]>([]);

onMounted(load);

async function load() {
  loading.value = true;
  try {
    const [s, d] = await Promise.all([
      api.homeSummary(),
      api.homeDaily(14).catch(() => [] as DailyPoint[]),
    ]);
    summary.value = s;
    daily.value = d;
  } catch (e) {
    error.value = errMsg(e);
  } finally {
    loading.value = false;
  }
}

/* ---- 问候与日期 ---- */
const greeting = computed(() => {
  const h = new Date().getHours();
  if (h < 5) return "夜深了";
  if (h < 9) return "早上好";
  if (h < 12) return "上午好";
  if (h < 14) return "中午好";
  if (h < 18) return "下午好";
  return "晚上好";
});
const authName = computed(() => {
  const n = auth.user?.display_name || auth.user?.username;
  return n ? `，${n}` : "";
});
const dateLabel = computed(() => {
  const d = new Date();
  const week = ["日", "一", "二", "三", "四", "五", "六"][d.getDay()];
  return `${d.getMonth() + 1} 月 ${d.getDate()} 日 · 星期${week}`;
});

/* ---- 时长格式化 ---- */
function fmtDuration(sec: number): string {
  if (!sec || sec <= 0) return "0 分钟";
  const h = Math.floor(sec / 3600);
  const m = Math.floor((sec % 3600) / 60);
  if (h >= 1) return m ? `${h} 小时 ${m} 分钟` : `${h} 小时`;
  if (m >= 1) return `${m} 分钟`;
  return "不到 1 分钟";
}

/** 相对时间：今天/昨天/N 天前 */
function relTime(iso: string | null | undefined): string {
  if (!iso) return "";
  const t = new Date(iso).getTime();
  if (!t) return "";
  const startOfDay = new Date();
  startOfDay.setHours(0, 0, 0, 0);
  const days = Math.floor((startOfDay.getTime() - t) / 86_400_000);
  if (days <= 0)
    return `今天 ${new Date(t).toTimeString().slice(0, 5)}`;
  if (days === 1) return "昨天";
  if (days < 30) return `${days} 天前`;
  return `${iso.slice(0, 10)}`;
}

/* ---- 近 14 天迷你柱状图 ---- */
const maxDaily = computed(() =>
  Math.max(60, ...daily.value.map((d) => d.seconds)),
);

interface StatCard {
  label: string;
  value: string;
  sub: string;
}

const statCards = computed<StatCard[]>(() => {
  const s = summary.value;
  if (!s) return [];
  return [
    { label: "今日阅读", value: fmtDuration(s.todaySeconds), sub: `连续 ${s.streakDays} 天` },
    { label: "累计时长", value: fmtDuration(s.totalSeconds), sub: `已读 ${s.totalDays} 天` },
    { label: "累计阅读", value: `${s.totalBooks} 本`, sub: `书架 ${summary.value?.updates.length ?? 0} 本有更新` },
  ];
});

function continueReading(b: {
  sourceUrl: string;
  bookUrl: string;
  name: string;
  author?: string;
  coverUrl?: string;
  intro?: string;
  lastChapter?: string;
  tocUrl?: string;
}) {
  void openReader($router, {
    sourceUrl: b.sourceUrl,
    bookUrl: b.bookUrl,
    name: b.name ?? "",
    author: b.author ?? "",
    coverUrl: b.coverUrl ?? "",
    intro: b.intro ?? "",
    lastChapter: b.lastChapter ?? "",
    tocUrl: b.tocUrl ?? "",
  });
}
</script>

<template>
  <div>
    <!-- 问候区 -->
    <div class="page-head hero">
      <div>
        <h2 class="page-title">{{ greeting }}{{ authName }}</h2>
        <p class="page-sub">{{ dateLabel }} · 今天也读一会儿吧</p>
      </div>
    </div>

    <div v-if="loading" class="center"><MiuixProgressIndicator /></div>
    <div v-else-if="error" class="center err">{{ error }}</div>

    <template v-else-if="summary">
      <!-- 统计卡 -->
      <div class="stat-row">
        <div v-for="(c, i) in statCards" :key="i" class="stat-card">
          <span class="stat-label">{{ c.label }}</span>
          <span class="stat-value">{{ c.value }}</span>
          <span class="stat-sub">{{ c.sub }}</span>
        </div>
      </div>

      <!-- 近 14 天柱状图 -->
      <div v-if="daily.some((d) => d.seconds > 0)" class="chart-card">
        <div class="chart-head">
          <span class="stat-label">近 14 天</span>
          <span class="chart-total">共 {{ fmtDuration(daily.reduce((a, b) => a + b.seconds, 0)) }}</span>
        </div>
        <div class="bars">
          <div
            v-for="(d, i) in daily"
            :key="d.day"
            class="bar-col"
            :title="`${d.day} · ${fmtDuration(d.seconds)}`"
          >
            <div class="bar-track">
              <div
                class="bar"
                :class="{ zero: d.seconds <= 0 }"
                :style="{
                  height: `${Math.max(d.seconds > 0 ? 8 : 2, (d.seconds / maxDaily) * 100)}%`,
                  animationDelay: `${60 + i * 22}ms`,
                }"
              />
            </div>
            <span class="bar-day">{{ Number(d.day.slice(8)) }}</span>
          </div>
        </div>
      </div>

      <!-- 最近阅读 -->
      <section class="sec">
        <div class="sec-head">
          <h3 class="sec-title">最近阅读</h3>
          <button type="button" class="linkbtn" @click="$router.push('/shelf')">
            全部书架 →
          </button>
        </div>

        <div v-if="!summary.recents.length" class="empty-state slim">
          <span class="es-title">还没有阅读记录</span>
          <span>从书架挑一本开始，或去搜索找本新书。</span>
          <span class="es-act">
            <button type="button" class="chip selected" @click="$router.push('/search')">去搜索</button>
          </span>
        </div>

        <div v-else class="cover-grid">
          <div
            v-for="b in summary.recents"
            :key="b.bookUrl"
            class="ctile shelf-tile"
            role="button"
            tabindex="0"
            @click="continueReading(b)"
            @keydown.enter.prevent="continueReading(b)"
          >
            <span class="ctile-cover">
              <img
                :src="b.coverUrl ? coverProxyUrl(b.coverUrl) : FALLBACK_COVER_SVG"
                loading="lazy"
                @error="onCoverError($event, b.coverUrl)"
              >
            </span>
            <span class="ctile-name">{{ b.name }}</span>
            <span class="ctile-sub">
              {{ b.chapterTitle ? `读到 ${b.chapterTitle}` : "刚开始读" }}
              <template v-if="b.readAt"> · {{ relTime(b.readAt) }}</template>
            </span>
            <span class="ctile-meta">{{ b.author || "佚名" }}</span>
          </div>
        </div>
      </section>

      <!-- 书架有更新 -->
      <section v-if="summary.updates.length" class="sec">
        <div class="sec-head">
          <h3 class="sec-title">有更新</h3>
          <span class="sec-sub">每天自动检查书架目录，新章节会出现在这里</span>
        </div>
        <div class="cover-grid">
          <div
            v-for="u in summary.updates"
            :key="u.id"
            class="ctile shelf-tile has-upd"
            role="button"
            tabindex="0"
            @click="continueReading(u)"
            @keydown.enter.prevent="continueReading(u)"
          >
            <span class="ctile-cover">
              <img
                :src="u.coverUrl ? coverProxyUrl(u.coverUrl) : FALLBACK_COVER_SVG"
                loading="lazy"
                @error="onCoverError($event, u.coverUrl)"
              >
              <span class="ctile-badge upd">有更新</span>
            </span>
            <span class="ctile-name">{{ u.name }}</span>
            <span class="ctile-sub">
              {{ u.lastChapter ? `更新到 ${u.lastChapter}` : "" }}
            </span>
            <span class="ctile-meta">{{ u.author || "佚名" }}</span>
          </div>
        </div>
      </section>
    </template>
  </div>
</template>

<style scoped>
.hero .page-title {
  font-size: 26px;
}

/* 统计卡 */
.stat-row {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
  gap: 12px;
  margin-bottom: 14px;
}
.stat-card {
  display: flex;
  flex-direction: column;
  gap: 4px;
  padding: 16px 18px;
  border-radius: 20px;
  background: var(--m-color-surface-container);
  border: 1px solid color-mix(in srgb, var(--m-color-outline) 35%, transparent);
}
/* 入场：与全局卡片同款弹簧上浮，依次错开（复用 base.css 的 rise-in） */
@media (prefers-reduced-motion: no-preference) {
  .stat-card {
    animation: rise-in 0.5s var(--app-ease-spring, cubic-bezier(0.3, 1.12, 0.4, 1)) both;
  }
  .stat-card:nth-child(2) { animation-delay: 0.05s; }
  .stat-card:nth-child(3) { animation-delay: 0.1s; }
}
.stat-label {
  font-size: 12px;
  color: var(--m-color-on-surface-secondary);
}
.stat-value {
  font-size: 22px;
  font-weight: 700;
  letter-spacing: -0.01em;
  color: var(--m-color-on-surface);
}
.stat-sub {
  font-size: 12px;
  color: var(--m-color-on-surface-variant, var(--m-color-on-surface-secondary));
}

/* 柱状图 */
.chart-card {
  padding: 14px 18px 10px;
  border-radius: 20px;
  background: var(--m-color-surface-container);
  border: 1px solid color-mix(in srgb, var(--m-color-outline) 35%, transparent);
  margin-bottom: 22px;
}
.chart-head {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  margin-bottom: 10px;
}
.chart-total {
  font-size: 12px;
  color: var(--m-color-on-surface-secondary);
}
.bars {
  display: flex;
  gap: 6px;
  align-items: stretch;
}
.bar-col {
  flex: 1;
  min-width: 0;
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 4px;
}
.bar-track {
  height: 64px;
  width: 100%;
  max-width: 26px;
  display: flex;
  align-items: flex-end;
}
.bar {
  width: 100%;
  border-radius: 6px 6px 3px 3px;
  background: var(--m-color-primary);
  opacity: 0.85;
}
.bar.zero {
  background: color-mix(in srgb, var(--m-color-on-surface) 18%, transparent);
  opacity: 1;
}
/* 入场：自基线生长（origin 在轴上），逐根错开；animationDelay 由模板按列注入 */
@media (prefers-reduced-motion: no-preference) {
  .bar {
    transform-origin: bottom;
    animation: bar-grow 0.45s var(--app-ease-calm, cubic-bezier(0.22, 0.61, 0.36, 1)) both;
  }
}
@keyframes bar-grow {
  from {
    opacity: 0;
    transform: scaleY(0);
  }
  to {
    opacity: 1;
    transform: scaleY(1);
  }
}
.bar-day {
  font-size: 10px;
  color: var(--m-color-on-surface-secondary);
}

/* 区块标题 */
.sec {
  margin-bottom: 26px;
}
.sec-head {
  display: flex;
  align-items: baseline;
  gap: 10px;
  margin-bottom: 10px;
}
.sec-title {
  margin: 0;
  font-size: 17px;
  font-weight: 700;
  color: var(--m-color-on-surface);
}
.sec-sub {
  font-size: 12px;
  color: var(--m-color-on-surface-secondary);
}

.empty-state.slim {
  padding: 34px 20px;
}

/* 更新徽标配色 */
.ctile-badge.upd {
  background: color-mix(in srgb, #e5484d 90%, transparent);
  color: #fff;
}
</style>
