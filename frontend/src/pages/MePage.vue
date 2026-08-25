<script setup lang="ts">
import { onMounted, ref } from "vue";
import { useRouter } from "vue-router";
import {
  MiuixButton,
  MiuixCard,
  MiuixInput,
  MiuixText,
  MiuixSlider,
} from "miuix-vue";
import PasswordField from "@/components/PasswordField.vue";
import AppearancePanel from "@/components/AppearancePanel.vue";
import { useAuth } from "@/stores/auth";
import { api, errMsg } from "@/api/client";

const auth = useAuth();
const $router = useRouter();

const displayName = ref(auth.user?.display_name ?? "");
const email = ref(auth.user?.email ?? "");
const bio = ref(auth.user?.bio ?? "");
const hue = ref(auth.user?.avatar_hue ?? 217);
const profileMsg = ref("");

const oldPw = ref("");
const newPw = ref("");
const pwMsg = ref("");
const pwError = ref("");

onMounted(() => void auth.refreshMe());

async function saveProfile() {
  profileMsg.value = "";
  try {
    await api.updateProfile({
      display_name: displayName.value,
      email: email.value,
      bio: bio.value,
      avatar_hue: Math.round(hue.value),
    });
    await auth.refreshMe();
    profileMsg.value = "已保存";
  } catch (e) {
    profileMsg.value = errMsg(e);
  }
}

async function savePassword() {
  pwError.value = "";
  pwMsg.value = "";
  if (newPw.value.length < 6) {
    pwError.value = "新密码至少 6 位";
    return;
  }
  try {
    await api.changePassword(oldPw.value, newPw.value);
    oldPw.value = "";
    newPw.value = "";
    pwMsg.value = "密码已更新";
  } catch (e) {
    pwError.value = errMsg(e);
  }
}

function logout() {
  if (!confirm("确定退出登录？")) return;
  auth.logout();
  $router.push("/login");
}
</script>

<template>
  <div class="page-me">
    <div class="page-head">
      <div>
        <h2 class="page-title">我的</h2>
        <p class="page-sub">账户、外观与安全设置</p>
      </div>
    </div>

    <!-- 身份 -->
    <MiuixCard class="card" :show-indication="false">
      <div class="head">
        <div
          class="big-avatar"
          :style="{ background: `hsl(${auth.user?.avatar_hue ?? 217}, 45%, 60%)` }"
        >
          {{ auth.user?.display_name?.slice(0, 1)?.toUpperCase() }}
        </div>
        <div>
          <MiuixText type="title3">{{ auth.user?.display_name }}</MiuixText>
          <div class="sub">
            @{{ auth.user?.username }}
            <span v-if="auth.isSuperuser" class="tag">超级管理员</span>
          </div>
          <div v-if="auth.user?.created_at" class="joined">
            注册于 {{ auth.user.created_at.slice(0, 10) }} · 书架 {{ auth.user?.shelf_count ?? 0 }} 本
          </div>
        </div>
      </div>
    </MiuixCard>

    <!-- 资料 -->
    <MiuixCard class="card" :show-indication="false">
      <h3>资料</h3>
      <div class="grid">
        <label>昵称<MiuixInput v-model="displayName" single-line /></label>
        <label>邮箱<MiuixInput v-model="email" single-line /></label>
      </div>
      <label class="bio-label">个人简介</label>
      <textarea v-model="bio" rows="3" />
      <div class="hue-row">
        <span>头像色相</span>
        <MiuixSlider v-model="hue" :min="0" :max="360" style="flex:1" />
        <span class="mono">{{ Math.round(hue) }}°</span>
      </div>
      <div class="row-actions">
        <MiuixButton type="primary" @click="saveProfile">保存资料</MiuixButton>
        <span v-if="profileMsg" class="ok">{{ profileMsg }}</span>
      </div>
    </MiuixCard>

    <!-- 外观 -->
    <MiuixCard class="card" :show-indication="false">
      <h3>外观</h3>
      <AppearancePanel />
    </MiuixCard>

    <!-- 安全 -->
    <MiuixCard class="card" :show-indication="false">
      <h3>修改密码</h3>
      <div class="col">
        <PasswordField v-model="oldPw" label="当前密码" />
        <PasswordField v-model="newPw" label="新密码（至少 6 位）" />
        <div class="row-actions">
          <MiuixButton type="primary" @click="savePassword">更新密码</MiuixButton>
          <span v-if="pwMsg" class="ok">{{ pwMsg }}</span>
          <span v-if="pwError" class="err">{{ pwError }}</span>
        </div>
      </div>
    </MiuixCard>

    <!-- 更多 -->
    <MiuixCard
      v-if="auth.can('books.replace.read')"
      class="card link-card"
      :show-indication="false"
      @click="$router.push('/replace')"
    >
      <div class="link-row">
        <div>
          <h3 style="margin: 0">净化规则</h3>
          <p class="sub">替换/净化章节正文，支持导入 legado 替换规则</p>
        </div>
        <span class="arrow">›</span>
      </div>
    </MiuixCard>

    <!-- 正文净化插件 -->
    <MiuixCard
      v-if="auth.can('purify.read')"
      class="card link-card"
      :show-indication="false"
      @click="$router.push('/purify')"
    >
      <div class="link-row">
        <div>
          <h3 style="margin: 0">正文净化</h3>
          <p class="sub">规则包（预设 / 乌云净化类导入）· 净化缓存 · 试一试</p>
        </div>
        <span class="arrow">›</span>
      </div>
    </MiuixCard>

    <!-- 危险区：移动端底部标签栏没有退出入口，这里兜底所有端 -->
    <button type="button" class="logout-row" @click="logout">退出登录</button>
  </div>
</template>

<style scoped>
.page-me {
  max-width: 720px;
}
.card {
  /* 内边距经变量传入内层可见卡片（MiuixCard 结构） */
  --app-card-pad: 22px;
}
.link-card {
  cursor: pointer;
}
.link-row {
  display: flex;
  justify-content: space-between;
  align-items: center;
}
.arrow {
  font-size: 22px;
  color: var(--m-color-on-surface-secondary);
}
.head {
  display: flex;
  gap: 16px;
  align-items: center;
}
.big-avatar {
  width: 64px;
  height: 64px;
  border-radius: 50%;
  color: #fff;
  font-size: 26px;
  font-weight: 800;
  display: grid;
  place-items: center;
  flex: none;
}
.sub {
  color: var(--m-color-on-surface-secondary);
  font-size: 13px;
}
.tag {
  margin-left: 8px;
  background: var(--m-color-primary-container);
  color: var(--m-color-on-primary-container);
  padding: 2px 10px;
  border-radius: 999px;
  font-size: 11px;
}
.joined {
  margin-top: 4px;
  color: var(--m-color-on-background-variant);
  font-size: 12px;
}
h3 {
  margin: 14px 0 10px;
  font-size: 15px;
}
.card h3:first-of-type {
  margin-top: 0;
}
.grid {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 12px;
  font-size: 13px;
  color: var(--m-color-on-surface-secondary);
}
@media (max-width: 560px) {
  .grid {
    grid-template-columns: 1fr;
  }
}
.bio-label {
  display: block;
  margin-top: 10px;
  font-size: 13px;
  color: var(--m-color-on-surface-secondary);
}
textarea {
  margin-top: 6px;
}
.hue-row {
  display: flex;
  align-items: center;
  gap: 12px;
  margin-top: 12px;
  font-size: 13px;
  color: var(--m-color-on-surface-secondary);
}
.mono {
  width: 40px;
  text-align: right;
}
.row-actions {
  margin-top: 12px;
  display: flex;
  align-items: center;
  gap: 12px;
}
.col {
  display: flex;
  flex-direction: column;
  gap: 12px;
  max-width: 360px;
}

/* 退出登录：整行幽灵按钮，悬停/按压染错误色 */
.logout-row {
  width: 100%;
  margin-top: 4px;
  padding: 12px;
  border: 1px solid color-mix(in srgb, var(--m-color-error) 35%, transparent);
  border-radius: var(--app-radius-input, 12px);
  background: transparent;
  color: var(--m-color-error);
  font-size: 14px;
  font-family: inherit;
  font-weight: 600;
  cursor: pointer;
  transition: background 0.15s ease-out;
}
.logout-row:hover {
  background: color-mix(in srgb, var(--m-color-error) 8%, transparent);
}
.logout-row:active {
  opacity: 0.7;
}
</style>
