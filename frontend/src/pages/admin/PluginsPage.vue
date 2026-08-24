<script setup lang="ts">
import { onMounted, ref } from "vue";
import { MiuixButton, MiuixCard, MiuixSwitch, MiuixText } from "miuix-vue";
import { api, errMsg } from "@/api/client";
import type { PluginItem } from "@/api/client";

const items = ref<PluginItem[]>([]);
const loading = ref(true);
const error = ref("");
const uploading = ref(false);
const zipInput = ref<HTMLInputElement | null>(null);

async function load() {
  loading.value = true;
  try {
    items.value = (await api.pluginsList()).items;
  } catch (e) {
    error.value = errMsg(e);
  } finally {
    loading.value = false;
  }
}
onMounted(load);

async function toggle(p: PluginItem, on: boolean) {
  try {
    await api.pluginToggle(p.name, on);
    p.enabled = on;
    if (!on) {
      alert("已停用：重启后端后生效");
    }
  } catch (e) {
    alert(errMsg(e));
  }
}

function pickZip() {
  zipInput.value?.click();
}

async function onZipPicked(e: Event) {
  const input = e.target as HTMLInputElement;
  const file = input.files?.[0];
  input.value = ""; // 允许重复选择同一文件
  if (!file) return;
  if (!/\.zip$/i.test(file.name)) {
    alert("请选择 .zip 插件包");
    return;
  }
  uploading.value = true;
  try {
    const r = await api.pluginInstall(file);
    const lines = [`插件「${r.name}」已安装`];
    if (r.note) lines.push(r.note);
    if (r.title && r.version) lines.unshift(`${r.title} v${r.version}`);
    alert(lines.join("\n"));
    await load();
  } catch (err) {
    alert(errMsg(err));
  } finally {
    uploading.value = false;
  }
}
</script>

<template>
  <div>
    <div class="bar">
      <h2 class="page-title">插件管理</h2>
      <MiuixButton type="primary" :disabled="uploading" @click="pickZip">
        {{ uploading ? "安装中…" : "上传 ZIP 安装" }}
      </MiuixButton>
      <input
        ref="zipInput"
        type="file"
        accept=".zip,application/zip,application/x-zip-compressed"
        hidden
        @change="onZipPicked"
      />
    </div>
    <p class="sub">
      每个插件挂载在 /api/&lt;mount&gt; 下，提供独立路由与权限声明；停用后需重启后端生效。
      上传的插件包为 ZIP 格式，根目录需包含 plugin.py。
    </p>
    <div v-if="loading" class="center">加载中…</div>
    <div v-else-if="error" class="center err">{{ error }}</div>
    <div v-else class="plug-grid">
      <MiuixCard v-for="p in items" :key="p.name" class="plug" :show-indication="false">
        <div class="row">
          <div>
            <MiuixText type="title3">{{ p.title }}</MiuixText>
            <span class="ver">v{{ p.version }}</span>
          </div>
          <MiuixSwitch :model-value="p.enabled" @update:model-value="(v: boolean) => toggle(p, v)" />
        </div>
        <p class="desc">{{ p.description }}</p>
        <code class="mount">/api/{{ p.mount }}</code>
      </MiuixCard>
    </div>
  </div>
</template>

<style scoped>
.sub {
  color: var(--m-color-on-surface-secondary);
  font-size: 13px;
  margin-top: 0;
}
.plug-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(280px, 1fr));
  gap: 14px;
}
.plug {
  --app-card-pad: 16px;
}
.row {
  display: flex;
  justify-content: space-between;
  align-items: center;
}
.ver {
  margin-left: 8px;
  color: var(--m-color-on-background-variant);
  font-size: 12px;
}
.desc {
  color: var(--m-color-on-surface-secondary);
  font-size: 13px;
  min-height: 36px;
}
.mount {
  font-family: Consolas, monospace;
  font-size: 12px;
  background: var(--m-color-surface-container-high);
  padding: 3px 8px;
  border-radius: 8px;
  color: var(--m-color-primary);
}
</style>
