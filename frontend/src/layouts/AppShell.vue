<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from "vue";
import { useRoute, useRouter } from "vue-router";
import { MiuixIconButton, useTheme as useMiuixTheme } from "miuix-vue";
import { useAuth } from "@/stores/auth";
import { useThemeStore } from "@/stores/theme";
import { api } from "@/api/client";
import AppearancePanel from "@/components/AppearancePanel.vue";

/**
 * 应用骨架：一块铬铁原则。
 * - 桌面（>860px）：分组侧栏承担全部导航与身份操作，无顶栏 —— 内容更靠上；
 * - 移动（≤860px）：底部标签栏（拇指可达、毛玻璃），身份操作收进「我的」页。
 */

const auth = useAuth();
const themeStore = useThemeStore();
const router = useRouter();
const route = useRoute();

/* miuix 内部主题状态（响应式），用于明暗图标 */
const { theme: live } = useMiuixTheme();
const isDark = computed(() => live.value === "dark");

interface NavItem {
  label: string;
  to: string;
  icon: string;
  show: () => boolean;
  match: (p: string) => boolean;
}

/* 线性小图标（stroke 风格，与系统字体粗细协调） */
const ICONS: Record<string, string> = {
  home:
    '<path d="m4.2 11.3 7.8-7 7.8 7"/><path d="M6.3 9.6V19a.9.9 0 0 0 .9.9h9.6a.9.9 0 0 0 .9-.9V9.6"/><path d="M10 19.7v-5.2h4v5.2"/>',
  shelf:
    '<path d="M5.5 20V7.5"/><path d="M10 20V4.5"/><path d="M14.5 20V7.5"/><path d="m18.9 18.6-2.2-11"/><path d="M3.8 20h16.4"/>',
  library:
    '<path d="M12 4.5V13"/><path d="m8.5 9.7 3.5 3.5 3.5-3.5"/><path d="M4.5 14.5V18A1.5 1.5 0 0 0 6 19.5h12a1.5 1.5 0 0 0 1.5-1.5v-3.5"/>',
  search:
    '<circle cx="11" cy="11" r="6.25"/><path d="m15.8 15.8 4.2 4.2"/>',
  explore:
    '<circle cx="12" cy="12" r="8.25"/><path d="m15.6 8.4-1.9 5.3-5.3 1.9 1.9-5.3z"/>',
  admin:
    '<path d="M12 3.8 5.2 6.2v5c0 4.2 2.9 7.8 6.8 9.2 3.9-1.4 6.8-5 6.8-9.2v-5z"/>',
  purify:
    '<path d="M12 3.5v4"/><path d="M12 16.5v4"/><path d="m5.2 5.2 2.8 2.8"/><path d="m16 16 2.8 2.8"/><path d="M3.5 12h4"/><path d="M16.5 12h4"/><path d="m5.2 18.8 2.8-2.8"/><path d="m16 8 2.8-2.8"/><circle cx="12" cy="12" r="2.6"/>',
  webdav:
    '<path d="M7.2 17.5a4.1 4.1 0 1 1 .55-8.16 5.3 5.3 0 0 1 10.28 1.42A3.55 3.55 0 0 1 17.3 17.5Z"/><path d="M12 12.5v4"/><path d="m10 14.5 2-2 2 2"/>',
  me: '<circle cx="12" cy="8.2" r="3.7"/><path d="M5 19.5c1.4-3.2 3.9-4.8 7-4.8s5.6 1.6 7 4.8"/>',
};

const nav: NavItem[] = [
  {
    label: "首页",
    to: "/home",
    icon: "home",
    show: () => auth.can("home.read"),
    match: (p) => p === "/home",
  },
  {
    label: "书架",
    to: "/shelf",
    icon: "shelf",
    show: () => true,
    match: (p) => p === "/shelf" || p.startsWith("/book/") || p.startsWith("/reader"),
  },
  {
    label: "本地库",
    to: "/library",
    icon: "library",
    show: () => auth.can("books.content"),
    match: (p) => p === "/library",
  },
  {
    label: "搜索",
    to: "/search",
    icon: "search",
    show: () => true,
    match: (p) => p === "/search",
  },
  {
    label: "发现",
    to: "/explore",
    icon: "explore",
    show: () => auth.can("books.explore"),
    match: (p) => p === "/explore",
  },
  {
    label: "净化",
    to: "/purify",
    icon: "purify",
    show: () => auth.can("purify.read"),
    match: (p) => p === "/purify",
  },
  {
    label: "WebDAV",
    to: "/webdav",
    icon: "webdav",
    show: () => auth.can("webdav.use"),
    match: (p) => p === "/webdav",
  },
  {
    label: "管理",
    to: "/admin",
    icon: "admin",
    show: () => auth.can("dashboard.read"),
    match: (p) => p.startsWith("/admin"),
  },
  {
    label: "我的",
    to: "/me",
    icon: "me",
    show: () => true,
    match: (p) => p === "/me" || p.startsWith("/replace"),
  },
];

/** 分组：相邻的同类入口放在一起，分组标题解释「这一片是什么」。 */
const groups: { label: string; items: NavItem[] }[] = [
  { label: "阅读", items: nav.slice(0, 3) },
  { label: "探索", items: nav.slice(3, 5) },
  { label: "系统", items: nav.slice(5, 9) },
];

const visibleGroups = computed(() =>
  groups
    .map((g) => ({
      ...g,
      items: g.items.map((n) => ({ ...n, visible: n.show(), active: n.match(route.path) })),
    }))
    .filter((g) => g.items.some((i) => i.visible)),
);

const flatVisible = computed(() => visibleGroups.value.flatMap((g) => g.items));

/* ---- 桌面侧栏滑块 ---- */
const navEl = ref<HTMLElement | null>(null);
const itemEls = new Map<string, HTMLElement>();
const thumb = ref({ x: 0, y: 0, w: 0, h: 0 });
const thumbAnimated = ref(false);
let measureRaf = 0;

function setItemEl(to: string, el: unknown) {
  if (el instanceof HTMLElement) itemEls.set(to, el);
}

function moveThumb() {
  const target = flatVisible.value.find((i) => i.active);
  const el = target ? itemEls.get(target.to) : undefined;
  if (!el) return;
  thumb.value = {
    x: el.offsetLeft,
    y: el.offsetTop,
    w: el.offsetWidth,
    h: el.offsetHeight,
  };
}

function scheduleThumbMeasure() {
  cancelAnimationFrame(measureRaf);
  measureRaf = requestAnimationFrame(() => moveThumb());
}

watch(
  () => route.path,
  () => void nextTick(scheduleThumbMeasure),
);
watch(flatVisible, () => void nextTick(scheduleThumbMeasure));

let ro: ResizeObserver | null = null;
onMounted(() => {
  scheduleThumbMeasure();
  requestAnimationFrame(() =>
    requestAnimationFrame(() => {
      thumbAnimated.value = true;
    }),
  );
  window.addEventListener("resize", scheduleThumbMeasure);
  if (navEl.value && typeof ResizeObserver !== "undefined") {
    ro = new ResizeObserver(scheduleThumbMeasure);
    ro.observe(navEl.value);
  }
  void checkConn(true);
  connTimer = window.setInterval(() => void checkConn(), 30_000);
  document.addEventListener("visibilitychange", onConnVisibility);
});
onBeforeUnmount(() => {
  cancelAnimationFrame(measureRaf);
  window.removeEventListener("resize", scheduleThumbMeasure);
  ro?.disconnect();
  if (connTimer !== null) window.clearInterval(connTimer);
  document.removeEventListener("visibilitychange", onConnVisibility);
});

const thumbStyle = computed(() => ({
  transform: `translate(${thumb.value.x}px, ${thumb.value.y}px)`,
  width: `${thumb.value.w}px`,
  height: `${thumb.value.h}px`,
}));

function go(to: string) {
  if (route.path !== to) router.push(to);
}

/* ---- 外观弹层（锚定在侧栏用户卡上方） ---- */
const showAppearance = ref(false);
const appearanceHost = ref<HTMLElement | null>(null);

function onDocPointerDown(e: MouseEvent) {
  if (appearanceHost.value && !appearanceHost.value.contains(e.target as Node)) {
    showAppearance.value = false;
  }
}
function onDocKeydown(e: KeyboardEvent) {
  if (e.key === "Escape") showAppearance.value = false;
}

watch(showAppearance, (open) => {
  if (open) {
    document.addEventListener("pointerdown", onDocPointerDown, true);
    document.addEventListener("keydown", onDocKeydown);
  } else {
    document.removeEventListener("pointerdown", onDocPointerDown, true);
    document.removeEventListener("keydown", onDocKeydown);
  }
});
onBeforeUnmount(() => {
  document.removeEventListener("pointerdown", onDocPointerDown, true);
  document.removeEventListener("keydown", onDocKeydown);
});

function confirmLogout() {
  if (!confirm("确定退出登录？")) return;
  auth.logout();
  router.push("/login");
}

/* ---- 后端连通性（操作按钮排右侧的在线状态点） ----
 * 挂载时检测一次，之后每 30s 静默复查 + 页面回到前台时复查，避免漏判断网恢复；
 * 手动点击带「检测中」脉冲。 */
const conn = ref<"checking" | "online" | "offline">("checking");
let connTimer: number | null = null;
const connTitle = computed(() =>
  conn.value === "online" ? "后端已连接 · 点击重新检测"
  : conn.value === "offline" ? "后端已断开 · 点击重试"
  : "正在检测后端连通性…",
);
async function checkConn(showChecking = false) {
  if (showChecking) conn.value = "checking";
  try {
    await api.health();
    conn.value = "online";
  } catch {
    conn.value = "offline";
  }
}
function onConnVisibility() {
  if (document.visibilityState === "visible") void checkConn();
}
</script>

<template>
  <div class="shell">
    <!-- ======================= 桌面侧栏 ======================= -->
    <aside class="rail">
      <button class="brand" type="button" title="进入首页" @click="go('/home')">
        <span class="logo">V</span>
        <span class="brand-name">Antares Viewer</span>
      </button>

      <nav ref="navEl" class="rail-nav" aria-label="主导航">
        <span
          class="rail-thumb"
          :class="{ anim: thumbAnimated }"
          :style="thumbStyle"
          aria-hidden="true"
        ></span>
        <template v-for="grp in visibleGroups" :key="grp.label">
          <div class="grp-label">{{ grp.label }}</div>
          <template v-for="it in grp.items" :key="it.to">
            <button
              v-if="it.visible"
              class="rail-item"
              :class="{ active: it.active }"
              :ref="(el) => setItemEl(it.to, el)"
              @click="go(it.to)"
            >
              <svg
                class="nic"
                viewBox="0 0 24 24"
                fill="none"
                stroke="currentColor"
                stroke-width="1.7"
                stroke-linecap="round"
                stroke-linejoin="round"
                aria-hidden="true"
                v-html="ICONS[it.icon]"
              ></svg>
              <span class="pill">{{ it.label }}</span>
            </button>
          </template>
        </template>
      </nav>

      <div class="rail-foot">
        <!-- 操作按钮单独一排在上面，把最底部整行让给头像 + 用户名 -->
        <div class="uacts">
          <div ref="appearanceHost" class="appearance-host">
            <MiuixIconButton
              :title="`设计风格：${themeStore.design === 'md3e' ? 'Material 3 Expressive' : 'Miuix'}`"
              @click="showAppearance = !showAppearance"
            >
              <svg class="aic" viewBox="0 0 24 24" aria-hidden="true">
                <path
                  d="M12 3a9 9 0 1 0 0 18c.83 0 1.5-.67 1.5-1.5 0-.39-.15-.74-.39-1.01a1.5 1.5 0 0 1 1.12-2.49H16a5 5 0 0 0 5-5c0-4.42-4.03-8-9-8Zm-4.5 8a1.5 1.5 0 1 1 0-3 1.5 1.5 0 0 1 0 3Zm3-4a1.5 1.5 0 1 1 0-3 1.5 1.5 0 0 1 0 3Zm5 0a1.5 1.5 0 1 1 0-3 1.5 1.5 0 0 1 0 3Zm3 4a1.5 1.5 0 1 1 0-3 1.5 1.5 0 0 1 0 3Z"
                />
              </svg>
            </MiuixIconButton>

            <Transition name="pop-up">
              <div v-if="showAppearance" class="appearance-pop">
                <AppearancePanel />
              </div>
            </Transition>
          </div>

          <MiuixIconButton title="切换深色 / 浅色" @click="themeStore.toggleDark($event)">
            <svg v-if="isDark" class="aic" viewBox="0 0 24 24" aria-hidden="true">
              <path
                d="M12 7a5 5 0 1 0 0 10 5 5 0 0 0 0-10Zm0-5a1 1 0 0 1 1 1v2a1 1 0 1 1-2 0V3a1 1 0 0 1 1-1Zm0 18a1 1 0 0 1 1 1v1a1 1 0 1 1-2 0v-1a1 1 0 0 1 1-1Zm10-8a1 1 0 0 1-1 1h-2a1 1 0 1 1 0-2h2a1 1 0 0 1 1 1ZM6 12a1 1 0 0 1-1 1H3a1 1 0 1 1 0-2h2a1 1 0 0 1 1 1Zm12.07-7.07a1 1 0 0 1 0 1.41l-1.41 1.42a1 1 0 1 1-1.42-1.42l1.42-1.41a1 1 0 0 1 1.41 0ZM8.76 15.24a1 1 0 0 1 0 1.41l-1.42 1.42a1 1 0 1 1-1.41-1.42l1.41-1.41a1 1 0 0 1 1.42 0Zm9.31 1.41a1 1 0 0 1-1.41 0l-1.42-1.41a1 1 0 0 1 1.42-1.42l1.41 1.42a1 1 0 0 1 0 1.41ZM8.76 8.76a1 1 0 0 1-1.42 0L5.93 7.34a1 1 0 0 1 1.41-1.41l1.42 1.41a1 1 0 0 1 0 1.42Z"
              />
            </svg>
            <svg v-else class="aic" viewBox="0 0 24 24" aria-hidden="true">
              <path
                d="M20.75 14.36a8.9 8.9 0 0 1-2.61.39 9 9 0 0 1-9-9c0-.98.16-1.93.45-2.82A9.5 9.5 0 0 0 2.5 11.75a9.5 9.5 0 0 0 16.44 6.5c.72-.83 1.33-1.78 1.81-3.89Z"
              />
            </svg>
          </MiuixIconButton>

          <MiuixIconButton title="退出登录" @click="confirmLogout">
            <svg class="aic" viewBox="0 0 24 24" aria-hidden="true">
              <path
                d="M17 7.7 15.59 9.11 17.48 11H10v2h7.48l-1.89 1.89L17 16.3l4.3-4.3L17 7.7ZM5 5h6V3H5c-1.1 0-2 .9-2 2v14c0 1.1.9 2 2 2h6v-2H5V5Z"
              />
            </svg>
          </MiuixIconButton>

          <!-- 后端连通性状态：占住这一排右侧，按钮保持左对齐 -->
          <button
            type="button"
            class="conn"
            :class="conn"
            :title="connTitle"
            @click="checkConn(true)"
          >
            <span class="conn-dot"></span>
            <span class="conn-label">{{ conn === "online" ? "在线" : conn === "offline" ? "离线" : "检测中" }}</span>
          </button>
        </div>

        <!-- 最底部：整行宽度尽量完整展示头像与用户名 -->
        <button class="ucard" type="button" @click="go('/me')">
          <span class="avatar" :style="{ background: `hsl(${auth.user?.avatar_hue ?? 217}, 45%, 60%)` }">
            {{ auth.user?.display_name?.slice(0, 1)?.toUpperCase() }}
          </span>
          <span class="uwho">
            <span class="uname">{{ auth.user?.display_name }}</span>
            <span class="usub">@{{ auth.user?.username }}</span>
          </span>
        </button>
      </div>
    </aside>

    <!-- ======================= 内容区 ======================= -->
    <main class="main">
      <router-view v-slot="{ Component }">
        <Transition name="page" mode="out-in">
          <component :is="Component" />
        </Transition>
      </router-view>
    </main>

    <!-- ======================= 移动端底部标签栏 ======================= -->
    <nav class="tabbar" aria-label="主导航">
      <button
        v-for="it in flatVisible"
        :key="'t-' + it.to"
        type="button"
        class="tab-item"
        :class="{ active: it.active }"
        @click="go(it.to)"
      >
        <svg
          class="nic"
          viewBox="0 0 24 24"
          fill="none"
          stroke="currentColor"
          stroke-width="1.7"
          stroke-linecap="round"
          stroke-linejoin="round"
          aria-hidden="true"
          v-html="ICONS[it.icon]"
        ></svg>
        <span>{{ it.label }}</span>
      </button>
    </nav>
  </div>
</template>

<style scoped>
.shell {
  min-height: 100dvh;
  display: flex;
}

/* ================= 桌面侧栏 ================= */
.rail {
  position: sticky;
  top: 0;
  height: 100dvh;
  width: 232px;
  flex: none;
  display: flex;
  flex-direction: column;
  padding: 16px 12px 14px;
  gap: 6px;
  /* sticky 自身成堆叠上下文：外观弹层被关在里面，而主内容区的
   * 瓷砖/卡片带 fill:both 的入场动画会长期持有 transform，绘制时
   * 整体压过弹层。给整条侧栏一个显式层级，让弹层盖住内容区，
   * 同时仍低于 tabbar / miuix 弹窗的 800 层。 */
  z-index: 60;
  border-right: 1px solid color-mix(in srgb, var(--m-color-on-surface) 7%, transparent);
  background: color-mix(in srgb, var(--m-color-surface-container) 55%, transparent);
}
.brand {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 6px 10px 16px;
  border: 0;
  background: none;
  cursor: pointer;
  text-align: left;
}
.logo {
  width: 34px;
  height: 34px;
  border-radius: 11px;
  background: var(--m-color-primary);
  color: var(--m-color-on-primary);
  font-weight: 700;
  display: grid;
  place-items: center;
  font-size: 17px;
  flex: none;
}
.brand-name {
  font-weight: 700;
  font-size: 16px;
  letter-spacing: -0.01em;
  color: var(--m-color-on-surface);
}

.rail-nav {
  position: relative;
  flex: 1;
  overflow-y: auto;
  display: flex;
  flex-direction: column;
  gap: 2px;
  min-height: 0;
}
.grp-label {
  font-size: 11px;
  font-weight: 600;
  letter-spacing: 0.08em;
  color: var(--m-color-on-surface-secondary);
  padding: 14px 14px 5px;
  user-select: none;
}
.rail-thumb {
  position: absolute;
  top: 0;
  left: 0;
  border-radius: 999px;
  background: var(--m-color-secondary-container);
  pointer-events: none;
  will-change: transform, width, height;
}
.rail-thumb.anim {
  transition:
    transform 0.55s var(--app-ease-spring, cubic-bezier(0.3, 1.12, 0.4, 1)),
    width 0.55s var(--app-ease-spring, cubic-bezier(0.3, 1.12, 0.4, 1)),
    height 0.55s var(--app-ease-spring, cubic-bezier(0.3, 1.12, 0.4, 1));
}
@media (prefers-reduced-motion: reduce) {
  .rail-thumb.anim {
    transition: none;
  }
}
.rail-item {
  position: relative;
  z-index: 1;
  display: flex;
  align-items: center;
  gap: 10px;
  width: 100%;
  border: 0;
  background: transparent;
  padding: 0 14px;
  height: 38px;
  cursor: pointer;
  text-align: left;
  font-family: inherit;
  font-size: 14px;
  color: var(--m-color-on-surface-secondary);
  border-radius: 999px;
}
.nic {
  width: 19px;
  height: 19px;
  flex: none;
}
@media (prefers-reduced-motion: no-preference) {
  .nic,
  .rail-item .pill {
    transition: transform 0.35s var(--app-ease-spring, cubic-bezier(0.3, 1.12, 0.4, 1));
  }
  .rail-item:not(.active):hover {
    background: color-mix(in srgb, var(--m-color-on-surface) 5%, transparent);
  }
  .rail-item:active .pill {
    transform: scale(0.97);
  }
  .rail-item:active .nic {
    transform: scale(0.92);
  }
}
.rail-item.active {
  color: var(--m-color-on-secondary-container);
  font-weight: 600;
}

/* 用户卡 + 身份操作：按钮一排在上，用户卡独占最底一行 */
.rail-foot {
  border-top: 1px solid color-mix(in srgb, var(--m-color-on-surface) 7%, transparent);
  padding-top: 10px;
  display: flex;
  flex-direction: column;
  align-items: stretch;
  gap: 6px;
}
.ucard {
  display: flex;
  align-items: center;
  gap: 10px;
  min-width: 0;
  flex: 1;
  border: 0;
  background: none;
  cursor: pointer;
  text-align: left;
  padding: 0;
  font-family: inherit;
}
.avatar {
  width: 36px;
  height: 36px;
  border-radius: 50%;
  display: grid;
  place-items: center;
  color: #fff;
  font-weight: 700;
  font-size: 15px;
  flex: none;
}
.uwho {
  min-width: 0;
  display: block;
}
.uname {
  display: block;
  font-size: 13px;
  font-weight: 600;
  color: var(--m-color-on-surface);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.usub {
  display: block;
  font-size: 11px;
  color: var(--m-color-on-surface-secondary);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.uacts {
  display: flex;
  align-items: center;
  justify-content: flex-start;
  gap: 2px;
}
.aic {
  width: 19px;
  height: 19px;
  fill: currentColor;
  display: block;
}

/* 后端连通性状态：一排在右（margin-left:auto），按钮保持左对齐 */
.conn {
  margin-left: auto;
  display: inline-flex;
  align-items: center;
  gap: 5px;
  border: 0;
  background: transparent;
  cursor: pointer;
  font-family: inherit;
  font-size: 11px;
  color: var(--m-color-on-surface-secondary);
  padding: 4px 6px;
  border-radius: 999px;
}
.conn:hover {
  background: color-mix(in srgb, var(--m-color-on-surface) 5%, transparent);
}
.conn-dot {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  background: var(--m-color-outline);
  flex: none;
}
.conn.online .conn-dot {
  background: var(--m-color-primary);
  box-shadow: 0 0 0 3px color-mix(in srgb, var(--m-color-primary) 18%, transparent);
}
.conn.offline .conn-dot {
  background: var(--m-color-error);
}
@media (prefers-reduced-motion: no-preference) {
  .conn.checking .conn-dot {
    animation: conn-pulse 1.1s ease-in-out infinite;
  }
}
@keyframes conn-pulse {
  50% { opacity: 0.35; }
}

/* 外观弹层：从按钮向右侧内容区飞出（flyout）。
 * 不能向上/向左展开 —— 按钮在屏幕最左侧的侧栏里，弹层往左伸会
 * 越过视口左边界导致点不到；右侧是主内容区，空间永远充足。
 * 底部对齐 + 从左侧长出，进出同路径。 */
.appearance-host {
  position: relative;
  display: inline-flex;
}
.appearance-pop {
  position: absolute;
  left: calc(100% + 10px);
  bottom: 0;
  z-index: 900;
  min-width: 280px;
  padding: 16px;
  border-radius: 20px;
  background: color-mix(in srgb, var(--m-color-surface-container) 88%, transparent);
  -webkit-backdrop-filter: blur(24px) saturate(160%);
  backdrop-filter: blur(24px) saturate(160%);
  box-shadow:
    inset 0 1px 0 rgba(255, 255, 255, 0.18),
    var(--app-shadow-pop, 0 10px 32px rgba(0, 0, 0, 0.18));
  border: 1px solid color-mix(in srgb, var(--m-color-outline) 40%, transparent);
  transform-origin: left bottom;
}
@media (prefers-reduced-transparency: reduce) {
  .appearance-pop {
    background: var(--m-color-surface-container);
    -webkit-backdrop-filter: none;
    backdrop-filter: none;
  }
}
.pop-up-enter-active {
  transition:
    opacity 0.22s ease-out,
    transform 0.45s var(--app-ease-spring, cubic-bezier(0.3, 1.12, 0.4, 1));
}
.pop-up-leave-active {
  transition:
    opacity 0.15s ease-in,
    transform 0.18s ease-in;
}
.pop-up-enter-from,
.pop-up-leave-to {
  opacity: 0;
  transform: translateX(-6px) scale(0.97);
}
@media (prefers-reduced-motion: reduce) {
  .pop-up-enter-active,
  .pop-up-leave-active {
    transition: opacity 0.15s ease;
  }
  .pop-up-enter-from,
  .pop-up-leave-to {
    transform: none;
  }
}

/* ================= 内容区 ================= */
.main {
  flex: 0 1 1160px;
  min-width: 0;
  margin: 0 auto; /* 内容区在侧栏右侧剩余区域内水平居中；侧栏与页内组件不动 */
  padding: 26px 34px 48px;
}

/* ================= 移动端 ================= */
.tabbar {
  display: none;
}

/* 底部标签栏的降级与动效（顶层书写，避免嵌套媒体查询兼容问题） */
@media (max-width: 860px) and (prefers-reduced-transparency: reduce) {
  .tabbar {
    background: var(--m-color-surface);
    -webkit-backdrop-filter: none;
    backdrop-filter: none;
  }
}
@media (max-width: 860px) and (prefers-reduced-motion: no-preference) {
  .tab-item {
    transition: transform 0.35s var(--app-ease-spring, cubic-bezier(0.3, 1.12, 0.4, 1));
  }
  .tab-item:active {
    transform: scale(0.92);
  }
}

@media (max-width: 860px) {
  .shell {
    flex-direction: column;
  }
  .rail {
    display: none;
  }
  .main {
    padding: 18px 16px calc(84px + env(safe-area-inset-bottom));
    max-width: none;
    width: 100%;
    flex-basis: auto; /* 覆盖桌面行向的 1160px 高度基准 */
  }

  /* 底部标签栏：拇指区、毛玻璃、安全区适配 */
  .tabbar {
    position: fixed;
    left: 0;
    right: 0;
    bottom: 0;
    z-index: 800;
    display: flex;
    justify-content: space-around;
    padding: 6px 8px max(8px, env(safe-area-inset-bottom));
    background: color-mix(in srgb, var(--m-color-surface) 78%, transparent);
    -webkit-backdrop-filter: blur(24px) saturate(160%);
    backdrop-filter: blur(24px) saturate(160%);
    box-shadow: inset 0 1px 0 color-mix(in srgb, var(--m-color-on-surface) 7%, transparent);
  }
  .tab-item {
    flex: 1;
    max-width: 96px;
    display: flex;
    flex-direction: column;
    align-items: center;
    gap: 3px;
    border: 0;
    background: none;
    cursor: pointer;
    font-family: inherit;
    font-size: 10.5px;
    color: var(--m-color-on-surface-secondary);
    padding: 5px 0 3px;
    border-radius: 12px;
  }
  .tab-item .nic {
    width: 23px;
    height: 23px;
  }
  .tab-item.active {
    color: var(--m-color-primary);
    font-weight: 600;
  }
}
</style>
