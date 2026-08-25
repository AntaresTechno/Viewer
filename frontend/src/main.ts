import { createApp } from "vue";
import { createPinia } from "pinia";
import App from "./App.vue";
import router from "./router";
import { useThemeStore } from "@/stores/theme";

import "miuix-vue/style.css";
import "./theme/design.css"; // miuix / md3e 双设计 token + 组件形制覆盖
import "./styles/base.css"; // 共享工具类

const pinia = createPinia();
const app = createApp(App).use(pinia).use(router);

// Chrome/Edge 自动填充触发 animationstart（见 base.css 的 onAutoFillStart），
// 但自动填充后浏览器可能不派发 input 事件，导致 v-model 仍是空、按钮被禁用。
// 这里捕获该动画事件，把填充值同步进 Vue 模型（bubbles 的 input 事件冒泡到组件的 @input 上）。
document.addEventListener(
  "animationstart",
  (e) => {
    const t = e.target;
    if (e instanceof AnimationEvent && e.animationName === "onAutoFillStart" && t instanceof HTMLInputElement) {
      // 给浏览器一拍时间填充 value，再以菊花链 rAF 确保取到实际值
      requestAnimationFrame(() => {
        requestAnimationFrame(() => {
          t.dispatchEvent(new Event("input", { bubbles: true }));
        });
      });
    }
  },
  true,
);

// 挂载前应用持久化的设计与明暗模式（index.html 里的引导片段已先行设置，
// 这里把 miuix-vue 内部主题状态对齐到同一取值）。
useThemeStore().init();

app.mount("#app");
