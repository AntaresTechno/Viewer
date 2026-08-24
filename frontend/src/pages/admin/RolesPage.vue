<script setup lang="ts">
import { computed, onMounted, ref } from "vue";
import {
  MiuixButton,
  MiuixCard,
  MiuixCheckbox,
  MiuixDialog,
  MiuixInput,
  MiuixText,
} from "miuix-vue";
import { api, errMsg } from "@/api/client";
import type { PermissionCatalog, RoleItem } from "@/api/client";

const roles = ref<RoleItem[]>([]);
const catalog = ref<PermissionCatalog | null>(null);
const loading = ref(true);
const error = ref("");

/* editor state */
const showEditor = ref(false);
const editingId = ref<number | null>(null); // null = create
const fName = ref("");
const fDesc = ref("");
const fPerms = ref<string[]>([]);
const formErr = ref("");

async function load() {
  loading.value = true;
  error.value = "";
  try {
    const [r, c] = await Promise.all([api.rolesList(), api.permCatalog()]);
    roles.value = r.items;
    catalog.value = c;
  } catch (e) {
    error.value = errMsg(e);
  } finally {
    loading.value = false;
  }
}
onMounted(load);

const groups = computed(() =>
  Object.entries(catalog.value?.grouped ?? {}).map(([ns, items]) => ({
    ns,
    items,
  })),
);

function openCreate() {
  editingId.value = null;
  fName.value = "";
  fDesc.value = "";
  fPerms.value = [];
  formErr.value = "";
  showEditor.value = true;
}

function openEdit(r: RoleItem) {
  editingId.value = r.id;
  fName.value = r.name;
  fDesc.value = r.description;
  fPerms.value = [...r.permissions];
  formErr.value = "";
  showEditor.value = true;
}

function togglePerm(key: string, on: boolean) {
  const set = new Set(fPerms.value);
  if (on) set.add(key);
  else set.delete(key);
  // keep wildcard consistency
  set.delete(`${key.split(".")[0]}.*`);
  fPerms.value = [...set];
}

function groupAllOn(ns: string) {
  return (catalog.value?.grouped[ns] ?? []).every((i) => hasPerm(i.key));
}
function hasPerm(key: string) {
  return fPerms.value.includes(key);
}
function toggleGroup(ns: string, on: boolean) {
  const keys = (catalog.value?.grouped[ns] ?? []).map((i) => i.key);
  const set = new Set(fPerms.value.filter((k) => !keys.includes(k)));
  if (on) {
    keys.forEach((k) => set.add(k));
  } else {
    set.add(`${ns}.*`); // fall back to wildcard when unchecking everything
  }
  fPerms.value = [...set];
}

async function save() {
  formErr.value = "";
  if (!fName.value.trim()) {
    formErr.value = "名称不能为空";
    return;
  }
  try {
    const body = {
      name: fName.value.trim(),
      description: fDesc.value,
      permissions: [...fPerms.value],
    };
    if (editingId.value === null) await api.roleCreate(body);
    else await api.roleUpdate(editingId.value, body);
    showEditor.value = false;
    await load();
  } catch (e) {
    formErr.value = errMsg(e);
  }
}

async function remove(r: RoleItem) {
  if (!confirm(`删除权限组 ${r.name}？`)) return;
  try {
    await api.roleDelete(r.id);
    await load();
  } catch (e) {
    alert(errMsg(e));
  }
}
</script>

<template>
  <div>
    <div class="bar">
      <h2 class="page-title">权限组</h2>
      <MiuixButton type="primary" @click="openCreate">新建权限组</MiuixButton>
    </div>

    <div class="role-grid">
      <MiuixCard v-for="r in roles" :key="r.id" class="role-card" :show-indication="false">
        <div class="role-head">
          <MiuixText type="title3">{{ r.name }}</MiuixText>
          <span v-if="r.is_system" class="sys-tag">系统</span>
        </div>
        <p class="desc">{{ r.description || "（无描述）" }}</p>
        <div class="meta">
          {{ r.permissions.includes("*") ? "全部权限 *" : `${r.permissions.length} 项权限` }}
          · {{ r.users_count ?? 0 }} 个用户
        </div>
        <div class="ops">
          <MiuixButton :disabled="r.is_system" @click="openEdit(r)">编辑</MiuixButton>
          <MiuixButton :disabled="r.is_system" @click="remove(r)">删除</MiuixButton>
        </div>
      </MiuixCard>
    </div>

    <MiuixDialog
      :model-value="showEditor"
      :title="editingId === null ? '新建权限组' : `编辑：${fName}`"
      @update:model-value="(v: boolean) => { if (!v) showEditor = false }"
    >
      <div class="dlg">
        <div class="row2">
          <MiuixInput v-model="fName" label="名称" single-line />
          <MiuixInput v-model="fDesc" label="描述" single-line />
        </div>
        <div class="perm-scroll">
          <fieldset v-for="g in groups" :key="g.ns" class="perm-group">
            <legend>
              <label class="g-all">
                <input
                  type="checkbox"
                  :checked="groupAllOn(g.ns)"
                  @change="toggleGroup(g.ns, ($event.target as HTMLInputElement).checked)"
                />
                {{ g.ns }}
              </label>
            </legend>
            <label v-for="it in g.items" :key="it.key" class="perm-item">
              <MiuixCheckbox
                :model-value="hasPerm(it.key)"
                @update:model-value="(v: boolean) => togglePerm(it.key, v)"
              />
              <span class="pk">{{ it.key }}</span>
              <span class="pt">{{ it.title }}</span>
            </label>
          </fieldset>
        </div>
        <div class="wild-row">
          <MiuixCheckbox
            :model-value="fPerms.includes('*')"
            @update:model-value="
              (v: boolean) => {
                const s = new Set(fPerms);
                v ? s.add('*') : s.delete('*');
                fPerms = [...s];
              }
            "
          />
          超级权限（*，拥有一切权限）
        </div>
        <div v-if="formErr" class="err">{{ formErr }}</div>
      </div>
      <div class="dlg-actions">
        <MiuixButton @click="showEditor = false">取消</MiuixButton>
        <MiuixButton type="primary" @click="save">保存</MiuixButton>
      </div>
    </MiuixDialog>

    <div v-if="loading" class="center">加载中…</div>
    <div v-if="error" class="err center">{{ error }}</div>
  </div>
</template>

<style scoped>
.role-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(240px, 1fr));
  gap: 14px;
}
.role-card {
  --app-card-pad: 16px;
}
.role-head {
  display: flex;
  align-items: center;
  gap: 8px;
}
.sys-tag {
  background: var(--m-color-tertiary-container);
  color: var(--m-color-on-tertiary-container);
  border-radius: 999px;
  padding: 2px 10px;
  font-size: 11px;
}
.desc {
  color: var(--m-color-on-surface-secondary);
  font-size: 13px;
  margin: 8px 0;
  min-height: 18px;
}
.meta {
  font-size: 12px;
  color: var(--m-color-on-background-variant);
  margin-bottom: 12px;
}
.ops {
  display: flex;
  gap: 8px;
}
.dlg {
  width: 560px;
  max-width: 80vw;
}
.row2 {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 10px;
}
.perm-scroll {
  max-height: 320px;
  overflow-y: auto;
  display: flex;
  flex-direction: column;
  gap: 8px;
  padding-right: 4px;
}
.perm-group {
  border: 1px solid var(--m-color-outline);
  border-radius: 12px;
  padding: 8px 10px;
  margin: 0;
}
.perm-group legend {
  font-size: 13px;
  font-weight: 600;
  padding: 0 6px;
}
.g-all {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  cursor: pointer;
}
.perm-item {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 3px 4px;
  cursor: pointer;
  border-radius: 8px;
  font-size: 13px;
}
.perm-item:hover {
  background: var(--m-color-surface-container-high);
}
.pk {
  font-family: Consolas, monospace;
  color: var(--m-color-primary);
  min-width: 150px;
}
.pt {
  color: var(--m-color-on-surface-secondary);
}
.wild-row {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 13px;
}
</style>
