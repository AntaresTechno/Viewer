<script setup lang="ts">
import { computed, onMounted, ref } from "vue";
import { MiuixButton, MiuixCard, MiuixSwitch, MiuixText } from "miuix-vue";
import { api, errMsg } from "@/api/client";
import type { PluginItem } from "@/api/client";

const items = ref<PluginItem[]>([]);
const loading = ref(true);
const error = ref("");
const uploading = ref(false);
const zipInput = ref<HTMLInputElement | null>(null);

const sections = computed(() => ([
  {
    kind: "engine" as const,
    title: "规则引擎",
    description: "负责解析书源协议与规则，可按需安装、启用或停用。",
  },
  {
    kind: "plugin" as const,
    title: "插件",
    description: "为应用增加可选功能，可独立启用或停用。",
  },
  {
    kind: "core" as const,
    title: "核心模块",
    description: "应用运行与基础业务所需的内置模块，始终启用。",
  },
].map((section) => ({
  ...section,
  items: items.value.filter((item) => item.kind === section.kind),
}))));

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
  if (!p.canToggle) return;
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
    alert("请选择 .zip 组件包");
    return;
  }
  uploading.value = true;
  try {
    const r = await api.pluginInstall(file);
    const lines = [`组件「${r.name}」已安装`];
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
      组件通过 <code>PLUGIN.kind</code> 明确声明为规则引擎、插件或核心模块；
      上传包为 ZIP 格式，根目录需包含 plugin.py。
    </p>
    <div v-if="loading" class="center">加载中…</div>
    <div v-else-if="error" class="center err">{{ error }}</div>
    <div v-else class="sections">
      <section v-for="section in sections" :key="section.kind" class="kind-section">
        <div class="section-head">
          <div>
            <h3>{{ section.title }} <span class="count">{{ section.items.length }}</span></h3>
            <p>{{ section.description }}</p>
          </div>
        </div>
        <div v-if="section.items.length" class="plug-grid">
          <MiuixCard v-for="p in section.items" :key="p.name" class="plug" :show-indication="false">
            <div class="row">
              <div class="title-wrap">
                <MiuixText type="title3">{{ p.title }}</MiuixText>
                <span class="ver">v{{ p.version }}</span>
              </div>
              <MiuixSwitch
                v-if="p.canToggle"
                :model-value="p.enabled"
                @update:model-value="(v: boolean) => toggle(p, v)"
              />
              <span v-else class="fixed">固定启用</span>
            </div>
            <p class="desc">{{ p.description }}</p>
            <div class="meta-row">
              <code v-if="p.mount" class="mount">/api/{{ p.mount }}</code>
              <span class="kind-badge" :class="p.kind">{{ p.kindLabel }}</span>
              <span v-if="p.legacyManifest" class="legacy" title="建议迁移为 PLUGIN 声明">旧声明</span>
            </div>
          </MiuixCard>
        </div>
        <p v-else class="none">暂无{{ section.title }}</p>
      </section>
    </div>
  </div>
</template>

<style scoped>
.sub {
  color: var(--m-color-on-surface-secondary);
  font-size: 13px;
  margin-top: 0;
}
.sub code { color: var(--m-color-primary); }
.sections { display: grid; gap: 26px; }
.section-head { margin-bottom: 11px; }
.section-head h3 { margin: 0; color: var(--m-color-on-surface); font-size: 18px; }
.section-head p,.none { margin: 4px 0 0; color: var(--m-color-on-surface-secondary); font-size: 13px; }
.count { display: inline-grid; place-items: center; min-width: 22px; height: 22px; margin-left: 5px; border-radius: 999px; background: var(--m-color-surface-container-high); color: var(--m-color-on-surface-secondary); font-size: 12px; font-weight: 500; }
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
.title-wrap { min-width: 0; }
.fixed { padding: 4px 9px; border-radius: 999px; background: var(--m-color-surface-container-high); color: var(--m-color-on-surface-secondary); font-size: 11px; white-space: nowrap; }
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
.meta-row { display: flex; align-items: center; flex-wrap: wrap; gap: 7px; }
.kind-badge,.legacy { padding: 3px 8px; border-radius: 999px; font-size: 11px; }
.kind-badge.engine { background: var(--m-color-tertiary-container, var(--m-color-secondary-container)); color: var(--m-color-on-tertiary-container, var(--m-color-on-secondary-container)); }
.kind-badge.plugin { background: var(--m-color-secondary-container); color: var(--m-color-on-secondary-container); }
.kind-badge.core { background: var(--m-color-surface-container-high); color: var(--m-color-on-surface-secondary); }
.legacy { color: var(--m-color-error); border: 1px solid color-mix(in srgb, var(--m-color-error) 45%, transparent); }
</style>
