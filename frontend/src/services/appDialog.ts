import { shallowRef } from "vue";

export interface AppDialogOptions {
  title?: string;
  confirmText?: string;
  cancelText?: string;
  danger?: boolean;
}

export interface AppDialogRequest extends Required<AppDialogOptions> {
  kind: "alert" | "confirm";
  message: string;
  resolve: (confirmed: boolean) => void;
}

export const activeAppDialog = shallowRef<AppDialogRequest | null>(null);

const queue: AppDialogRequest[] = [];

function showNext() {
  if (!activeAppDialog.value) activeAppDialog.value = queue.shift() ?? null;
}

function enqueue(
  kind: AppDialogRequest["kind"],
  message: unknown,
  options: AppDialogOptions,
): Promise<boolean> {
  return new Promise((resolve) => {
    queue.push({
      kind,
      message: String(message ?? ""),
      title: options.title ?? (kind === "confirm" ? "请确认" : "提示"),
      confirmText: options.confirmText ?? (kind === "confirm" ? "确定" : "知道了"),
      cancelText: options.cancelText ?? "取消",
      danger: options.danger ?? false,
      resolve,
    });
    showNext();
  });
}

export async function showAlert(
  message: unknown,
  options: AppDialogOptions = {},
): Promise<void> {
  await enqueue("alert", message, options);
}

export function showConfirm(
  message: unknown,
  options: AppDialogOptions = {},
): Promise<boolean> {
  return enqueue("confirm", message, options);
}

export function settleAppDialog(confirmed: boolean) {
  const request = activeAppDialog.value;
  if (!request) return;
  activeAppDialog.value = null;
  request.resolve(confirmed);
  queueMicrotask(showNext);
}
