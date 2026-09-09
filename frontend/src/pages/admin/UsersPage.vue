<script setup lang="ts">
import { computed, onMounted, ref } from "vue";
import {
  MiuixButton,
  MiuixCard,
  MiuixCheckbox,
  MiuixDialog,
  MiuixInput,
  MiuixSwitch,
} from "miuix-vue";
import { api, errMsg } from "@/api/client";
import type { RoleItem, UserPublic } from "@/api/client";
import { showAlert, showConfirm } from "@/services/appDialog";
import BatchActionBar from "@/components/admin/BatchActionBar.vue";

const users = ref<UserPublic[]>([]);
const roles = ref<RoleItem[]>([]);
const loading = ref(true);
const error = ref("");
const selectedIds = ref<number[]>([]);
const batchBusy = ref(false);

/* create dialog */
const showCreate = ref(false);
const cName = ref("");
const cDisplay = ref("");
const cPw = ref("");
const cRoleIds = ref<number[]>([]);
const createErr = ref("");

/* reset password dialog */
const resetTarget = ref<UserPublic | null>(null);
const resetPw = ref("");
const resetErr = ref("");

async function load() {
  loading.value = true;
  error.value = "";
  try {
    const [u, r] = await Promise.all([api.usersList(), api.rolesList()]);
    users.value = u.items;
    roles.value = r.items;
    const known = new Set(users.value.map((user) => user.id));
    selectedIds.value = selectedIds.value.filter((id) => known.has(id));
  } catch (e) {
    error.value = errMsg(e);
  } finally {
    loading.value = false;
  }
}
onMounted(load);

async function doCreate() {
  createErr.value = "";
  if (cName.value.length < 2 || cPw.value.length < 6) {
    createErr.value = "用户名≥2字符，密码≥6位";
    return;
  }
  try {
    await api.userCreate({
      username: cName.value,
      password: cPw.value,
      display_name: cDisplay.value,
      role_ids: [...cRoleIds.value],
    });
    showCreate.value = false;
    cName.value = "";
    cDisplay.value = "";
    cPw.value = "";
    cRoleIds.value = [];
    await load();
  } catch (e) {
    createErr.value = errMsg(e);
  }
}

function roleName(id: number) {
  return roles.value.find((r) => r.id === id)?.name ?? `#${id}`;
}

async function toggleActive(u: UserPublic) {
  try {
    await api.userUpdate(u.id, { is_active: !u.is_active });
    u.is_active = !u.is_active;
  } catch (e) {
    await showAlert(errMsg(e));
  }
}

async function toggleSuper(u: UserPublic) {
  try {
    await api.userUpdate(u.id, { is_superuser: !u.is_superuser });
    u.is_superuser = !u.is_superuser;
  } catch (e) {
    await showAlert(errMsg(e));
  }
}

async function doReset() {
  if (!resetTarget.value) return;
  resetErr.value = "";
  if (resetPw.value.length < 6) {
    resetErr.value = "密码至少 6 位";
    return;
  }
  try {
    await api.userResetPassword(resetTarget.value.id, resetPw.value);
    resetTarget.value = null;
    resetPw.value = "";
  } catch (e) {
    resetErr.value = errMsg(e);
  }
}

async function removeUser(u: UserPublic) {
  if (!await showConfirm(
    `确定删除用户 ${u.username}？`,
    { title: "删除用户", confirmText: "删除", danger: true },
  )) return;
  try {
    await api.userDelete(u.id);
    await load();
  } catch (e) {
    await showAlert(errMsg(e));
  }
}

function toggleRole(u: UserPublic, rid: number) {
  const has = u.role_ids.includes(rid);
  const next = has ? u.role_ids.filter((x) => x !== rid) : [...u.role_ids, rid];
  api
    .userUpdate(u.id, { role_ids: next })
    .then(() => (u.role_ids = next))
    .catch((e) => void showAlert(errMsg(e)));
}

async function toggleRoleAdd(u: UserPublic, rid: number) {
  try {
    await api.userUpdate(u.id, { role_ids: [...u.role_ids, rid] });
    u.role_ids = [...u.role_ids, rid];
  } catch (e) {
    await showAlert(errMsg(e));
  }
}

const visibleIds = computed(() => users.value.map((user) => user.id));
const allVisibleSelected = computed(() =>
  visibleIds.value.length > 0
  && visibleIds.value.every((id) => selectedIds.value.includes(id)),
);

function toggleSelected(id: number) {
  selectedIds.value = selectedIds.value.includes(id)
    ? selectedIds.value.filter((value) => value !== id)
    : [...selectedIds.value, id];
}

function toggleAllVisible() {
  selectedIds.value = allVisibleSelected.value ? [] : [...visibleIds.value];
}

async function batchSetActive(active: boolean) {
  const ids = [...selectedIds.value];
  if (!ids.length || batchBusy.value) return;
  batchBusy.value = true;
  try {
    await api.usersSetActive(ids, active);
    const selected = new Set(ids);
    users.value.forEach((user) => {
      if (selected.has(user.id)) user.is_active = active;
    });
    selectedIds.value = [];
  } catch (e) {
    await showAlert(errMsg(e));
  } finally {
    batchBusy.value = false;
  }
}

async function batchDelete() {
  const ids = [...selectedIds.value];
  if (!ids.length || batchBusy.value) return;
  if (!await showConfirm(
    `删除选中的 ${ids.length} 个用户？该操作不能撤销。`,
    { title: "批量删除用户", confirmText: "删除", danger: true },
  )) return;
  batchBusy.value = true;
  try {
    await api.usersDelete(ids);
    selectedIds.value = [];
    await load();
  } catch (e) {
    await showAlert(errMsg(e));
  } finally {
    batchBusy.value = false;
  }
}
</script>

<template>
  <div>
    <div class="bar">
      <h2 class="page-title">用户管理</h2>
      <MiuixButton type="primary" @click="showCreate = true">新建用户</MiuixButton>
    </div>
    <div v-if="error">{{ error }}</div>

    <BatchActionBar
      :selected-count="selectedIds.length"
      :total-count="visibleIds.length"
      :all-selected="allVisibleSelected"
      :busy="batchBusy"
      @toggle-all="toggleAllVisible"
      @clear="selectedIds = []"
      @enable="batchSetActive(true)"
      @disable="batchSetActive(false)"
      @delete="batchDelete"
    />

    <MiuixCard :show-indication="false" class="tbl-card">
      <table class="md-table">
        <thead>
          <tr>
            <th class="select-cell"><MiuixCheckbox :model-value="allVisibleSelected" aria-label="全选当前用户" @update:model-value="toggleAllVisible" /></th>
            <th>ID</th><th>用户名</th><th>昵称</th><th>状态</th>
            <th>超级</th><th>权限组</th><th>最近登录</th><th></th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="u in users" :key="u.id">
            <td class="select-cell"><MiuixCheckbox :model-value="selectedIds.includes(u.id)" :aria-label="`选择用户 ${u.username}`" @update:model-value="toggleSelected(u.id)" /></td>
            <td>{{ u.id }}</td>
            <td>{{ u.username }}</td>
            <td>{{ u.display_name }}</td>
            <td>
              <MiuixSwitch
                :model-value="u.is_active"
                @update:model-value="toggleActive(u)"
              />
            </td>
            <td>
              <MiuixSwitch
                :model-value="u.is_superuser"
                @update:model-value="toggleSuper(u)"
              />
            </td>
            <td>
              <span
                v-for="rid in u.role_ids"
                :key="rid"
                class="role-chip"
                title="点击移除"
                @click="toggleRole(u, rid)"
              >{{ roleName(rid) }} ×</span>
              <select
                class="role-add"
                value=""
                @change="
                  ($event.target as HTMLSelectElement).value &&
                    toggleRoleAdd(u, +($event.target as HTMLSelectElement).value);
                  ($event.target as HTMLSelectElement).value = ''
                "
              >
                <option value="" disabled>+ 添加…</option>
                <option
                  v-for="r in roles.filter((r) => !u.role_ids.includes(r.id))"
                  :key="r.id"
                  :value="r.id"
                >{{ r.name }}</option>
              </select>
            </td>
            <td>{{ u.last_login_at?.slice(0, 19).replace("T", " ") ?? "—" }}</td>
            <td class="ops">
              <button class="linkbtn" @click="resetTarget = u; resetPw = ''">重置密码</button>
              <button
                class="linkbtn danger"
                @click="removeUser(u)"
              >删除</button>
            </td>
          </tr>
        </tbody>
      </table>
    </MiuixCard>

    <!-- create -->
    <MiuixDialog v-model="showCreate" title="新建用户">
      <div class="dlg">
        <MiuixInput v-model="cName" label="用户名" single-line />
        <MiuixInput v-model="cDisplay" label="昵称（可选）" single-line />
        <input v-model="cPw" class="pw-input" placeholder="初始密码（≥6 位）" />
        <div class="roles-pick">
          <label
            v-for="r in roles"
            :key="r.id"
            class="pick"
          >
            <MiuixCheckbox
              :model-value="cRoleIds.includes(r.id)"
              @update:model-value="
                () => {
                  const i = cRoleIds.indexOf(r.id);
                  i >= 0 ? cRoleIds.splice(i, 1) : cRoleIds.push(r.id);
                }
              "
            />
            {{ r.name }}
          </label>
        </div>
        <div v-if="createErr" class="err">{{ createErr }}</div>
      </div>
      <div class="dlg-actions">
        <MiuixButton @click="showCreate = false">取消</MiuixButton>
        <MiuixButton type="primary" @click="doCreate">创建</MiuixButton>
      </div>
    </MiuixDialog>

    <!-- reset password -->
    <MiuixDialog
      :model-value="!!resetTarget"
      :title="`重置 ${resetTarget?.username ?? ''} 的密码`"
      @update:model-value="(v: boolean) => { if (!v) resetTarget = null }"
    >
      <div class="dlg">
        <input v-model="resetPw" class="pw-input" placeholder="新密码（≥6 位）" />
        <div v-if="resetErr" class="err">{{ resetErr }}</div>
      </div>
      <div class="dlg-actions">
        <MiuixButton @click="resetTarget = null">取消</MiuixButton>
        <MiuixButton type="primary" @click="doReset">确认重置</MiuixButton>
      </div>
    </MiuixDialog>
  </div>
</template>

<style scoped>
.tbl-card {
  --app-card-pad: 4px 10px;
}
.select-cell {
  width: 38px;
  text-align: center;
}
.role-chip {
  display: inline-block;
  margin-right: 6px;
  padding: 2px 10px;
  background: var(--m-color-secondary-container);
  color: var(--m-color-on-secondary-container);
  border-radius: 999px;
  font-size: 12px;
  cursor: pointer;
}
.role-add {
  border: 1px dashed var(--m-color-outline);
  background: transparent;
  color: var(--m-color-on-surface-secondary);
  border-radius: 8px;
  font-size: 12px;
  padding: 2px 6px;
}
.ops {
  white-space: nowrap;
}
.dlg {
  display: flex;
  flex-direction: column;
  gap: 12px;
  min-width: 280px;
}
.roles-pick {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
}
.pick {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  font-size: 13px;
}
</style>
