/** Typed API client for the Viewer backend. */
import axios from "axios";

export interface UserPublic {
  id: number;
  username: string;
  display_name: string;
  email: string | null;
  bio: string | null;
  avatar_hue: number;
  is_superuser: boolean;
  is_active: boolean;
  role_ids: number[];
  permissions: string[];
  created_at: string | null;
  last_login_at?: string | null;
  shelf_count?: number;
}

export interface RoleItem {
  id: number;
  name: string;
  description: string;
  permissions: string[];
  is_system: boolean;
  users_count?: number;
}

export interface PermissionCatalog {
  items: { key: string; title: string }[];
  grouped: Record<string, { key: string; title: string }[]>;
}

export interface PluginItem {
  name: string;
  title: string;
  version: string;
  description: string;
  mount: string;
  enabled: boolean;
}

export interface DashboardSummary {
  users_total: number;
  sources_total: number;
  shelf_total: number;
  roles_total: number;
  plugins_enabled: number;
  plugins_total: number;
  recent_users: {
    id: number;
    username: string;
    display_name: string;
    created_at: string | null;
  }[];
  server_time: string;
}

export interface BookResult {
  name: string;
  author?: string;
  kind?: string;
  wordCount?: string;
  intro?: string;
  coverUrl?: string;
  lastChapter?: string;
  bookUrl: string;
  origin: string;
  originName?: string;
}

export interface SourceRow {
  id: number;
  sourceUrl: string;
  sourceName: string;
  sourceGroup: string;
  enabled: boolean;
  engine: string;
}

/** 单个书源的完整信息 + 规则快照（详情页展示"源规则/书源"用）。 */
export interface SourceInfo {
  name: string;
  type: string;
  url: string;
  enabled: boolean;
  rules: Record<string, unknown>;
}

export interface EngineInfo {
  key: string;
  title: string;
  version: string;
  description: string;
  pluginName: string;
  sources: number;
}

export interface Chapter {
  url: string;
  baseUrl: string;
  title: string;
  index: number;
  isVolume: boolean;
  isVip: boolean;
}

export interface ShelfEntry {
  id: number;
  bookUrl: string;
  tocUrl: string;
  name: string;
  author: string;
  coverUrl: string;
  intro: string;
  lastChapter: string;
  sourceUrl: string;
  /** 书源侧最近一次检测到内容变化的时间（目录刷新发现新章时更新） */
  updatedAt?: string | null;
  /** 目录刷新检测到新章且还没读过时为 true */
  hasUpdate?: boolean;
  progress?: {
    chapterIndex: number;
    chapterTitle: string;
    offset?: number;
    updatedAt: string | null;
  };
  toc?: {
    chapters: number;
    status: "none" | "queued" | "running" | "done" | "error";
    error: string;
  };
}

/** 书架排序方式：加入时间 / 最近更新 / 最后阅读 */
export type ShelfSort = "added" | "updated" | "read";

export interface ExploreKind {
  title: string;
  url: string | null;
  type: string;
  chars?: string[];
}

export interface ReplaceRuleItem {
  id: number;
  name: string;
  group: string;
  groupOrder: number;
  order: number;
  isActive: boolean;
  pattern: string;
  replacement: string;
  scope: string;
  regex: boolean;
  caseSensitive: boolean;
}

export interface BookRef {
  id: number;
  sourceUrl: string;
  bookUrl: string;
  name: string;
  author: string;
  coverUrl: string;
  intro?: string;
  kind?: string;
  lastChapter?: string;
  tocUrl?: string;
}

export interface PrefetchItem {
  url: string;
  title?: string;
  base?: string;
  isVolume?: boolean;
}

/* ------------------------------------------------------ purify (正文净化) */
/** 规则来源目录项：内置(MD3) / 乌云净化（自定义来源走 import 接口）。 */
export interface PurifyCatalogItem {
  key: string;
  title: string;
  description: string;
  installable: boolean;
  installed: boolean;
  jsEngine: boolean;
  /** 乌云净化专属字段 */
  packId?: number | null;
  packEnabled?: boolean;
  ruleCount?: number;
  groups?: Record<string, number>;
  jsRules?: number;
  contentRules?: number;
}

export interface PurifyPack {
  id: number;
  name: string;
  description: string;
  origin: string;
  enabled: boolean;
  order: number;
  ruleCount?: number;
}

export interface PurifyRuleItem {
  id: number;
  packId: number;
  name: string;
  order: number;
  isActive: boolean;
  pattern: string;
  replacement: string;
  scope: string;
  regex: boolean;
  caseSensitive: boolean;
  scopeContent: boolean;
  scopeTitle: boolean;
}

export interface PurifyCacheStats {
  chapters: number;
  rawBytes: number;
  contentBytes: number;
  booksTotal: number;
  books: Array<{
    sourceUrl: string;
    bookUrl: string;
    name: string;
    chapters: number;
  }>;
}

/* ------------------------------------------------------- home（首页插件） */
export interface HomeRecent {
  bookUrl: string;
  sourceUrl: string;
  name: string;
  author: string;
  coverUrl: string;
  intro?: string;
  lastChapter?: string;
  chapterIndex: number;
  chapterTitle: string;
  readAt: string | null;
}

export interface HomeUpdate {
  id: number;
  bookUrl: string;
  sourceUrl: string;
  name: string;
  author: string;
  coverUrl: string;
  lastChapter?: string;
  updatedAt: string | null;
  readAt: string | null;
}

export interface HomeSummary {
  todaySeconds: number;
  totalSeconds: number;
  totalDays: number;
  totalBooks: number;
  streakDays: number;
  recents: HomeRecent[];
  updates: HomeUpdate[];
  date: string;
}

export interface DailyPoint {
  day: string;
  seconds: number;
}

/* ---------------------------------------------------- webdav（备份插件） */
export interface WebDavConfigInfo {
  url: string;
  username: string;
  directory: string;
  hasPassword: boolean;
  autoBackup: boolean;
  lastBackupAt: string | null;
  lastBackupFile: string;
}

export interface WebDavBackupItem {
  name: string;
  href: string;
  size: number;
  modified: string;
  isDir: boolean;
}

const http = axios.create({ baseURL: "/api", timeout: 60_000 });
http.interceptors.request.use((cfg) => {
  const token = localStorage.getItem("viewer_token");
  if (token) cfg.headers.Authorization = `Bearer ${token}`;
  return cfg;
});

http.interceptors.response.use(
  (r) => r,
  (err) => {
    if (err?.response?.status === 401 && location.pathname !== "/login") {
      localStorage.removeItem("viewer_token");
      if (!location.pathname.startsWith("/login")) location.href = "/login";
    }
    return Promise.reject(err);
  },
);

export function errMsg(e: unknown): string {
  const anyErr = e as { response?: { data?: { detail?: unknown } } };
  const d = anyErr?.response?.data?.detail;
  if (typeof d === "string") return d;
  if (Array.isArray(d)) {
    const first = d[0] as { msg?: string };
    return first?.msg ?? "请求失败";
  }
  return "网络错误";
}

/* ------------------------------------------------------------------ auth */
export const api = {
  login: async (username: string, password: string) => {
    const r = await http.post<{ token: string; user: UserPublic }>(
      "/auth/login",
      { username, password },
    );
    return r.data;
  },
  register: async (username: string, password: string, display_name = "") => {
    const r = await http.post<{ token: string; user: UserPublic }>(
      "/auth/register",
      { username, password, display_name },
    );
    return r.data;
  },
  me: async () => (await http.get<UserPublic>("/auth/me")).data,
  updateProfile: async (patch: Partial<Pick<UserPublic,
    "display_name" | "email" | "bio" | "avatar_hue">>) => {
    await http.patch("/auth/me/profile", patch);
  },
  changePassword: async (old_password: string, new_password: string) => {
    await http.post("/auth/me/password", { old_password, new_password });
  },

  /* ------------------------------------------------------------- admin */
  dashboard: async () =>
    (await http.get<DashboardSummary>("/dashboard")).data,

  usersList: async (keyword = "", page = 1) => {
    const r = await http.get("/users", { params: { keyword, page, size: 50 } });
    return r.data as { total: number; items: UserPublic[] };
  },
  userCreate: async (body: Record<string, unknown>) => {
    const r = await http.post("/users", body);
    return r.data as UserPublic;
  },
  userUpdate: async (id: number, patch: Record<string, unknown>) => {
    const r = await http.patch(`/users/${id}`, patch);
    return r.data as UserPublic;
  },
  userDelete: async (id: number) => {
    await http.delete(`/users/${id}`);
  },
  userResetPassword: async (id: number, new_password: string) => {
    await http.post(`/users/${id}/reset-password`, { new_password });
  },

  rolesList: async () => {
    const r = await http.get("/roles");
    return r.data as { items: RoleItem[] };
  },
  roleCreate: async (b: Pick<RoleItem, "name" | "description"> & {
    permissions: string[];
  }) => (await http.post<RoleItem>("/roles", b)).data,
  roleUpdate: async (id: number, b: Pick<RoleItem, "name" | "description"> & {
    permissions: string[];
  }) => (await http.patch<RoleItem>(`/roles/${id}`, b)).data,
  roleDelete: async (id: number) => {
    await http.delete(`/roles/${id}`);
  },
  permCatalog: async () =>
    (await http.get<PermissionCatalog>("/roles/permissions/catalog")).data,

  pluginsList: async () => {
    const r = await http.get("/plugins");
    return r.data as { items: PluginItem[] };
  },
  pluginToggle: async (name: string, enabled: boolean) => {
    await http.post(`/plugins/${name}/toggle`, { enabled });
  },
  pluginInstall: async (file: File) => {
    const fd = new FormData();
    fd.append("file", file);
    const r = await http.post<{ ok: boolean; name: string; title?: string; version?: string; note?: string }>(
      "/plugins/install",
      fd,
      { timeout: 120_000 },
    );
    return r.data;
  },

  /* ------------------------------------------------------------ books */
  sourcesList: async () => {
    const r = await http.get("/books/sources");
    return r.data as { items: SourceRow[]; groups: string[] };
  },
  enginesList: async () => {
    const r = await http.get("/books/engines");
    return r.data as { items: EngineInfo[] };
  },
  sourcesImport: async (body: { data?: string; url?: string; engine?: string }) => {
    const r = await http.post<{
      added: number;
      updated: number;
      skipped: number;
    }>("/books/sources/import", body);
    return r.data;
  },
  sourcesDelete: async (ids: number[]) => {
    await http.post("/books/sources/delete", { ids });
  },
  sourceToggle: async (id: number) => {
    const r = await http.post(`/books/sources/${id}/toggle`, {});
    return r.data as { enabled: boolean };
  },
  searchBooks: async (
    key: string,
    page: number,
    sourceIds?: number[],
  ): Promise<{ items: BookResult[]; errors: { message: string; originName?: string }[] }> => {
    const r = await http.post("/books/search", {
      key,
      page,
      source_ids: sourceIds,
    });
    return r.data;
  },
  bookInfo: async (
    sourceUrl: string,
    bookUrl: string,
    name = "",
    author = "",
    cover = "",
  ) => {
    const params = new URLSearchParams({
      source_url: sourceUrl,
      book_url: bookUrl,
      name,
      author,
      cover,
    });
    const r = await http.get(`/books/info?${params.toString()}`);
    return r.data as BookResult & { tocUrl: string };
  },
  /** 单个书源信息 + 规则快照（详情页"源规则/书源"展示用）。 */
  sourceInfo: async (sourceUrl: string) => {
    const r = await http.get<SourceInfo>(
      `/books/source?source_url=${encodeURIComponent(sourceUrl)}`,
    );
    return r.data;
  },
  /** 本地书库概览。 */
  library: async () => {
    const r = await http.get<{
      chapters: number;
      images: number;
      covers: number;
      booksTotal: number;
      books: Array<{
        sourceUrl: string;
        bookUrl: string;
        name: string;
        author: string;
        coverUrl: string;
        intro: string;
        storedChapters: number;
        totalChapters: number;
      }>;
    }>("/books/library");
    return r.data;
  },
  /** 一键预下载整本到本地书库（后台）。 */
  libraryDownload: async (body: {
    sourceUrl: string;
    bookUrl: string;
    name?: string;
    author?: string;
    cover?: string;
    concurrency?: number | null;
  }) => {
    const r = await http.post<{
      status: string;
      done: number;
      total: number;
      error: string;
    }>("/books/library/download", body);
    return r.data;
  },
  /** 预下载作业进度。 */
  libraryDownloadStatus: async () => {
    const r = await http.get<{ jobs: Array<{
      key: string;
      sourceUrl: string;
      bookUrl: string;
      name: string;
      status: string;
      total: number;
      done: number;
      current: string;
      error: string;
    }> }>("/books/library/download/status");
    return r.data;
  },
  /** 清除某本书的所有已下载章节。 */
  libraryClear: async (sourceUrl: string, bookUrl: string) => {
    const params = new URLSearchParams({ source_url: sourceUrl, book_url: bookUrl });
    const r = await http.delete<{ deleted: number }>(
      `/books/library?${params.toString()}`,
    );
    return r.data;
  },
  exploreKinds: async (sourceUrl: string) => {
    const params = new URLSearchParams({ source_url: sourceUrl });
    const r = await http.get(`/books/explore/kinds?${params.toString()}`);
    return r.data as { items: ExploreKind[] };
  },
  exploreBooks: async (sourceUrl: string, url: string, page: number) => {
    const params = new URLSearchParams({ source_url: sourceUrl, url, page: String(page) });
    const r = await http.get(`/books/explore?${params.toString()}`);
    return r.data as { items: BookResult[] };
  },
  chaptersCached: async (sourceUrl: string, bookUrl: string, fallback = true) => {
    const params = new URLSearchParams({
      source_url: sourceUrl,
      book_url: bookUrl,
      fallback: String(fallback),
    });
    const r = await http.get(`/books/chapters?${params.toString()}`);
    return r.data as { chapters: Chapter[]; cached: boolean };
  },
  toc: async (sourceUrl: string, tocUrl: string) => {
    const params = new URLSearchParams({
      source_url: sourceUrl,
      toc_url: tocUrl,
    });
    const r = await http.get(`/books/toc?${params.toString()}`);
    return r.data as { chapters: Chapter[]; cached: boolean };
  },
  content: async (
    sourceUrl: string,
    url: string,
    base: string,
    title: string,
    name = "",
    bookUrl = "",
    nextChapterUrl = "",
    isVolume = false,
  ) => {
    const params = new URLSearchParams({
      source_url: sourceUrl,
      url,
      base,
      title,
      name,
      book_url: bookUrl,
      next_chapter_url: nextChapterUrl,
      // 缓存键的一部分：与预取接口传的 isVolume 保持一致，否则命中不了
      is_volume: String(isVolume),
    });
    const r = await http.get(`/books/content?${params.toString()}`);
    return r.data as { content: string };
  },
  shelf: async (sort: ShelfSort = "added") => {
    const r = await http.get("/books/shelf", { params: { sort } });
    return r.data as { items: ShelfEntry[] };
  },
  shelfAdd: async (e: Omit<ShelfEntry, "id" | "progress">) => {
    const r = await http.post("/books/shelf", e);
    return r.data as { id: number };
  },
  shelfRemove: async (id: number) => {
    await http.delete(`/books/shelf/${id}`);
  },
  shelfRefreshToc: async (id: number) => {
    const r = await http.post(`/books/shelf/${id}/refresh-toc`, {});
    return r.data as { ok: boolean; message?: string };
  },
  progressGet: async (bookUrl: string) => {
    const params = new URLSearchParams({ book_url: bookUrl });
    const r = await http.get(`/books/progress?${params.toString()}`);
    return r.data as {
      progress: {
        chapterIndex: number;
        chapterTitle: string;
        offset: number;
        updatedAt: string | null;
      } | null;
    };
  },
  saveProgress: async (
    bookUrl: string,
    chapterIndex: number,
    chapterTitle: string,
    offset = 0,
  ) => {
    await http.post("/books/progress", {
      bookUrl,
      chapterIndex,
      chapterTitle,
      offset,
    });
  },

  /* ------------------------------------------------------- book short id */
  resolveBook: async (b: Omit<BookRef, "id">) => {
    const r = await http.post<BookRef>("/books/resolve", b);
    return r.data;
  },
  bookRef: async (id: number) => {
    const r = await http.get<BookRef>(`/books/refs/${id}`);
    return r.data;
  },
  /** 按 来源+书籍地址 查缓存档案（详情页即时渲染用，不现场请求书源）。 */
  bookProfile: async (sourceUrl: string, bookUrl: string) => {
    const params = new URLSearchParams({
      source_url: sourceUrl,
      book_url: bookUrl,
    });
    const r = await http.get<(BookRef & { found: true }) | { found: false }>(
      `/books/profile?${params.toString()}`,
    );
    return r.data;
  },
  /** 目录缓存未命中时，排队一次后台抓取（阅读器轮询直到完成）。 */
  chaptersRefresh: async (sourceUrl: string, bookUrl: string) => {
    const r = await http.post<{ ok: boolean; message?: string }>(
      "/books/chapters/refresh",
      { source_url: sourceUrl, book_url: bookUrl },
    );
    return r.data;
  },
  tocStatus: async (sourceUrl: string, bookUrl: string) => {
    const params = new URLSearchParams({
      source_url: sourceUrl,
      book_url: bookUrl,
    });
    const r = await http.get<{ status: string; error: string; chapters: number }>(
      `/books/toc-status?${params.toString()}`,
    );
    return r.data;
  },
  prefetchContent: async (sourceUrl: string, items: PrefetchItem[]) => {
    if (!items.length) return { queued: 0 };
    const r = await http.post<{ queued: number }>(
      "/books/content/prefetch",
      { source_url: sourceUrl, items },
      { timeout: 15_000 },
    );
    return r.data;
  },

  /* --------------------------------------------------- replace rules */
  replaceList: async () => {
    const r = await http.get("/books/replace");
    return r.data as { items: ReplaceRuleItem[] };
  },
  replaceImport: async (data: string) => {
    const r = await http.post<{ imported: number }>("/books/replace/import", { data });
    return r.data;
  },
  replaceUpdate: async (id: number, patch: Partial<ReplaceRuleItem>) => {
    await http.put(`/books/replace/${id}`, patch);
  },
  replaceToggle: async (id: number) => {
    const r = await http.post(`/books/replace/${id}/toggle`, {});
    return r.data as { ok: boolean; isActive: boolean };
  },
  replaceDelete: async (ids: number[]) => {
    await http.post("/books/replace/delete", { ids });
  },
  replaceTest: async (text: string, bookName = "", sourceUrl = "") => {
    const r = await http.post<{ content: string; applied: string[] }>(
      "/books/replace/test",
      { text, bookName, sourceUrl },
    );
    return r.data;
  },

  /* ------------------------------------------------- purify (正文净化) */
  purifyCatalog: async () => {
    const r = await http.get("/purify/catalog");
    return r.data as { items: PurifyCatalogItem[] };
  },
  purifyInstallPreset: async (key: string) => {
    const r = await http.post<{
      ok: boolean;
      installed: boolean;
      packId?: number;
      rules?: number;
      note?: string;
    }>("/purify/presets/install", { key });
    return r.data;
  },
  purifyPacks: async () => {
    const r = await http.get("/purify/packs");
    return r.data as { items: PurifyPack[] };
  },
  purifyCreatePack: async (name: string, description = "") => {
    const r = await http.post<{ ok: boolean; id: number }>(
      "/purify/packs/create",
      { name, description },
    );
    return r.data;
  },
  /** 导入 legado 替换规则 JSON 为一个净化规则包（粘贴）。 */
  purifyImportPack: async (data: string, name = "") => {
    const r = await http.post<{ ok: boolean; packId: number; imported: number }>(
      "/purify/packs/import",
      { data, name },
    );
    return r.data;
  },
  /** 从 URL 拉取规则 JSON 导入为一个净化规则包。 */
  purifyImportUrl: async (url: string, name = "") => {
    const r = await http.post<{ ok: boolean; packId: number; imported: number }>(
      "/purify/packs/import-url",
      { url, name },
    );
    return r.data;
  },
  /** 上传规则 JSON 文件导入为一个净化规则包。 */
  purifyImportFile: async (file: File, name = "") => {
    const fd = new FormData();
    fd.append("file", file);
    if (name) fd.append("name", name);
    const r = await http.post<{ ok: boolean; packId: number; imported: number }>(
      "/purify/packs/import-file",
      fd,
      { timeout: 60_000 },
    );
    return r.data;
  },
  purifyUpdatePack: async (
    id: number,
    patch: Partial<Pick<PurifyPack, "name" | "description" | "enabled" | "order">>,
  ) => {
    await http.put(`/purify/packs/${id}`, patch);
  },
  purifyTogglePack: async (id: number) => {
    const r = await http.post(`/purify/packs/${id}/toggle`, {});
    return r.data as { ok: boolean; enabled: boolean };
  },
  purifyDeletePack: async (id: number) => {
    await http.delete(`/purify/packs/${id}`);
  },
  purifyRules: async (packId: number) => {
    const r = await http.get(`/purify/packs/${packId}/rules`);
    return r.data as { items: PurifyRuleItem[] };
  },
  purifyAddRule: async (
    packId: number,
    body: Omit<PurifyRuleItem, "id" | "packId" | "isActive">,
  ) => {
    const r = await http.post<{ ok: boolean; id: number }>(
      `/purify/packs/${packId}/rules`,
      body,
    );
    return r.data;
  },
  purifyUpdateRule: async (id: number, patch: Partial<PurifyRuleItem>) => {
    await http.put(`/purify/rules/${id}`, patch);
  },
  purifyToggleRule: async (id: number) => {
    const r = await http.post(`/purify/rules/${id}/toggle`, {});
    return r.data as { ok: boolean; isActive: boolean };
  },
  purifyDeleteRules: async (ids: number[]) => {
    await http.post("/purify/rules/delete", { ids });
  },
  purifyTest: async (text: string, bookName = "", sourceUrl = "") => {
    const r = await http.post<{
      content: string;
      applied: string[];
      fingerprint: string;
    }>("/purify/test", { text, bookName, sourceUrl });
    return r.data;
  },
  purifyContent: async (
    sourceUrl: string,
    url: string,
    bookUrl = "",
    title = "",
    base = "",
    name = "",
  ) => {
    const params = new URLSearchParams({
      source_url: sourceUrl,
      url,
      book_url: bookUrl,
      title,
      base,
      name,
    });
    const r = await http.get<{ content: string; cached: boolean; purified: boolean }>(
      `/purify/content?${params.toString()}`,
    );
    return r.data;
  },
  purifyCacheStats: async () => {
    const r = await http.get("/purify/cache/stats");
    return r.data as PurifyCacheStats;
  },
  purifyClearCache: async (sourceUrl = "", bookUrl = "") => {
    const params = new URLSearchParams({ source_url: sourceUrl, book_url: bookUrl });
    const r = await http.delete<{ deleted: number }>(
      `/purify/cache?${params.toString()}`,
    );
    return r.data;
  },

  /* ------------------------------------------------- home (首页插件) */
  homeSummary: async () => {
    const r = await http.get<HomeSummary>("/home/summary");
    return r.data;
  },
  homeHeartbeat: async (bookUrl: string, sourceUrl: string, seconds: number) => {
    await http.post("/home/heartbeat", { bookUrl, sourceUrl, seconds });
  },
  homeDaily: async (days = 14) => {
    const r = await http.get<{ items: DailyPoint[] }>("/home/daily", {
      params: { days },
    });
    return r.data.items;
  },

  /* ------------------------------------------------ webdav (备份插件) */
  webdavGetConfig: async () => {
    const r = await http.get<WebDavConfigInfo>("/webdav/config");
    return r.data;
  },
  webdavSaveConfig: async (body: {
    url: string;
    username: string;
    password?: string;
    directory: string;
    autoBackup: boolean;
  }) => {
    const r = await http.put<{ ok: boolean } & WebDavConfigInfo>(
      "/webdav/config",
      body,
    );
    return r.data;
  },
  webdavTest: async () => {
    const r = await http.post<{ ok: boolean }>("/webdav/test");
    return r.data;
  },
  webdavBackup: async () => {
    const r = await http.post<{
      ok?: boolean;
      file: string;
      shelf: number;
      progress: number;
      readingStats: number;
    }>("/webdav/backup");
    return r.data;
  },
  webdavBackups: async () => {
    const r = await http.get<{ items: WebDavBackupItem[] }>("/webdav/backups");
    return r.data.items;
  },
  webdavRestore: async (file: string) => {
    const r = await http.post<{
      ok: boolean;
      shelfAdded: number;
      shelfUpdated: number;
      progressUpdated: number;
      statsMerged: number;
    }>("/webdav/restore", { file });
    return r.data;
  },
  webdavDeleteBackup: async (name: string) => {
    await http.delete(`/webdav/backups/${encodeURIComponent(name)}`);
  },
};

export function coverProxyUrl(url: string): string {
  const token = localStorage.getItem("viewer_token") ?? "";
  return `/api/books/cover?url=${encodeURIComponent(url)}&token=${encodeURIComponent(token)}`;
}
