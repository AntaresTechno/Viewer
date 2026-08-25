<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from "vue";
import { useRoute, useRouter } from "vue-router";
import { MiuixTopAppBar, MiuixIconButton, useTheme as useMiuixTheme } from "miuix-vue";
import { useAuth } from "@/stores/auth";
import { useThemeStore } from "@/stores/theme";
import AppearancePanel from "@/components/AppearancePanel.vue";

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
  show: () => boolean;
  match: (p: string) => boolean;
}

const nav: NavItem[] = [
  {
    label: "书架",
    to: "/shelf",
    show: () => true,
    match: (p) => p === "/shelf" || p.startsWith("/book/"),
  },
  { label: "搜索", to: "/search", show: () => true, match: (p) => p === "/search" },
  { label: "本地库", to: "/library", show: () => auth.can("books.content"), match: (p) => p === "/library" },
  {
    label: "发现",
    to: "/explore",
    show: () => auth.can("books.explore"),
    match: (p) => p === "/explore",
  },
  {
    label: "管理",
    to: "/admin",
    show: () => auth.can("dashboard.read"),
    match: (p) => p.startsWith("/admin"),
  },
  { label: "我的", to: "/me", show: () => true, match: (p) => p === "/me" },
];

const items = computed(() =>
  nav.map((n) => ({ ...n, visible: n.show(), active: n.match(route.path) })),
);

/* ---- 导航滑块（sliding indicator）----
 * 激活态背景不是每项各自淡入淡出，而是一整枚「滑块」沿导航条弹到目标项 ——
 * 空间连续性：旧位置与新位置在视觉上被一条连续轨迹连接。
 * 位置用 offsetLeft/offsetTop 测量（桌面竖排 / 移动横排同一套逻辑），
 * 首次定位不播动画，之后路由切换、窗口尺寸变化都会平滑跟随。 */
const navEl = ref<HTMLElement | null>(null);
const itemEls = new Map<string, HTMLElement>();
const thumb = ref({ x: 0, y: 0, w: 0, h: 0 });
/** 首帧测量完成前禁用过渡，避免滑块从 (0,0) 飞入 */
const thumbAnimated = ref(false);
let measureRaf = 0;

function setItemEl(to: string, el: unknown) {
  if (el instanceof HTMLElement) itemEls.set(to, el);
}

function moveThumb() {
  const target = items.value.find((i) => i.visible && i.active);
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
watch(items, () => void nextTick(scheduleThumbMeasure));

let ro: ResizeObserver | null = null;
onMounted(() => {
  scheduleThumbMeasure();
  // 等字体/布局稳定两帧后再开启过渡动画
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
});
onBeforeUnmount(() => {
  cancelAnimationFrame(measureRaf);
  window.removeEventListener("resize", scheduleThumbMeasure);
  ro?.disconnect();
});

const thumbStyle = computed(() => ({
  transform: `translate(${thumb.value.x}px, ${thumb.value.y}px)`,
  width: `${thumb.value.w}px`,
  height: `${thumb.value.h}px`,
}));

/* ---- 外观弹层 ---- */
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

function toggleAppearance() {
  showAppearance.value = !showAppearance.value;
}

function confirmLogout() {
  if (!confirm("确定退出登录？")) return;
  auth.logout();
  router.push("/login");
}
</script>

<template>
  <div class="shell">
    <!-- color="transparent"：关掉组件内联底色，玻璃材质由 design.css 的
         .m-top-app-bar 规则提供（内容从半透明栏下穿过） -->
    <MiuixTopAppBar title="Viewer" subtitle="阅读 · 管理" color="transparent">
      <template #actions>
        <div ref="appearanceHost" class="appearance-host">
          <MiuixIconButton
            :title="`设计风格：${themeStore.design === 'md3e' ? 'Material 3 Expressive' : 'Miuix'}`"
            @click="toggleAppearance"
          >
            <svg class="ic" viewBox="0 0 24 24" aria-hidden="true">
              <path
                d="M12 3a9 9 0 1 0 0 18c.83 0 1.5-.67 1.5-1.5 0-.39-.15-.74-.39-1.01a1.5 1.5 0 0 1 1.12-2.49H16a5 5 0 0 0 5-5c0-4.42-4.03-8-9-8Zm-4.5 8a1.5 1.5 0 1 1 0-3 1.5 1.5 0 0 1 0 3Zm3-4a1.5 1.5 0 1 1 0-3 1.5 1.5 0 0 1 0 3Zm5 0a1.5 1.5 0 1 1 0-3 1.5 1.5 0 0 1 0 3Zm3 4a1.5 1.5 0 1 1 0-3 1.5 1.5 0 0 1 0 3Z"
              />
            </svg>
          </MiuixIconButton>

          <Transition name="pop">
            <div v-if="showAppearance" class="appearance-pop">
              <AppearancePanel />
            </div>
          </Transition>
        </div>

        <MiuixIconButton title="切换深色 / 浅色" @click="themeStore.toggleDark($event)">
          <svg v-if="isDark" class="ic" viewBox="0 0 24 24" aria-hidden="true">
            <path
              d="M12 7a5 5 0 1 0 0 10 5 5 0 0 0 0-10Zm0-5a1 1 0 0 1 1 1v2a1 1 0 1 1-2 0V3a1 1 0 0 1 1-1Zm0 18a1 1 0 0 1 1 1v1a1 1 0 1 1-2 0v-1a1 1 0 0 1 1-1Zm10-8a1 1 0 0 1-1 1h-2a1 1 0 1 1 0-2h2a1 1 0 0 1 1 1ZM6 12a1 1 0 0 1-1 1H3a1 1 0 1 1 0-2h2a1 1 0 0 1 1 1Zm12.07-7.07a1 1 0 0 1 0 1.41l-1.41 1.42a1 1 0 1 1-1.42-1.42l1.42-1.41a1 1 0 0 1 1.41 0ZM8.76 15.24a1 1 0 0 1 0 1.41l-1.42 1.42a1 1 0 1 1-1.41-1.42l1.41-1.41a1 1 0 0 1 1.42 0Zm9.31 1.41a1 1 0 0 1-1.41 0l-1.42-1.41a1 1 0 0 1 1.42-1.42l1.41 1.42a1 1 0 0 1 0 1.41ZM8.76 8.76a1 1 0 0 1-1.42 0L5.93 7.34a1 1 0 0 1 1.41-1.41l1.42 1.41a1 1 0 0 1 0 1.42Z"
            />
          </svg>
          <svg v-else class="ic" viewBox="0 0 24 24" aria-hidden="true">
            <path
              d="M20.75 14.36a8.9 8.9 0 0 1-2.61.39 9 9 0 0 1-9-9c0-.98.16-1.93.45-2.82A9.5 9.5 0 0 0 2.5 11.75a9.5 9.5 0 0 0 16.44 6.5c.72-.83 1.33-1.78 1.81-3.89Z"
            />
          </svg>
        </MiuixIconButton>

        <MiuixIconButton title="退出登录" @click="confirmLogout">
          <svg class="ic" viewBox="0 0 24 24" aria-hidden="true">
            <path
              d="M17 7.7 15.59 9.11 17.48 11H10v2h7.48l-1.89 1.89L17 16.3l4.3-4.3L17 7.7ZM5 5h6V3H5c-1.1 0-2 .9-2 2v14c0 1.1.9 2 2 2h6v-2H5V5Z"
            />
          </svg>
        </MiuixIconButton>
      </template>
    </MiuixTopAppBar>

    <div class="body">
      <aside class="rail">
        <div class="brand">
          <div class="logo">V</div>
          <div class="brand-name">Viewer</div>
        </div>
        <nav ref="navEl" class="rail-nav">
          <span
            class="rail-thumb"
            :class="{ anim: thumbAnimated }"
            :style="thumbStyle"
            aria-hidden="true"
          ></span>
          <template v-for="(it, i) in items" :key="it.to + i">
            <button
              v-if="it.visible"
              class="rail-item"
              :class="{ active: it.active }"
              :ref="(el) => setItemEl(it.to, el)"
              @click="router.push(it.to)"
            >
              <span class="pill">{{ it.label }}</span>
            </button>
          </template>
        </nav>
        <div class="rail-footer">
          <div class="avatar" :style="{ background: `hsl(${auth.user?.avatar_hue ?? 217}, 45%, 60%)` }">
            {{ auth.user?.display_name?.slice(0, 1)?.toUpperCase() }}
          </div>
          <div class="who">
            <div class="who-name">{{ auth.user?.display_name }}</div>
            <button class="logout" @click="auth.logout(); router.push('/login')">
              退出登录
            </button>
          </div>
        </div>
      </aside>

      <main class="page">
        <router-view v-slot="{ Component }">
          <Transition name="page" mode="out-in">
            <component :is="Component" />
          </Transition>
        </router-view>
      </main>
    </div>
  </div>
</template>

<style scoped>
.shell {
  min-height: 100vh;
  display: flex;
  flex-direction: column;
}
.body {
  flex: 1;
  display: grid;
  grid-template-columns: 220px 1fr;
  max-width: 1280px;
  width: 100%;
  margin: 0 auto;
  gap: 24px;
  padding: 20px 24px 40px;
}
.rail {
  position: sticky;
  top: 64px; /* 让出半透明顶栏的高度，随页面一起停在栏下 */
  align-self: start;
  display: flex;
  flex-direction: column;
  gap: 4px;
}
.brand {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 8px 12px 18px;
}
.logo {
  width: 36px;
  height: 36px;
  border-radius: var(--app-radius-input, 12px);
  background: var(--m-color-primary);
  color: var(--m-color-on-primary);
  font-weight: 700;
  display: grid;
  place-items: center;
  font-size: 18px;
}
.brand-name {
  font-weight: 600;
  color: var(--m-color-on-surface);
}

/* ---- 导航条 + 滑块 ---- */
.rail-nav {
  position: relative;
  display: flex;
  flex-direction: column;
  gap: 4px;
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
  display: block;
  width: 100%;
  border: 0;
  background: transparent;
  padding: 2px 0;
  cursor: pointer;
  text-align: left;
}
.rail-item .pill {
  display: block;
  padding: 10px 16px;
  border-radius: 999px;
  color: var(--m-color-on-surface-secondary);
  font-size: 14px;
  background: transparent;
}
@media (prefers-reduced-motion: no-preference) {
  .rail-item .pill {
    transition:
      background 0.15s ease-out,
      transform 0.35s var(--app-ease-spring, cubic-bezier(0.3, 1.12, 0.4, 1));
  }
  /* 悬停高亮只出现在未激活项上；激活项的高亮由滑块承担 */
  .rail-item:not(.active):hover .pill {
    background: var(--m-color-surface-container-high);
  }
  .rail-item:active .pill {
    transform: scale(0.97);
  }
}
.rail-item.active .pill {
  color: var(--m-color-on-secondary-container);
  font-weight: 600;
}
.rail-footer {
  margin-top: auto;
  display: flex;
  gap: 10px;
  align-items: center;
  padding: 14px 8px;
}
.avatar {
  width: 38px;
  height: 38px;
  border-radius: 50%;
  display: grid;
  place-items: center;
  color: #fff;
  font-weight: 700;
  flex: none;
}
.who-name {
  font-size: 13px;
  color: var(--m-color-on-surface);
}
.logout {
  border: 0;
  background: none;
  color: var(--m-color-on-surface-secondary);
  font-size: 12px;
  cursor: pointer;
  padding: 0;
}
.logout:hover {
  color: var(--m-color-error);
}
.page {
  min-width: 0;
}

/* ---- 顶栏外观按钮 + 弹层 ----
 * 玻璃材料：半透明面 + 背景模糊 + 顶部受光的细亮缘；
 * transform-origin 锚定在触发按钮一侧，进出走同一条路径。 */
.appearance-host {
  position: relative;
  display: inline-flex;
}
.ic {
  width: 20px;
  height: 20px;
  fill: currentColor;
  display: block;
}
.appearance-pop {
  position: absolute;
  top: calc(100% + 6px);
  right: 0;
  z-index: 900;
  min-width: 280px;
  padding: 16px;
  border-radius: 20px;
  background: color-mix(in srgb, var(--m-color-surface-container) 86%, transparent);
  -webkit-backdrop-filter: blur(24px) saturate(160%);
  backdrop-filter: blur(24px) saturate(160%);
  box-shadow:
    inset 0 1px 0 rgba(255, 255, 255, 0.18),
    var(--app-shadow-pop, 0 10px 32px rgba(0, 0, 0, 0.18));
  border: 1px solid color-mix(in srgb, var(--m-color-outline) 40%, transparent);
  transform-origin: top right;
}
@media (prefers-reduced-transparency: reduce) {
  .appearance-pop {
    background: var(--m-color-surface-container);
    -webkit-backdrop-filter: none;
    backdrop-filter: none;
  }
}
/* 进出同路径：enter-from 与 leave-to 完全一致；
 * 进入用弹簧（略带回弹），离开更快更收敛 —— 材料感与响应感并存。 */
.pop-enter-active {
  transition:
    opacity 0.22s ease-out,
    transform 0.45s var(--app-ease-spring, cubic-bezier(0.3, 1.12, 0.4, 1));
}
.pop-leave-active {
  transition:
    opacity 0.15s ease-in,
    transform 0.18s ease-in;
}
.pop-enter-from,
.pop-leave-to {
  opacity: 0;
  transform: translateY(-6px) scale(0.97);
}
@media (prefers-reduced-motion: reduce) {
  .pop-enter-active,
  .pop-leave-active {
    transition: opacity 0.15s ease;
  }
  .pop-enter-from,
  .pop-leave-to {
    transform: none;
  }
}

@media (max-width: 760px) {
  .body {
    grid-template-columns: 1fr;
  }
  .rail {
    position: static;
    flex-direction: row;
    align-items: center;
    overflow-x: auto;
  }
  .brand,
  .rail-footer {
    display: none;
  }
  .rail-nav {
    flex-direction: row;
  }
  .rail-item {
    width: auto;
  }
  .appearance-pop {
    position: fixed;
    top: 56px;
    right: 12px;
    left: 12px;
    min-width: 0;
  }
}
</style>
