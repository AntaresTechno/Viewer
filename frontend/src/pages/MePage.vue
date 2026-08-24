<script setup lang="ts">
import { onMounted, ref } from "vue";
import {
  MiuixButton,
  MiuixCard,
  MiuixInput,
  MiuixText,
  MiuixDivider,
  MiuixSlider,
} from "miuix-vue";
import PasswordField from "@/components/PasswordField.vue";
import AppearancePanel from "@/components/AppearancePanel.vue";
import { useAuth } from "@/stores/auth";
import { api, errMsg } from "@/api/client";

const auth = useAuth();

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
</script>

<template>
  <div class="page-me">
    <MiuixCard class="card" :show-indication="false">
      <h3>外观</h3>
      <AppearancePanel />
    </MiuixCard>

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
            注册于 {{ auth.user.created_at.slice(0, 10) }} · 书架
            {{ auth.user?.shelf_count ?? 0 }} 本
          </div>
        </div>
      </div>

      <MiuixDivider />

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
  </div>
</template>

<style scoped>
.page-me {
  display: grid;
  gap: 20px;
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
  margin-bottom: 14px;
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
.grid {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 12px;
  font-size: 13px;
  color: var(--m-color-on-surface-secondary);
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
</style>
