<script setup lang="ts">
import { MiuixButton, MiuixDialog } from "miuix-vue";
import { activeAppDialog, settleAppDialog } from "@/services/appDialog";

function onOpenChange(open: boolean) {
  if (!open) settleAppDialog(false);
}
</script>

<template>
  <MiuixDialog
    v-if="activeAppDialog"
    :model-value="true"
    :title="activeAppDialog.title"
    @update:model-value="onOpenChange"
  >
    <p class="message">{{ activeAppDialog.message }}</p>
    <div class="actions">
      <MiuixButton
        v-if="activeAppDialog.kind === 'confirm'"
        @click="settleAppDialog(false)"
      >{{ activeAppDialog.cancelText }}</MiuixButton>
      <MiuixButton
        type="primary"
        :class="{ danger: activeAppDialog.danger }"
        @click="settleAppDialog(true)"
      >{{ activeAppDialog.confirmText }}</MiuixButton>
    </div>
  </MiuixDialog>
</template>

<style scoped>
.message {
  min-width: min(360px, 72vw);
  max-width: min(560px, 78vw);
  margin: 0;
  color: var(--m-color-on-surface);
  line-height: 1.65;
  white-space: pre-wrap;
  overflow-wrap: anywhere;
}
.actions {
  display: flex;
  justify-content: flex-end;
  gap: 10px;
}
.danger {
  --m-color-primary: var(--m-color-error, #b3261e);
}
@media (max-width: 520px) {
  .message {
    min-width: 0;
    max-width: none;
  }
}
</style>
