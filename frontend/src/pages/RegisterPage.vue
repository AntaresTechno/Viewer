<script setup lang="ts">
import { ref } from "vue";
import { useRouter } from "vue-router";
import {
  MiuixButton,
  MiuixCard,
  MiuixInput,
  MiuixText,
} from "miuix-vue";
import PasswordField from "@/components/PasswordField.vue";
import { useAuth } from "@/stores/auth";
import { errMsg } from "@/api/client";

const auth = useAuth();
const router = useRouter();

const username = ref("");
const displayName = ref("");
const password = ref("");
const confirm = ref("");
const busy = ref(false);
const error = ref("");

async function submit() {
  if (username.value.length < 2) {
    error.value = "用户名至少 2 个字符";
    return;
  }
  if (password.value.length < 6) {
    error.value = "密码至少 6 位";
    return;
  }
  if (password.value !== confirm.value) {
    error.value = "两次输入的密码不一致";
    return;
  }
  busy.value = true;
  error.value = "";
  try {
    await auth.register(username.value, password.value, displayName.value);
    router.push("/");
  } catch (e) {
    error.value = errMsg(e);
  } finally {
    busy.value = false;
  }
}
</script>

<template>
  <div class="login-wrap">
    <MiuixCard class="panel" :show-indication="false">
      <div class="hero">
        <div class="logo">V</div>
        <MiuixText type="title2">注册 Viewer 账号</MiuixText>
      </div>
      <form class="form" @submit.prevent="submit">
        <MiuixInput v-model="username" label="用户名" single-line />
        <MiuixInput v-model="displayName" label="昵称（可选）" single-line />
        <PasswordField v-model="password" label="密码（至少 6 位）" />
        <PasswordField v-model="confirm" label="确认密码" />
        <div v-if="error" class="error">{{ error }}</div>
        <MiuixButton type="primary" :disabled="busy || !username || !password" @click="submit">
          {{ busy ? "注册中…" : "注 册" }}
        </MiuixButton>
      </form>
      <div class="alt">
        已有账号？<router-link to="/login">去登录</router-link>
      </div>
    </MiuixCard>
  </div>
</template>

<style scoped src="./auth.css"></style>
