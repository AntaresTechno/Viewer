<script setup lang="ts">
import { onMounted, ref } from "vue";
import { MiuixButton, MiuixCard, MiuixInput, MiuixSwitch } from "miuix-vue";
import PasswordField from "@/components/PasswordField.vue";
import {
  api,
  errMsg,
  type WebDavBackupItem,
  type WebDavConfigInfo,
  type WebDavPendingItem,
  type WebDavServerInfo,
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

/* ---- WebDAV 服务端（legado 进度同步）---- */
const server = ref<WebDavServerInfo | null>(null);
const serverToggling = ref(false);
const secretOnce = ref(""); // 生成的访问密码，仅展示一次
const copied = ref("");
const pending = ref<WebDavPendingItem[]>([]);

async function loadPending() {
  try {
    pending.value = (await api.webdavServerPending()).items;
  } catch {
    pending.value = [];
  }
}

onMounted(() => {
  void load();
  void loadServer();
});

async function loadServer() {
  try {
    const s = await api.webdavGetServer();
    // 旧版后端会把未知 /api 路径回退成 index.html（HTML 字符串）
    if (!s || typeof s !== "object" || !("enabled" in s)) {
      throw new Error("后端未提供同步服务端接口，请重启后端后刷新页面");
    }
    server.value = s;
    if (s.enabled) void loadPending();
  } catch (e) {
    err.value = errMsg(e);
  }
}

async function toggleServer(v: boolean) {
  if (!server.value) return;
  if (v && !server.value.hasSecret) {
    // 首次开启：直接生成访问密码
    await genSecret();
    return;
  }
  serverToggling.value = true;
  err.value = "";
  try {
    await api.webdavSaveServer(v);
    msg.value = v ? "同步服务端已开启" : "同步服务端已关闭";
    await loadServer();
  } catch (e) {
    err.value = errMsg(e);
  } finally {
    serverToggling.value = false;
  }
}

async function genSecret() {
  err.value = "";
  if (
    server.value?.hasSecret &&
    !confirm("重新生成后旧密码立即失效，所有已配置的设备需要更新密码。继续？")
  ) {
    return;
  }
  serverToggling.value = true;
  try {
    secretOnce.value = await api.webdavResetServerSecret();
    msg.value = "访问密码已生成，请立即复制保存";
    await loadServer();
    void loadPending();
  } catch (e) {
    err.value = errMsg(e);
  } finally {
    serverToggling.value = false;
  }
}

async function copyText(text: string, tag: string) {
  try {
    await navigator.clipboard.writeText(text);
    copied.value = tag;
    setTimeout(() => (copied.value = ""), 1500);
  } catch {
    /* 剪贴板不可用时忽略 */
  }
}

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

    <!-- 同步服务端（legado 兼容） -->
    <MiuixCard class="card" :show-indication="false">
      <div class="srv-head">
        <h3>同步服务端 · 阅读(legado) 进度同步</h3>
        <MiuixSwitch
          :model-value="server?.enabled ?? false"
          :disabled="serverToggling"
          @update:model-value="(v: boolean) => toggleServer(v)"
        />
      </div>

      <template v-if="server">
        <div class="kv">
          <span class="k">服务器地址</span>
          <span class="v mono">{{ server.url }}</span>
          <button type="button" class="linkbtn" @click="copyText(server.url, 'url')">
            {{ copied === "url" ? "已复制" : "复制" }}
          </button>
        </div>
        <div class="kv">
          <span class="k">账号</span>
          <span class="v mono">{{ server.account }}</span>
        </div>
        <div class="kv">
          <span class="k">访问密码</span>
          <span v-if="secretOnce" class="v mono secret">{{ secretOnce }}</span>
          <span v-else-if="server.hasSecret" class="v">已设置（不显示）</span>
          <span v-else class="v warn">未生成 — 开启开关即可自动生成</span>
          <button
            type="button"
            class="linkbtn"
            :disabled="serverToggling"
            @click="genSecret"
          >{{ server.hasSecret ? "重新生成" : "生成密码" }}</button>
          <button
            v-if="secretOnce"
            type="button"
            class="linkbtn"
            @click="copyText(secretOnce, 'secret')"
          >{{ copied === "secret" ? "已复制" : "复制" }}</button>
        </div>
        <p v-if="server.lastSyncAt" class="sub" style="margin-top: 8px">
          最近同步：{{ new Date(server.lastSyncAt).toLocaleString() }}
        </p>

        <!-- 双端同步说明 + 待匹配列表 -->
        <div class="sync-flow">
          <span>legado 阅读 → 上传进度 → 自动入库本站书架</span>
          <span>本站阅读 → 进度更新 → legado 同步拉取</span>
        </div>
        <div v-if="pending.length" class="pending">
          <p class="sub" style="margin: 0 0 6px">
            待匹配（{{ pending.length }}）— 已同步进度但书架暂无此书，
            后台会自动用书源搜索入库；也可手动在本站搜索加入同名书籍，进度将自动关联：
          </p>
          <ul class="pending-list">
            <li v-for="p in pending" :key="p.file">
              <span>{{ p.name }}<template v-if="p.author"> · {{ p.author }}</template></span>
              <span class="pm-meta">读到第 {{ p.chapterIndex + 1 }} 章</span>
            </li>
          </ul>
        </div>

        <p class="hint">
          在阅读(legado) App 的「我的 → 备份与恢复 → WebDAV」中填入上方的
          服务器地址 / 账号 / 访问密码，并开启「阅读进度同步」即可实现双端同步：
          legado 上读的书（含新书）会按 书名+作者 匹配书源后自动加入本站书架并带入进度；
          本站的阅读进度也会合成为同名进度文件供 legado 拉取。仅同步阅读进度资源。
          若部署在反向代理之后，请按实际访问地址手工填写。
        </p>
      </template>
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
.srv-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
}
.srv-head h3 {
  margin: 0;
}
.kv {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 7px 0;
  font-size: 13px;
  flex-wrap: wrap;
}
.kv .k {
  color: var(--m-color-on-surface-secondary);
  flex: none;
  width: 72px;
}
.kv .v {
  min-width: 0;
  overflow-wrap: anywhere;
}
.kv .mono {
  font-family: ui-monospace, SFMono-Regular, Consolas, monospace;
  font-size: 12.5px;
}
.kv .secret {
  word-break: break-all;
}
.kv .warn {
  color: var(--m-color-error);
}
.sync-flow {
  margin-top: 12px;
  display: flex;
  flex-direction: column;
  gap: 4px;
  font-size: 12.5px;
  color: var(--m-color-primary);
}
.pending {
  margin-top: 12px;
  padding: 10px 12px;
  border-radius: 12px;
  background: color-mix(in srgb, var(--m-color-on-surface) 5%, transparent);
}
.pending-list {
  list-style: none;
  margin: 0;
  padding: 0;
  display: flex;
  flex-direction: column;
  gap: 4px;
  max-height: 180px;
  overflow: auto;
}
.pending-list li {
  display: flex;
  justify-content: space-between;
  gap: 10px;
  font-size: 13px;
}
.pm-meta {
  color: var(--m-color-on-surface-secondary);
  font-size: 12px;
  flex: none;
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
