<script setup lang="ts">
import { onMounted, reactive, ref } from "vue";
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
const connEnabled = ref(true); // 「连接网盘」启用总开关（持久化，legado 同步依赖它）

/* 各卡片展开状态（默认收起）。标题栏点击即可展开/收起。 */
const open = reactive({ conn: false, cloud: false, legado: false, server: false });
function toggleOpen(k: keyof typeof open) {
  open[k] = !open[k];
}

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

/* ---- legado 备份同步（外部 WebDAV 服务器）---- */
const legadoEnabled = ref(false);
const legadoDir = ref("legado");
const legadoLastSyncAt = ref<string | null>(null);
const legadoSaving = ref(false);
const legadoSyncing = ref("");
const legadoImporting = ref(false);

async function saveLegado() {
  err.value = "";
  msg.value = "";
  legadoSaving.value = true;
  try {
    const r = await api.webdavLegadoSave({
      enabled: legadoEnabled.value,
      directory: legadoDir.value.trim() || "legado",
    });
    info.value = r;
    legadoDir.value = r.legadoDirectory;
    legadoLastSyncAt.value = r.legadoLastSyncAt;
    msg.value = legadoEnabled.value
      ? "legado 同步已开启，请在 App 里也用同一目录"
      : "legado 同步已关闭";
  } catch (e) {
    err.value = errMsg(e);
  } finally {
    legadoSaving.value = false;
  }
}

async function legadoSync(direction: "both" | "pull" | "push", label: string) {
  if (!legadoEnabled.value) {
    err.value = "请先开启 legado 同步";
    return;
  }
  err.value = "";
  msg.value = "";
  legadoSyncing.value = label;
  try {
    const r = await api.webdavLegadoSync(direction);
    legadoLastSyncAt.value = r.legadoLastSyncAt;
    msg.value =
      `同步完成：拉取 ${r.pulled} · 推送 ${r.pushed} · ` +
      `合并 ${r.progressUpdated} · 待匹配 ${r.pendingMatch}`;
  } catch (e) {
    err.value = errMsg(e);
  } finally {
    legadoSyncing.value = "";
  }
}

async function legadoImport() {
  if (!legadoEnabled.value) {
    err.value = "请先开启 legado 同步";
    return;
  }
  if (!confirm("从最新 legado 全量备份 backup*.zip 导入书架与进度？已合并，不删本地。")) return;
  err.value = "";
  msg.value = "";
  legadoImporting.value = true;
  try {
    const r = await api.webdavLegadoImport();
    msg.value =
      `导入完成（${r.backup}）：新增 ${r.addedShelf} · 更新 ${r.updatedShelf} · 进度 ${r.progressUpdated}`;
  } catch (e) {
    err.value = errMsg(e);
  } finally {
    legadoImporting.value = false;
  }
}

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
    msg.value = v ? "服务端已开启" : "服务端已关闭";
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
    !confirm("重新生成后旧密码立即失效，所有已配置的设备需更新密码。继续？")
  ) {
    return;
  }
  serverToggling.value = true;
  try {
    secretOnce.value = await api.webdavResetServerSecret();
    msg.value = "密码已生成，请立即复制保存";
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
    connEnabled.value = c.enabled;
    legadoEnabled.value = c.legadoEnabled;
    legadoDir.value = c.legadoDirectory || "legado";
    legadoLastSyncAt.value = c.legadoLastSyncAt;
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
      enabled: connEnabled.value,
    });
    info.value = r;
    password.value = "";
    msg.value = "已保存";
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
  if (!confirm(`从「${name}」恢复？按更新时间合并，不删本地已有条目。`)) return;
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
        <h2 class="page-title">WebDAV</h2>
        <p class="page-sub">本站数据备份到网盘，或与阅读(legado)互同步进度</p>
        <div class="status">
          <span v-if="msg" class="ok">{{ msg }}</span>
          <span v-if="err" class="err">{{ err }}</span>
        </div>
      </div>
    </div>

    <!-- 客户端区：本站作为 WebDAV 客户端，连网盘备份 / 同步 -->
    <div class="zone">
      <h2 class="zone-title">客户端 · 网盘备份 / 同步</h2>

      <MiuixCard class="card" :show-indication="false">
        <div class="srv-head">
          <button type="button" class="shead" :class="{ open: open.conn }" @click="toggleOpen('conn')">
            <span>连接网盘</span>
            <span class="caret" aria-hidden="true"></span>
          </button>
          <MiuixSwitch
            :model-value="connEnabled"
            :disabled="saving"
            @update:model-value="(v: boolean) => { connEnabled = v; void save(); }"
          />
        </div>
        <div class="sbody" :class="{ collapsed: !open.conn }">
          <div class="sbody-inner">
            <div class="flow">本站书架 · 进度 · 统计 <b>---></b> 网盘</div>
            <div class="col">
              <label>服务器地址
                <MiuixInput v-model="url" single-line placeholder="https://dav.jianguoyun.com/dav/" />
              </label>
              <label>账号
                <MiuixInput v-model="username" single-line placeholder="WebDAV 用户名" />
              </label>
              <PasswordField
                v-model="password"
                :label="info?.hasPassword ? '密码（留空不变）' : '密码'"
              />
              <label>远端目录
                <MiuixInput v-model="directory" single-line placeholder="AntaresViewer" />
              </label>
              <label class="check-row">
                <input v-model="autoBackup" type="checkbox">
                <span>每日自动备份</span>
              </label>
            </div>
            <div class="row-actions">
              <MiuixButton type="primary" :disabled="saving" @click="save">保存</MiuixButton>
              <MiuixButton :disabled="testing || saving" @click="testConn">测试</MiuixButton>
            </div>
            <p class="hint">兼容坚果云 / Alist / InfiniCloud / Nextcloud。密码不对外回显。</p>
          </div>
        </div>
      </MiuixCard>

      <MiuixCard class="card" :show-indication="false">
        <button type="button" class="shead" :class="{ open: open.cloud }" @click="toggleOpen('cloud')">
          <span>云端备份</span>
          <span class="caret" aria-hidden="true"></span>
        </button>
        <div class="sbody" :class="{ collapsed: !open.cloud }">
          <div class="sbody-inner">
            <div class="flow">本站 <b>---></b> 网盘（备份） · 网盘 <b>---></b> 本站（恢复）</div>
            <div class="row-actions">
              <MiuixButton type="primary" :disabled="backingUp" @click="backupNow">
                {{ backingUp ? "备份中…" : "立即备份" }}
              </MiuixButton>
              <MiuixButton :disabled="listing" @click="listBackups">刷新</MiuixButton>
              <span v-if="info?.lastBackupAt" class="sub">上次 {{ new Date(info.lastBackupAt).toLocaleString() }}</span>
            </div>
            <div v-if="listing" class="sub">加载中…</div>
            <p v-else-if="!backups.length" class="sub">无云端备份</p>
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
          </div>
        </div>
      </MiuixCard>

      <MiuixCard class="card" :show-indication="false">
        <div class="srv-head">
          <button type="button" class="shead" :class="{ open: open.legado }" @click="toggleOpen('legado')">
            <span>legado 进度同步</span>
            <span class="caret" aria-hidden="true"></span>
          </button>
          <MiuixSwitch
            :model-value="legadoEnabled"
            :disabled="legadoSaving || !connEnabled"
            @update:model-value="(v: boolean) => { legadoEnabled = v; void saveLegado(); }"
          />
        </div>
        <p v-if="!connEnabled" class="hint warn" style="margin: 10px 0 0">
          legado 同步需先打开「连接网盘」开关方可启用。
        </p>
        <div class="sbody" :class="{ collapsed: !open.legado }">
          <div class="sbody-inner">
            <div class="flow">本站 <b><--></b> 网盘{bookProgress} <b><--></b> legado</div>
            <div class="col" style="margin-top: 12px">
              <label>legado 目录（与 App 内一致）
                <MiuixInput v-model="legadoDir" single-line placeholder="legado" />
              </label>
            </div>
            <div class="row-actions">
              <MiuixButton type="primary" :disabled="legadoSyncing !== '' || legadoSaving" @click="saveLegado">
                {{ legadoSaving ? "保存中…" : "保存" }}
              </MiuixButton>
              <MiuixButton :disabled="legadoSyncing !== ''" @click="legadoSync('both', 'both')">
                {{ legadoSyncing === 'both' ? "同步中…" : "双向同步" }}
              </MiuixButton>
              <MiuixButton :disabled="legadoSyncing !== ''" @click="legadoSync('push', 'push')">
                {{ legadoSyncing === 'push' ? "…" : "推送" }}
              </MiuixButton>
              <MiuixButton :disabled="legadoSyncing !== ''" @click="legadoSync('pull', 'pull')">
                {{ legadoSyncing === 'pull' ? "…" : "拉取" }}
              </MiuixButton>
              <MiuixButton :disabled="legadoImporting" @click="legadoImport">
                {{ legadoImporting ? "导入中…" : "导入书架" }}
              </MiuixButton>
            </div>
            <p v-if="legadoLastSyncAt" class="sub">最近同步 {{ new Date(legadoLastSyncAt).toLocaleString() }}</p>
            <p class="hint">App 的 WebDAV「目录」填同一值、开阅读进度同步即可。</p>
          </div>
        </div>
      </MiuixCard>

      <p class="risk">风险：恢复 / 同步按「更新时间晚的覆盖早的」合并，旧进度会被覆盖且无法找回 —— 先「立即备份」再操作。</p>
    </div>

    <!-- 服务端区：本站作为 WebDAV 服务端，供 legado 接入 -->
    <div class="zone">
      <h2 class="zone-title">服务端 · 供 legado 接入</h2>

      <MiuixCard class="card" :show-indication="false">
        <div class="srv-head">
          <button type="button" class="shead" :class="{ open: open.server }" @click="toggleOpen('server')">
            <span>同步服务端</span>
            <span class="caret" aria-hidden="true"></span>
          </button>
          <MiuixSwitch
            :model-value="server?.enabled ?? false"
            :disabled="serverToggling"
            @update:model-value="(v: boolean) => toggleServer(v)"
          />
        </div>

        <div class="sbody" :class="{ collapsed: !open.server }">
          <div class="sbody-inner">
            <template v-if="server">
              <div class="flow">legado <b>---></b> 本站 /dav{bookProgress} <b>---></b> 本站进度</div>
              <div class="kv">
                <span class="k">地址</span>
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
                <span class="k">密码</span>
                <span v-if="secretOnce" class="v mono secret">{{ secretOnce }}</span>
                <span v-else-if="server.hasSecret" class="v">已设置</span>
                <span v-else class="v warn">未生成</span>
                <button type="button" class="linkbtn" :disabled="serverToggling" @click="genSecret">
                  {{ server.hasSecret ? "重生成" : "生成密码" }}
                </button>
                <button v-if="secretOnce" type="button" class="linkbtn" @click="copyText(secretOnce, 'secret')">
                  {{ copied === "secret" ? "已复制" : "复制" }}
                </button>
              </div>
              <p v-if="server.lastSyncAt" class="sub">最近同步 {{ new Date(server.lastSyncAt).toLocaleString() }}</p>

              <div v-if="pending.length" class="pending">
                <p class="sub" style="margin: 0 0 6px">待匹配 {{ pending.length }}（已同步进度但书架无此书）：</p>
                <ul class="pending-list">
                  <li v-for="p in pending" :key="p.file">
                    <span>{{ p.name }}<template v-if="p.author"> · {{ p.author }}</template></span>
                    <span class="pm-meta">第 {{ p.chapterIndex + 1 }} 章</span>
                  </li>
                </ul>
              </div>

              <p class="hint">legado「我的 → 备份与恢复 → WebDAV」填上面的 地址/账号/密码，开「阅读进度同步」。</p>
              <p class="risk">风险：此密码即访问凭据，泄露可被别人读取你的进度；反代部署请手填地址。</p>
            </template>
          </div>
        </div>
      </MiuixCard>
    </div>
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
.shead {
  display: flex;
  align-items: center;
  gap: 8px;
  width: 100%;
  padding: 2px 0;
  background: none;
  border: none;
  cursor: pointer;
  font-size: 15px;
  font-weight: 700;
  color: inherit;
  text-align: left;
  font-family: inherit;
}
.srv-head .shead {
  flex: 1;
}
.caret {
  flex: none;
  width: 0;
  height: 0;
  margin-top: 3px;
  border-left: 5px solid transparent;
  border-right: 5px solid transparent;
  border-top: 6px solid var(--m-color-on-surface-secondary);
  transition: transform 0.25s ease;
}
.shead.open .caret {
  transform: rotate(180deg);
}
/* 展开/收起动画：grid 行高从 0fr ⇄ 1fr + 淡入淡出 */
.sbody {
  display: grid;
  grid-template-rows: 1fr;
  transition: grid-template-rows 0.3s ease, opacity 0.22s ease;
}
.sbody.collapsed {
  grid-template-rows: 0fr;
  opacity: 0;
}
.sbody-inner {
  min-height: 0;
  overflow: hidden;
  padding-top: 14px;
}
.zone-title {
  margin: 22px 0 10px;
  font-size: 13px;
  color: var(--m-color-on-surface-secondary);
  letter-spacing: 0.04em;
}
.page-head h2 {
  font-size: 20px;
  margin: 0;
}
.zone-title::before {
  content: "";
  display: inline-block;
  width: 3px;
  height: 11px;
  margin-right: 8px;
  vertical-align: -1px;
  border-radius: 2px;
  background: var(--m-color-primary);
}
.status {
  margin-top: 8px;
  display: flex;
  gap: 10px;
  flex-wrap: wrap;
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
.flow {
  display: flex;
  align-items: center;
  gap: 6px;
  flex-wrap: wrap;
  margin: 2px 0 14px;
  padding: 8px 12px;
  border-radius: 10px;
  background: color-mix(in srgb, var(--m-color-primary) 8%, transparent);
  font-size: 13px;
  color: var(--m-color-on-surface);
}
.flow b {
  color: var(--m-color-primary);
  font-family: ui-monospace, SFMono-Regular, Consolas, monospace;
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
.hint.warn {
  color: var(--m-color-error);
}
.risk {
  margin: 12px 0 0;
  padding: 8px 12px;
  border-radius: 10px;
  background: color-mix(in srgb, var(--m-color-error) 12%, transparent);
  color: var(--m-color-error);
  font-size: 12.5px;
  line-height: 1.5;
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
  width: 44px;
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
.linkbtn {
  background: none;
  border: none;
  color: var(--m-color-primary);
  font-size: 13px;
  cursor: pointer;
  padding: 0;
}
.linkbtn:disabled {
  opacity: 0.5;
  cursor: default;
}
.linkbtn.danger {
  color: var(--m-color-error);
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
  .zone-title {
    margin-top: 18px;
  }
}
</style>