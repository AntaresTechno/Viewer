import { createRouter, createWebHistory } from "vue-router";
import { useAuth } from "@/stores/auth";

const router = createRouter({
  history: createWebHistory(),
  routes: [
    { path: "/login", component: () => import("@/pages/LoginPage.vue") },
    { path: "/register", component: () => import("@/pages/RegisterPage.vue") },
    {
      path: "/",
      component: () => import("@/layouts/AppShell.vue"),
      children: [
        // 打开网站默认进书架；「首页」是侧栏里的独立选项卡 /home
        { path: "", redirect: "/shelf" },
        { path: "home", component: () => import("@/pages/HomePage.vue") },
        { path: "shelf", component: () => import("@/pages/ShelfPage.vue") },
        { path: "search", component: () => import("@/pages/SearchPage.vue") },
        { path: "explore", component: () => import("@/pages/ExplorePage.vue") },
        { path: "library", component: () => import("@/pages/LocalLibraryPage.vue") },
        {
          path: "book/:bookUrl",
          component: () => import("@/pages/BookPage.vue"),
          props: true,
        },
        {
          // 详情页短链：由 book_refs 缓存档案直接渲染（不再拼超长 query）
          path: "book/ref/:refId",
          component: () => import("@/pages/BookPage.vue"),
          props: true,
        },
        {
          path: "replace",
          component: () => import("@/pages/ReplaceRulesPage.vue"),
        },
        {
          // 正文净化插件：规则包 / 缓存 / 测试
          path: "purify",
          component: () => import("@/pages/PurifyPage.vue"),
        },
        {
          // WebDAV 备份插件：配置 / 备份 / 恢复
          path: "webdav",
          component: () => import("@/pages/WebDavPage.vue"),
        },
        { path: "me", component: () => import("@/pages/MePage.vue") },
        {
          path: "admin",
          component: () => import("@/pages/admin/DashboardPage.vue"),
        },
        {
          path: "admin/users",
          component: () => import("@/pages/admin/UsersPage.vue"),
        },
        {
          path: "admin/roles",
          component: () => import("@/pages/admin/RolesPage.vue"),
        },
        {
          path: "admin/plugins",
          component: () => import("@/pages/admin/PluginsPage.vue"),
        },
        {
          path: "admin/sources",
          component: () => import("@/pages/admin/SourcesPage.vue"),
        },
      ],
    },
    {
      // 阅读器独立页：不套 AppShell，沉浸式全屏
      path: "/reader",
      component: () => import("@/pages/ReaderPage.vue"),
    },
    { path: "/:pathMatch(.*)*", redirect: "/" },
  ],
});

router.beforeEach((to) => {
  const auth = useAuth();
  const openPaths = ["/login", "/register"];
  if (!auth.isLoggedIn && !openPaths.includes(to.path)) {
    return { path: "/login", query: to.fullPath !== "/" ? { next: to.fullPath } : {} };
  }
  if (auth.isLoggedIn && openPaths.includes(to.path)) return "/";
});

export default router;
