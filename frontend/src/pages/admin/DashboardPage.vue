<script setup lang="ts">
import { onMounted, ref } from "vue";
import { MiuixCard, MiuixText, MiuixProgressIndicator } from "miuix-vue";
import { api } from "@/api/client";
import type { DashboardSummary } from "@/api/client";
import { useAuth } from "@/stores/auth";

const auth = useAuth();
const data = ref<DashboardSummary | null>(null);
const loading = ref(true);
const error = ref("");

onMounted(async () => {
  try {
    data.value = await api.dashboard();
  } catch (e) {
    error.value = String(e);
  } finally {
    loading.value = false;
  }
});

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

      <p v-if="auth.isSuperuser" class="tip">
        提示：插件启停与书源管理在左侧「管理」分区。
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
</style>
