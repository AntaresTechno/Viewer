<script setup lang="ts">
import { computed, ref, watch } from "vue";
import { useRoute, useRouter } from "vue-router";
import { MiuixButton, MiuixCard, MiuixText } from "miuix-vue";
import { api, errMsg } from "@/api/client";
import type { ProtocolAction, ProtocolImportResult } from "@/api/client";

const route = useRoute();
const router = useRouter();
const loading = ref(false);
const executing = ref(false);
const error = ref("");
const resolved = ref<ProtocolAction | null>(null);
const result = ref<ProtocolImportResult | null>(null);

function one(value: unknown): string {
  return Array.isArray(value) ? String(value[0] ?? "") : String(value ?? "");
}

const protocolUri = computed(() => {
  const supplied = one(route.query.uri || route.query.url).trim();
  if (supplied) return supplied;
  const scheme = one(route.params.scheme).trim();
  const host = one(route.params.host).trim();
  const action = one(route.params.action).trim();
  const src = one(route.query.src).trim();
  if (!scheme || !host || !action || !src) return "";
  return `${scheme}://${host}/${action}?src=${encodeURIComponent(src)}`;
});

const sourceUrl = computed(() => resolved.value?.payload.src ?? "");

async function resolve() {
  resolved.value = null;
  result.value = null;
  error.value = "";
  if (!protocolUri.value) {
    error.value = "缺少协议链接。请使用 /protocol?uri=… 或跨平台桥接路径。";
    return;
  }
  loading.value = true;
  try {
    resolved.value = await api.protocolResolve(protocolUri.value);
  } catch (e) {
    error.value = errMsg(e);
  } finally {
    loading.value = false;
  }
}

async function execute() {
  if (!resolved.value || executing.value) return;
  executing.value = true;
  error.value = "";
  try {
    result.value = await api.protocolExecute(resolved.value);
  } catch (e) {
    error.value = errMsg(e);
  } finally {
    executing.value = false;
  }
}

watch(() => route.fullPath, resolve, { immediate: true });
</script>

<template>
  <div class="protocol-page">
    <div class="page-head">
      <div>
        <h2 class="page-title">协议桥</h2>
        <p class="page-sub">安全解析来自其他平台和应用的自定义协议链接</p>
      </div>
    </div>

    <MiuixCard class="action-card" :show-indication="false">
      <div v-if="loading" class="state">正在解析协议…</div>
      <div v-else-if="resolved" class="content">
        <div class="protocol-badge">{{ resolved.scheme }}://</div>
        <MiuixText type="title2">{{ resolved.title }}</MiuixText>
        <p class="description">{{ resolved.description }}</p>

        <div class="field">
          <span>动作</span>
          <code>{{ resolved.action }}</code>
        </div>
        <div class="field source-field">
          <span>远程来源</span>
          <code>{{ sourceUrl }}</code>
        </div>

        <div v-if="result" class="success">
          <div>导入完成：新增 {{ result.added }}，更新 {{ result.updated }}，跳过 {{ result.skipped }}。</div>
          <div v-if="result.classified" class="classified">
            <span>书源 {{ result.classified.books }}</span>
            <span>订阅 {{ result.classified.subscriptions }}</span>
            <span>漫画 {{ result.classified.comic }}</span>
            <span>音频 {{ result.classified.audio }}</span>
            <span>视频 {{ result.classified.video }}</span>
          </div>
        </div>
        <p v-else class="warning">确认后才会下载、识别并分类写入；协议链接本身不会自动执行导入。</p>

        <div class="actions">
          <MiuixButton @click="router.push('/shelf')">取消</MiuixButton>
          <template v-if="result">
            <MiuixButton v-if="result.classified?.books" @click="router.push('/admin/sources')">查看书源</MiuixButton>
            <MiuixButton v-if="result.classified?.subscriptions" @click="router.push('/admin/rss-sources')">查看订阅源</MiuixButton>
            <MiuixButton v-if="(result.classified?.comic || 0) + (result.classified?.audio || 0) + (result.classified?.video || 0)" type="primary" @click="router.push('/admin/media-sources')">查看媒体源</MiuixButton>
          </template>
          <MiuixButton v-else type="primary" :disabled="executing" @click="execute">
            {{ executing ? "正在导入…" : "确认导入" }}
          </MiuixButton>
        </div>
      </div>
      <div v-else class="state">
        <p>{{ error || "无法解析协议链接" }}</p>
        <MiuixButton @click="resolve">重新解析</MiuixButton>
      </div>
      <p v-if="error && resolved" class="inline-error">{{ error }}</p>
    </MiuixCard>

    <p class="bridge-help">
      跨平台链接格式：<code>/protocol/legado/import/bookSource?src=&lt;JSON 地址&gt;</code>
    </p>
  </div>
</template>

<style scoped>
.protocol-page { max-width: 760px; margin: 0 auto; }
.page-head { margin-bottom: 18px; }
.page-sub { margin: 4px 0 0; color: var(--m-color-on-surface-secondary); }
.action-card { --app-card-pad: 22px; }
.content { display: grid; gap: 12px; }
.protocol-badge { width: fit-content; padding: 4px 10px; border-radius: 999px; background: var(--m-color-secondary-container); color: var(--m-color-on-secondary-container); font: 600 12px Consolas, monospace; }
.description,.warning,.bridge-help { color: var(--m-color-on-surface-secondary); font-size: 13px; }
.field { display: grid; grid-template-columns: 96px minmax(0, 1fr); gap: 10px; align-items: start; padding: 10px 0; border-top: 1px solid var(--m-color-outline-variant); }
.field span { color: var(--m-color-on-surface-secondary); font-size: 13px; }
.field code { overflow-wrap: anywhere; color: var(--m-color-on-surface); }
.success { padding: 12px; border-radius: 12px; background: var(--m-color-secondary-container); color: var(--m-color-on-secondary-container); }
.classified { display: flex; flex-wrap: wrap; gap: 7px; margin-top: 9px; }
.classified span { padding: 3px 8px; border-radius: 999px; background: color-mix(in srgb, currentColor 10%, transparent); font-size: 12px; }
.actions { display: flex; justify-content: flex-end; gap: 8px; margin-top: 4px; }
.state { min-height: 180px; display: grid; place-content: center; justify-items: center; gap: 12px; color: var(--m-color-on-surface-secondary); text-align: center; }
.inline-error { margin: 14px 0 0; color: var(--m-color-error); }
.bridge-help { text-align: center; }
.bridge-help code { overflow-wrap: anywhere; }
@media (max-width: 600px) {
  .field { grid-template-columns: 1fr; gap: 5px; }
  .actions { justify-content: stretch; }
  .actions > * { flex: 1; }
}
</style>
