<script setup lang="ts">
import { onMounted, ref } from "vue";
import { MiuixCard, MiuixProgressIndicator, MiuixText } from "miuix-vue";
import { api } from "@/api/client";
import type { DashboardSummary, JsEngines } from "@/api/client";
import { useAuth } from "@/stores/auth";

const auth = useAuth();
const data = ref<DashboardSummary | null>(null);
const loading = ref(true);
const error = ref("");

// ---- QuickJS 状态 ----
const js = ref<JsEngines | null>(null);

onMounted(async () => {
  try {
    data.value = await api.dashboard();
    js.value = (await api.jsEngines().catch(() => null)) ?? null;
  } catch (e) {
    error.value = String(e);
  } finally {
    loading.value = false;
  }
});

const cards = [
  ["users_total", "用户", "/admin/users"],
  ["sources_total", "书源", "/admin/sources"],
  ["rss_sources_total", "订阅源", "/admin/rss-sources"],
  ["media_sources_total", "媒体源", "/admin/media-sources"],
  ["shelf_total", "书架条目", "/shelf"],
  ["roles_total", "权限组", "/admin/roles"],
] as const;
</script>

<template>
  <div>
    <h2 class="page-title">仪表盘</h2>
    <div v-if="loading" class="center"><MiuixProgressIndicator /></div>
    <template v-else-if="data">
      <div class="stat-grid">
        <MiuixCard
          v-for="[k, label, to] in cards"
          :key="k"
          class="stat"
          @click="$router.push(to)"
        >
          <MiuixText type="title1">{{ data[k] }}</MiuixText>
          <div class="stat-label">{{ label }}</div>
        </MiuixCard>
        <MiuixCard
          class="stat component-stat"
          @click="$router.push('/admin/plugins')"
        >
          <MiuixText type="title1">
            {{ data.plugins_total + data.rule_engines_total + data.core_modules_total }}
          </MiuixText>
          <div class="stat-label">组件</div>
          <div class="component-breakdown">
            <span>插件 <b>{{ data.plugins_enabled }}/{{ data.plugins_total }}</b></span>
            <span>规则引擎 <b>{{ data.rule_engines_enabled }}/{{ data.rule_engines_total }}</b></span>
            <span>核心模块 <b>{{ data.core_modules_total }}</b></span>
          </div>
        </MiuixCard>
      </div>

      <MiuixCard class="recent" :show-indication="false">
        <h3>最近注册</h3>
        <table class="md-table">
          <thead>
            <tr><th>ID</th><th>用户名</th><th>昵称</th><th>注册时间</th></tr>
          </thead>
          <tbody>
            <tr v-for="u in data.recent_users" :key="u.id">
              <td>{{ u.id }}</td>
              <td>{{ u.username }}</td>
              <td>{{ u.display_name }}</td>
              <td>{{ u.created_at?.slice(0, 19).replace("T", " ") }}</td>
            </tr>
          </tbody>
        </table>
      </MiuixCard>

      <!-- QuickJS -->
      <MiuixCard class="recent" :show-indication="false">
        <h3>QuickJS</h3>
        <p class="js-desc">
          书源 @js/{{ "{" }}{{ "{" }}{{ "}" }}{{ "}" }} / jsLib 规则固定使用 QuickJS。
          番茄等依赖 Rhino 兼容（JavaImporter/Packages）的源已默认注入兼容层。
        </p>
        <div v-if="js" class="js-row">
          <span class="engine-name">QuickJS</span>
          <span class="engine-state" :class="{ ready: js.current === 'quickjs' }">
            {{ js.current === "quickjs" ? "已安装并启用" : "未安装" }}
          </span>
        </div>
        <div v-else class="js-desc">QuickJS 状态不可用（无 js.read 权限）。</div>
      </MiuixCard>

      <p v-if="auth.isSuperuser" class="tip">
        提示：插件启停、书源、订阅源和媒体源管理均在「管理」分区。
      </p>
    </template>
    <div v-else class="center">{{ error || "无法加载" }}</div>
  </div>
</template>

<style scoped>
.stat-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(160px, 1fr));
  gap: 14px;
}
.stat {
  --app-card-pad: 18px;
  cursor: pointer;
}
.stat-label {
  color: var(--m-color-on-surface-secondary);
  font-size: 13px;
  margin-top: 6px;
}
.component-stat { grid-column: span 2; }
.component-breakdown {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 8px;
  margin-top: 12px;
}
.component-breakdown span {
  padding: 7px 8px;
  border-radius: 10px;
  background: var(--m-color-surface-container-high);
  color: var(--m-color-on-surface-secondary);
  font-size: 11px;
  white-space: nowrap;
}
.component-breakdown b {
  display: block;
  margin-top: 2px;
  color: var(--m-color-on-surface);
  font-size: 13px;
}
.recent {
  --app-card-pad: 18px;
  margin-top: 20px;
}
.recent h3 {
  margin: 0 0 10px;
}
.tip {
  color: var(--m-color-on-background-variant);
  font-size: 13px;
}
.js-desc {
  color: var(--m-color-on-surface-secondary);
  font-size: 13px;
  margin: 0 0 12px;
}
.js-row {
  display: flex;
  align-items: center;
  gap: 12px;
  flex-wrap: wrap;
}
.engine-name { font-size: 14px; font-weight: 600; color: var(--m-color-on-surface); }
.engine-state { padding: 4px 9px; border-radius: 999px; background: var(--m-color-error-container); color: var(--m-color-on-error-container); font-size: 12px; }
.engine-state.ready { background: var(--m-color-primary-container); color: var(--m-color-on-primary-container); }
@media (max-width: 520px) {
  .component-stat { grid-column: 1 / -1; }
}
</style>
