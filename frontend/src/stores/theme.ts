import { defineStore } from "pinia";
import { nextTick } from "vue";
import { setTheme, setThemeMode, useTheme } from "miuix-vue";

/**
 * 双设计系统主题 store。
 *
 * - design：整体设计语言（组件形状 / 字体 / 色板 / 动效性格）
 *     - "miuix"  → miuix 原生 HyperOS 风（默认蓝 + 灰阶表面，扁平、临界阻尼的克制动效）
 *     - "md3e"   → Material Design 3 Expressive（高饱和色调色板、形状形变按钮、
 *                  弹簧动效、大圆角卡片）
 * - mode：外观模式，交给 miuix-vue 内部状态驱动 `.m-theme-dark`：
 *     - "light" | "dark" | "system"
 *
 * 两项均持久化到 localStorage；`data-design` 属性挂在 <html> 上，
 * 由 theme/design.css 按 `html[data-design=…]` 作用域生效。
 */

export type DesignId = "miuix" | "md3e";
export type ModeId = "light" | "dark" | "system";

const DESIGN_KEY = "viewer_design";
const MODE_KEY = "viewer_theme_mode"; // 沿用历史 key，旧值 light/dark 直接兼容

function loadDesign(): DesignId {
  const v = localStorage.getItem(DESIGN_KEY);
  // 历史值 "md3"（Material You）自动迁移为 "md3e"。
  if (v === "md3") return "md3e";
  return v === "miuix" || v === "md3e" ? v : "md3e";
}

function loadMode(): ModeId {
  const v = localStorage.getItem(MODE_KEY);
  return v === "light" || v === "dark" || v === "system" ? v : "light";
}

// miuix-vue 的主题状态是模块级响应式单例（useTheme 内部不依赖组件 inject），
// 这里取一次，让 isDark 成为真正的响应式派生值。
// 注意：不要改成读 document 上的 class —— 那不是响应式来源，
// Pinia getter 只会求值一次并永久缓存，导致 toggleDark 方向判断失效
// （表现为：切到深色后就再也切不回浅色）。
const { theme: liveTheme } = useTheme();

type Origin = { x: number; y: number };
type VTDocument = Document & {
  startViewTransition?: (cb: () => void | Promise<void>) => {
    ready: Promise<void>;
    finished: Promise<void>;
  };
};

/** 从点击事件取扩散圆心；键盘触发(坐标为 0)时退回元素中心。 */
function clickOrigin(e?: MouseEvent): Origin | undefined {
  if (!e) return undefined;
  let x = e.clientX;
  let y = e.clientY;
  if (!x && !y) {
    const el = e.currentTarget as HTMLElement | null;
    if (el) {
      const r = el.getBoundingClientRect();
      x = r.left + r.width / 2;
      y = r.top + r.height / 2;
    }
  }
  return { x, y };
}

/** 包一层 View Transitions：从点击处圆形扩散出新画面；不支持则直接生效。 */
function runWithThemeTransition(applyFn: () => Promise<void>, origin?: Origin) {
  const doc = document as VTDocument;
  const reduce = window.matchMedia?.("(prefers-reduced-motion: reduce)").matches;
  if (!doc.startViewTransition || reduce || !origin) {
    void applyFn();
    return;
  }
  document.documentElement.classList.add("vt-circle");
  const vt = doc.startViewTransition(() => applyFn());
  vt.ready
    .then(() => {
      const radius = Math.hypot(
        Math.max(origin.x, window.innerWidth - origin.x),
        Math.max(origin.y, window.innerHeight - origin.y),
      );
      document.documentElement.animate(
        {
          clipPath: [
            `circle(0px at ${origin.x}px ${origin.y}px)`,
            `circle(${radius}px at ${origin.x}px ${origin.y}px)`,
          ],
        },
        {
          duration: 480,
          easing: "cubic-bezier(0.33, 0, 0.2, 1)",
          pseudoElement: "::view-transition-new(root)",
        },
      );
    })
    .catch(() => {});
  vt.finished.finally(() => document.documentElement.classList.remove("vt-circle"));
}

export const useThemeStore = defineStore("theme", {
  state: () => ({
    design: loadDesign() as DesignId,
    mode: loadMode() as ModeId,
  }),
  getters: {
    isDark(): boolean {
      return liveTheme.value === "dark";
    },
  },
  actions: {
    /** 应用当前状态到 DOM 与 miuix 内部主题状态。 */
    apply() {
      document.documentElement.dataset.design = this.design;
      if (this.mode === "system") setThemeMode("system");
      else setTheme(this.mode);
    },
    /** 应用启动时调用一次（见 main.ts）。 */
    init() {
      this.apply();
    },
    setDesign(d: DesignId) {
      this.design = d;
      localStorage.setItem(DESIGN_KEY, d);
      this.apply();
    },
    /** e 传入点击事件时，明暗切换带「从点击处扩散」的过渡动画。 */
    setMode(m: ModeId, e?: MouseEvent) {
      this.mode = m;
      localStorage.setItem(MODE_KEY, m);
      runWithThemeTransition(async () => {
        this.apply();
        await nextTick(); // 确保 miuix 的 class watcher 先落地再截取新画面
      }, clickOrigin(e));
    },
    /** 顶栏快捷键：在浅色/深色之间直接翻转（脱离 system 模式）。 */
    toggleDark(e?: MouseEvent) {
      this.setMode(this.isDark ? "light" : "dark", e);
    },
  },
});
