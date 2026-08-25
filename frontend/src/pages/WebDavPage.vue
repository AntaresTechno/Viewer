<script setup lang="ts">
import { onMounted, ref } from "vue";
import { MiuixButton, MiuixCard, MiuixInput } from "miuix-vue";
import PasswordField from "@/components/PasswordField.vue";
import {
  api,
  errMsg,
  type WebDavBackupItem,
  type WebDavConfigInfo,
} from "@/api/client";

const loading = ref(true);
const saving = ref(false);
const testing = ref(false);
const backingUp = ref(false);
const listing = ref(false);

const url = ref("");
const username = ref("");
/* 留空表示不修改已保存的密码；后端以空串跳过、"-clear" 显式清除 */
const password = ref("");
const directory = ref("AntaresViewer");
const autoBackup = ref(false);

const info = ref<WebDavConfigInfo | null>(null);
const msg = ref("");
const err = ref("");

const backups = ref<WebDavBackupItem[]>([]);
const restoring = ref("");

onMounted(load);

async function load() {
  loading.value = true;
  try {
    const c = await api.webdavGetConfig();
    info.value = c;
    url.value = c.url;
    username.value = c.username;
    directory.value = c.directory || "AntaresViewer";
    autoBackup.value = c.autoBackup;
    password.value = "";
    void listBackups();
  } catch (e) {
    err.value = errMsg(e);
  } finally {
    loading.value = false;
  }
}

async function save() {
  msg.value = "";
  err.value = "";
  saving.value = true;
  try {
    const r = await api.webdavSaveConfig({
      url: url.value.trim(),
      username: username.value.trim(),
      password: password.value || undefined,
      directory: directory.value.trim(),
      autoBackup: autoBackup.value,
    });
    info.value = r;
    password.value = "";
    msg.value = "配置已保存";
  } catch (e) {
    err.value = errMsg(e);
  } finally {
    saving.value = false;
  }
}

async function testConn() {
  msg.value = "";
  err.value = "";
  // 先保存再测试，保证测的是当前表单内容
  await save();
  if (err.value) return;
  testing.value = true;
  try {
    await api.webdavTest();
    msg.value = "连接成功 ✓";
  } catch (e) {
    err.value = errMsg(e);
  } finally {
    testing.value = false;
  }
}

async function backupNow() {
  msg.value = "";
  err.value = "";
  backingUp.value = true;
  try {
    const r = await api.webdavBackup();
    msg.value = `已备份 ${r.file}（书架 ${r.shelf} · 进度 ${r.progress} · 统计 ${r.readingStats}）`;
    const c = await api.webdavGetConfig();
    info.value = c;
    void listBackups();
  } catch (e) {
    err.value = errMsg(e);
  } finally {
    backingUp.value = false;
  }
}

async function listBackups() {
  listing.value = true;
  try {
    backups.value = await api.webdavBackups();
  } catch {
    backups.value = [];
  } finally {
    listing.value = false;
  }
}

async function restore(name: string) {
  if (!confirm(`从「${name}」恢复？书架/进度将按更新时间合并，不会删除本地已有条目。`)) return;
  err.value = "";
  msg.value = "";
  restoring.value = name;
  try {
    const r = await api.webdavRestore(name);
    msg.value =
      `恢复完成：书架 +${r.shelfAdded}/更新 ${r.shelfUpdated} · ` +
      `进度 ${r.progressUpdated} · 时长 ${r.statsMerged}`;
  } catch (e) {
    err.value = errMsg(e);
  } finally {
    restoring.value = "";
  }
}

async function removeBackup(name: string) {
  if (!confirm(`删除远端备份「${name}」？此操作不可恢复。`)) return;
  try {
    await api.webdavDeleteBackup(name);
    await listBackups();
  } catch (e) {
    err.value = errMsg(e);
  }
}

function fmtSize(n: number): string {
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`;
  return `${(n / 1024 / 1024).toFixed(1)} MB`;
}
function fmtModified(s: string): string {
  const t = new Date(s).getTime();
  return Number.isFinite(t) && t > 0 ? new Date(t).toLocaleString() : s;
}
</script>

<template>
  <div class="page-webdav">
    <div class="page-head">
      <div>
        <h2 class="page-title">WebDAV 备份</h2>
        <p class="page-sub">把书架 / 阅读进度 / 阅读统计备份到你的网盘，可随时恢复</p>
      </div>
    </div>

    <!-- 服务器配置 -->
    <MiuixCard class="card" :show-indication="false">
      <h3>服务器</h3>
      <div class="col">
        <label>服务器地址
          <MiuixInput v-model="url" single-line placeholder="https://dav.jianguoyun.com/dav/" />
        </label>
        <label>账号
          <MiuixInput v-model="username" single-line placeholder="WebDAV 用户名" />
        </label>
        <PasswordField
          v-model="password"
          :label="info?.hasPassword ? '密码（已保存，留空则不修改）' : '密码'"
        />
        <label>远端目录
          <MiuixInput v-model="directory" single-line placeholder="AntaresViewer" />
        </label>
        <label class="check-row">
          <input v-model="autoBackup" type="checkbox">
          <span>每天自动备份一次（在每日目录刷新之后）</span>
        </label>
      </div>
      <div class="row-actions">
        <MiuixButton type="primary" :disabled="saving" @click="save">保存配置</MiuixButton>
        <MiuixButton :disabled="testing || saving" @click="testConn">测试连接</MiuixButton>
        <span v-if="msg" class="ok">{{ msg }}</span>
        <span v-if="err" class="err">{{ err }}</span>
      </div>
      <p class="hint">
        兼容坚果云 / InfiniCloud / Alist / Nextcloud 等 WebDAV 服务。
        密码仅混淆存储在本站数据库，接口不会回显。
      </p>
    </MiuixCard>

    <!-- 备份状态 -->
    <MiuixCard v-if="info?.lastBackupAt" class="card" :show-indication="false">
      <h3>上次备份</h3>
      <p class="sub">
        {{ new Date(info.lastBackupAt).toLocaleString() }}
        <template v-if="info.lastBackupFile"> · {{ info.lastBackupFile }}</template>
      </p>
    </MiuixCard>

    <!-- 备份文件 -->
    <MiuixCard class="card" :show-indication="false">
      <h3>云端备份</h3>
      <div class="row-actions" style="margin-top: 0">
        <MiuixButton type="primary" :disabled="backingUp" @click="backupNow">
          {{ backingUp ? "备份中…" : "立即备份" }}
        </MiuixButton>
        <MiuixButton :disabled="listing" @click="listBackups">刷新列表</MiuixButton>
      </div>

      <div v-if="listing" class="sub" style="margin-top: 12px">加载中…</div>
      <p v-else-if="!backups.length" class="sub" style="margin-top: 12px">
        还没有云端备份文件。
      </p>
      <ul v-else class="bk-list">
        <li v-for="b in backups" :key="b.href" class="bk-row">
          <span class="bk-name">{{ b.name }}</span>
          <span class="bk-meta">{{ fmtSize(b.size) }} · {{ fmtModified(b.modified) }}</span>
          <span class="bk-acts">
            <button
              type="button"
              class="linkbtn"
              :disabled="restoring === b.name"
              @click="restore(b.name)"
            >{{ restoring === b.name ? "恢复中…" : "恢复" }}</button>
            <button
              type="button"
              class="linkbtn danger"
              @click="removeBackup(b.name)"
            >删除</button>
          </span>
        </li>
      </ul>
    </MiuixCard>
  </div>
</template>

<style scoped>
.page-webdav {
  max-width: 720px;
}
.card {
  --app-card-pad: 22px;
  margin-bottom: 14px;
}
h3 {
  margin: 0 0 12px;
  font-size: 15px;
}
.col {
  display: flex;
  flex-direction: column;
  gap: 12px;
  max-width: 420px;
}
.col label {
  font-size: 13px;
  color: var(--m-color-on-surface-secondary);
}
.check-row {
  display: flex;
  align-items: center;
  gap: 8px;
}
.row-actions {
  margin-top: 14px;
  display: flex;
  align-items: center;
  gap: 12px;
  flex-wrap: wrap;
}
.ok {
  color: var(--m-color-primary);
  font-size: 13px;
}
.err {
  color: var(--m-color-error);
  font-size: 13px;
}
.hint {
  margin-top: 10px;
  font-size: 12px;
  color: var(--m-color-on-background-variant, var(--m-color-on-surface-secondary));
}
.sub {
  color: var(--m-color-on-surface-secondary);
  font-size: 13px;
}
.bk-list {
  list-style: none;
  margin: 12px 0 0;
  padding: 0;
  display: flex;
  flex-direction: column;
  gap: 6px;
}
.bk-row {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 9px 12px;
  border-radius: 12px;
  background: color-mix(in srgb, var(--m-color-on-surface) 4%, transparent);
  font-size: 13px;
}
.bk-name {
  font-weight: 600;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.bk-meta {
  color: var(--m-color-on-surface-secondary);
  font-size: 12px;
  flex: 1;
}
.bk-acts {
  display: inline-flex;
  gap: 10px;
  flex: none;
}
@media (max-width: 560px) {
  .bk-row {
    flex-wrap: wrap;
  }
}
</style>
