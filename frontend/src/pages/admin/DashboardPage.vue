<script setup lang="ts">
import { computed, onMounted, ref } from "vue";
import { MiuixButton, MiuixCard, MiuixProgressIndicator, MiuixText } from "miuix-vue";
import { api } from "@/api/client";
import type { DashboardSummary, JsEngines } from "@/api/client";
import { useAuth } from "@/stores/auth";

const auth = useAuth();
const data = ref<DashboardSummary | null>(null);
const loading = ref(true);
const error = ref("");

// ---- JS 引擎设置 ----
const js = ref<JsEngines | null>(null);
const jsSel = ref("auto");
const jsMsg = ref("");
const jsBusy = ref(false);
const canManageJs = computed(
  () => auth.isSuperuser || auth.can("js.manage"),
);

onMounted(async () => {
  try {
    data.value = await api.dashboard();
    js.value = (await api.jsEngines().catch(() => null)) ?? null;
    if (js.value) jsSel.value = js.value.requested || "auto";
  } catch (e) {
    error.value = String(e);
  } finally {
    loading.value = false;
  }
});

async function applyJsEngine() {
  jsMsg.value = "";
  jsBusy.value = true;
  try {
    js.value = await api.jsSetEngine(jsSel.value);
    jsMsg.value = "已切换，新值对之后的书源规则生效";
  } catch (e) {
    jsMsg.value = String(e);
  } finally {
    jsBusy.value = false;
  }
}

const cards = [
  ["users_total", "用户", "/admin/users"],
  ["sources_total", "书源", "/admin/sources"],
  ["shelf_total", "书架条目", "/shelf"],
  ["roles_total", "权限组", "/admin/roles"],
  ["plugins_total", "插件（启用/总数）", "/admin/plugins"],
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
          <MiuixText type="title1">
            {{
              k === "plugins_total"
                ? `${data.plugins_enabled}/${data.plugins_total}`
                : data[k]
            }}
          </MiuixText>
          <div class="stat-label">{{ label }}</div>
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

      <!-- JS 引擎 -->
      <MiuixCard class="recent" :show-indication="false">
        <h3>JS 引擎</h3>
        <p class="js-desc">
          书源 @js/{{ "{" }}{{ "{" }}{{ "}" }}{{ "}" }} / jsLib 规则的 JS 运行时。
          番茄等依赖 Rhino 兼容（JavaImporter/Packages）的源已默认注入兼容层。
        </p>
        <div v-if="js" class="js-row">
          <label class="eng-label" for="js-engine">引擎</label>
          <select
            id="js-engine"
            v-model="jsSel"
            class="eng-select"
            :disabled="!canManageJs || jsBusy"
          >
            <option value="auto">自动选择（推荐）</option>
            <option
              v-for="it in js.items"
              :key="it.key"
              :value="it.key"
              :disabled="!it.installed"
            >
              {{ it.title }}{{ it.installed ? "" : "（未安装）" }}
              {{ it.current ? "· 当前" : "" }}
            </option>
          </select>
          <MiuixButton
            v-if="canManageJs"
            type="primary"
            :disabled="jsBusy"
            @click="applyJsEngine"
          >
            应用
          </MiuixButton>
        </div>
        <div v-else class="js-desc">JS 引擎状态不可用（无 js.read 权限或插件未启用）。</div>
        <p v-if="jsMsg" class="js-msg">{{ jsMsg }}</p>
      </MiuixCard>

      <p v-if="auth.isSuperuser" class="tip">
        提示：插件启停与书源管理在「管理」分区。
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
.eng-label {
  font-size: 13px;
  color: var(--m-color-on-surface-secondary);
}
.eng-select {
  flex: 1;
  min-width: 200px;
  padding: 10px 12px;
  border: 1px solid color-mix(in srgb, var(--m-color-on-surface) 20%, transparent);
  border-radius: var(--app-radius-input, 12px);
  background: var(--m-color-surface-container);
  color: var(--m-color-on-surface);
  font-size: 14px;
  font-family: inherit;
}
.js-msg {
  color: var(--m-color-primary);
  font-size: 13px;
  margin: 10px 0 0;
}
</style>
