import { defineStore } from "pinia";
import { api } from "@/api/client";
import type { UserPublic } from "@/api/client";

export const useAuth = defineStore("auth", {
  state: () => ({
    token: localStorage.getItem("viewer_token") ?? "",
    user: JSON.parse(
      localStorage.getItem("viewer_user") ?? "null",
    ) as UserPublic | null,
  }),
  getters: {
    isLoggedIn: (s) => !!s.token,
    isSuperuser: (s) => !!s.user?.is_superuser,
    perms: (s) => new Set(s.user?.permissions ?? []),
  },
  actions: {
    can(key: string): boolean {
      const u = this.user;
      if (!u) return false;
      if (u.is_superuser || u.permissions.includes("*")) return true;
      if (u.permissions.includes(key)) return true;
      const ns = key.split(".")[0] + ".*";
      return u.permissions.includes(ns);
    },
    async login(username: string, password: string) {
      const data = await api.login(username, password);
      this.token = data.token;
      this.user = data.user;
      localStorage.setItem("viewer_token", data.token);
      localStorage.setItem("viewer_user", JSON.stringify(data.user));
    },
    async register(username: string, password: string, displayName = "") {
      const data = await api.register(username, password, displayName);
      this.token = data.token;
      this.user = data.user;
      localStorage.setItem("viewer_token", data.token);
      localStorage.setItem("viewer_user", JSON.stringify(data.user));
    },
    async refreshMe() {
      try {
        this.user = await api.me();
        localStorage.setItem("viewer_user", JSON.stringify(this.user));
      } catch {
        /* 401 handled by interceptor */
      }
    },
    logout() {
      this.token = "";
      this.user = null;
      localStorage.removeItem("viewer_token");
      localStorage.removeItem("viewer_user");
    },
  },
});
