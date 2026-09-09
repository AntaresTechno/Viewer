<script setup lang="ts">
import { MiuixButton } from "miuix-vue";

defineProps<{
  selectedCount: number;
  totalCount: number;
  allSelected: boolean;
  busy?: boolean;
  showEnable?: boolean;
  showDisable?: boolean;
  showDelete?: boolean;
}>();

defineEmits<{
  toggleAll: [];
  clear: [];
  enable: [];
  disable: [];
  delete: [];
}>();
</script>

<template>
  <div class="batch-bar" :class="{ active: selectedCount > 0 }">
    <span class="summary">
      {{ selectedCount ? `已选择 ${selectedCount} 项` : `当前共 ${totalCount} 项` }}
    </span>
    <div class="actions">
      <MiuixButton :disabled="busy || totalCount === 0" @click="$emit('toggleAll')">
        {{ allSelected ? "取消全选" : "全选当前" }}
      </MiuixButton>
      <MiuixButton
        v-if="selectedCount && showEnable !== false"
        :disabled="busy"
        @click="$emit('clear')"
      >清除选择</MiuixButton>
      <span v-if="selectedCount" class="divider" aria-hidden="true"></span>
      <MiuixButton
        v-if="selectedCount && showDisable !== false"
        :disabled="busy"
        @click="$emit('enable')"
      >批量启用</MiuixButton>
      <MiuixButton
        v-if="selectedCount && showDelete !== false"
        :disabled="busy"
        @click="$emit('disable')"
      >批量停用</MiuixButton>
      <MiuixButton
        v-if="selectedCount"
        class="danger"
        :disabled="busy"
        @click="$emit('delete')"
      >批量删除</MiuixButton>
    </div>
  </div>
</template>

<style scoped>
.batch-bar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  min-height: 42px;
  margin: 0 0 12px;
  padding: 7px 10px 7px 14px;
  border: 1px solid var(--app-color-outline-variant, var(--m-color-outline));
  border-radius: var(--app-shape-lg, 16px);
  background: var(--m-color-surface-container, transparent);
}
.batch-bar.active {
  border-color: color-mix(in srgb, var(--m-color-primary) 35%, transparent);
  background: color-mix(in srgb, var(--m-color-primary) 7%, var(--m-color-surface));
}
.summary {
  color: var(--m-color-on-surface-secondary);
  font-size: 13px;
  white-space: nowrap;
}
.active .summary {
  color: var(--m-color-primary);
  font-weight: 650;
}
.actions {
  display: flex;
  align-items: center;
  justify-content: flex-end;
  flex-wrap: wrap;
  gap: 7px;
}
.divider {
  width: 1px;
  height: 22px;
  margin: 0 2px;
  background: var(--app-color-outline-variant, var(--m-color-outline));
}
.danger {
  color: var(--m-color-error, #b3261e);
}
@media (max-width: 720px) {
  .batch-bar {
    align-items: flex-start;
    flex-direction: column;
  }
  .actions {
    justify-content: flex-start;
  }
}
</style>
