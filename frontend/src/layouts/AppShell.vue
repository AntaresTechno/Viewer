<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from "vue";
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
    <MiuixTopAppBar title="Viewer" subtitle="阅读 · 管理">
      <template #actions>
        <div ref="appearanceHost" class="appearance-host">
          <MiuixIconButton
            :title="`设计风格：${themeStore.design === 'md3' ? 'Material You' : 'Miuix'}`"
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
        <nav>
          <template v-for="(it, i) in items" :key="it.to + i">
            <button
              v-if="it.visible"
              class="rail-item"
              :class="{ active: it.active }"
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
  top: 20px;
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
.rail-item {
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
  transition: background 0.15s ease;
}
.rail-item:hover .pill {
  background: var(--m-color-surface-container-high);
}
.rail-item.active .pill {
  background: var(--m-color-secondary-container);
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

/* ---- 顶栏外观按钮 + 弹层 ---- */
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
  background: var(--m-color-surface-container);
  box-shadow: var(--app-shadow-pop, 0 10px 32px rgba(0, 0, 0, 0.18));
}
.pop-enter-active,
.pop-leave-active {
  transition:
    opacity 0.15s ease,
    transform 0.15s ease;
}
.pop-enter-from,
.pop-leave-to {
  opacity: 0;
  transform: translateY(-4px) scale(0.98);
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
  .rail nav {
    display: flex;
    gap: 6px;
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
