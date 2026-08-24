<script setup lang="ts">
import { ref } from "vue";
import { useRoute, useRouter } from "vue-router";
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
const route = useRoute();

const username = ref("");
const password = ref("");
const busy = ref(false);
const error = ref("");

async function submit() {
  if (!username.value || !password.value) {
    error.value = "请输入用户名和密码";
    return;
  }
  busy.value = true;
  error.value = "";
  try {
    await auth.login(username.value, password.value);
    const next = (route.query.next as string) || "/";
    router.push(next);
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
        <MiuixText type="title2">登录 Viewer</MiuixText>
        <MiuixText color="var(--m-color-on-surface-secondary)">
          阅读与管理一体的书源站点
        </MiuixText>
      </div>

      <form class="form" @submit.prevent="submit">
        <MiuixInput v-model="username" label="用户名" single-line />
        <PasswordField v-model="password" label="密码" />
        <div v-if="error" class="error">{{ error }}</div>
        <MiuixButton type="primary" :disabled="busy || !username || !password" @click="submit">
          {{ busy ? "登录中…" : "登 录" }}
        </MiuixButton>
      </form>

      <div class="alt">
        还没有账号？
        <router-link to="/register">注册一个</router-link>
      </div>
      <div class="hint">默认管理员：admin / view123456</div>
    </MiuixCard>
  </div>
</template>

<style scoped src="./auth.css"></style>
