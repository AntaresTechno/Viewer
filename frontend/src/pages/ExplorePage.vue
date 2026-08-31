<script setup lang="ts">
/**
 * 发现页。
 *
 * legado 的发现页不是一个分类列表，而是书源自己拼的 **flex 控件矩阵**：
 * `exploreUrl` 返回 ~200 个带权重（layout_flexBasisPercent）的按钮，外加
 * text / button / toggle / select 四种交互控件（番茄书源的「关键词输入 /
 * 搜索 / ⚙ / 分类下拉 / 偏好下拉」就是这类）。每个控件带一条 `action`
 * JS，必须在服务端求值 —— 它调的是书源 jsLib 里的函数，要签名、发请求、
 * 读写 infoMap，前端拿不到。
 *
 * 因此本页的职责是：
 * 1. 按 legado 的 flex 算法把控件排成网格（utils/exploreLayout.ts）；
 * 2. 按 type 渲染成对应控件；
 * 3. 把点击事件和值回传给服务端求值；
 * 4. 响应服务端回传的信号：refresh（重拉分类）/ openLogin（打开书源
 *    登录）/ searchKey（跳搜索页）。
 */
import { computed, onMounted, ref } from "vue";
import { useRouter } from "vue-router";
import {
  MiuixButton,
  MiuixProgressIndicator,
} from "miuix-vue";
import { api, errMsg, coverProxyUrl } from "@/api/client";
import type {
  BookResult,
  ExploreActionResult,
  ExploreKind,
  SourceRow,
} from "@/api/client";
import { FALLBACK_COVER_SVG, onCoverError } from "@/utils/cover";
import LoadingImage from "@/components/LoadingImage.vue";
import { openDetail } from "@/utils/reader";
import { calculateFlexRows, flexLayout, rowSpan } from "@/utils/exploreLayout";
import SourceLoginDialog from "@/components/SourceLoginDialog.vue";
import { useAuth } from "@/stores/auth";

const $router = useRouter();
const auth = useAuth();

/** legado 用 6 格一行（ExploreScreen 的 totalSpan < 6 补白）。 */
const MAX_SPAN = 6;

const sources = ref<SourceRow[]>([]);
const activeSource = ref("");
const kinds = ref<ExploreKind[]>([]);
/** 控件当前值（服务端 infoMap 的快照），用于回显 select / toggle / text。 */
const values = ref<Record<string, string>>({});
const activeKindUrl = ref("");
const books = ref<BookResult[]>([]);
const page = ref(1);
const loading = ref(false);
const loadingKinds = ref(false);
const error = ref("");
const done = ref(false);
const logs = ref<string[]>([]);
const acting = ref(false);
const showLogin = ref(false);

/** 按 legado flex 算法排好的控件行。 */
const kindRows = computed(() =>
  calculateFlexRows(kinds.value, MAX_SPAN, (k) => flexLayout(k.style)),
);

/** url 型分类（真正能翻出书籍的那些）。 */
const urlKinds = computed(() => kinds.value.filter((k) => !!k.url));

const canLogin = () => auth.can("legado.login");

/** 当前选中源的名字，用于登录对话框标题。 */
const activeSourceName = computed(
  () =>
    sources.value.find((s) => s.sourceUrl === activeSource.value)?.sourceName ||
    activeSource.value,
);

onMounted(async () => {
  try {
    const r = await api.sourcesList();
    sources.value = r.items.filter((s) => s.enabled);
    if (sources.value.length) await pickSource(sources.value[0].sourceUrl);
  } catch (e) {
    error.value = errMsg(e);
  }
});

async function pickSource(url: string) {
  activeSource.value = url;
  kinds.value = [];
  values.value = {};
  activeKindUrl.value = "";
  books.value = [];
  error.value = "";
  logs.value = [];
  done.value = false;
  await loadKinds(url);
}

async function loadKinds(url: string) {
  loadingKinds.value = true;
  try {
    const r = await api.exploreKinds(url);
    kinds.value = r.items;
    values.value = r.values ?? {};
    if (!kinds.value.length) {
      error.value = "该书源未配置发现（exploreUrl），换一个书源试试。";
      return;
    }
    // 之前的分类可能已被 refreshExplore 换掉，重拉后要重新挑一个
    const still = kinds.value.some((k) => k.url === activeKindUrl.value);
    if (!still) {
      const first = kinds.value.find((k) => k.url);
      activeKindUrl.value = "";
      if (first) await pickKind(first.url!);
    }
  } catch (e) {
    error.value = errMsg(e);
  } finally {
    loadingKinds.value = false;
  }
}

async function pickKind(url: string) {
  if (!url || activeKindUrl.value === url) return;
  activeKindUrl.value = url;
  page.value = 1;
  books.value = [];
  done.value = false;
  await fetchBooks();
}

async function fetchBooks() {
  if (!activeSource.value || !activeKindUrl.value || loading.value || done.value)
    return;
  loading.value = true;
  try {
    const r = await api.exploreBooks(
      activeSource.value,
      activeKindUrl.value,
      page.value,
    );
    books.value.push(...r.items);
    done.value = r.items.length === 0;
    page.value += 1;
  } catch (e) {
    error.value = errMsg(e);
    done.value = true;
  } finally {
    loading.value = false;
  }
}

function valueOf(kind: ExploreKind): string {
  const cur = values.value[kind.title];
  if (cur) return cur;
  return kind.default ?? kind.chars?.[0] ?? "";
}

/**
 * 执行控件动作：由服务端求值 action，前端响应回传的信号。
 *
 * 顺序（服务端保证）对齐 legado 的 ToggleTypeItem / SelectTypeItem：
 * 先把新值写进 infoMap，再执行 action —— 番茄的 action 读
 * `infoMap['分类：']` 取当前选中项，值没落盘动作就是空转。
 */
async function runAction(kind: ExploreKind, value?: string | null) {
  if (!activeSource.value || acting.value) return;
  acting.value = true;
  error.value = "";
  try {
    const res: ExploreActionResult = await api.exploreKindAction(
      activeSource.value,
      kind,
      value ?? null,
    );
    if (res.values) values.value = res.values;
    logs.value = res.log ?? [];
    if (res.error) error.value = res.error;
    if (res.refresh) await loadKinds(activeSource.value);
    if (res.openLogin) {
      if (canLogin()) showLogin.value = true;
      else
        error.value =
          "该书源请求打开登录页。请到 管理 → 书源管理 登录此书源（需要 legado.login 权限）。";
    }
    if (res.searchKey) {
      await $router.push({ path: "/search", query: { q: res.searchKey } });
    }
  } catch (e) {
    error.value = errMsg(e);
  } finally {
    acting.value = false;
  }
}

function onSelectChange(kind: ExploreKind, value: string) {
  values.value = { ...values.value, [kind.title]: value };
  void runAction(kind, value);
}

/** toggle：在 chars 间循环取值，再执行 action。 */
function toggleKind(kind: ExploreKind) {
  const chars = kind.chars?.length ? kind.chars : ["false", "true"];
  const cur = valueOf(kind);
  const idx = chars.indexOf(cur);
  const next = chars[(idx + 1) % chars.length];
  values.value = { ...values.value, [kind.title]: next };
  void runAction(kind, next);
}

async function openBook(b: BookResult) {
  await openDetail($router, {
    sourceUrl: b.origin,
    bookUrl: b.bookUrl,
    name: b.name,
    author: b.author ?? "",
    coverUrl: b.coverUrl ?? "",
    intro: b.intro ?? "",
    kind: b.kind ?? "",
    lastChapter: b.lastChapter ?? "",
  });
}
</script>

<template>
  <div>
    <div class="page-head">
      <div>
        <h2 class="page-title">发现</h2>
        <p class="page-sub">按书源的分类页探索新内容</p>
      </div>
    </div>

    <div class="src-row">
      <button
        v-for="s in sources"
        :key="s.sourceUrl"
        class="chip"
        :class="{ selected: s.sourceUrl === activeSource }"
        @click="pickSource(s.sourceUrl)"
      >
        {{ s.sourceName || s.sourceUrl }}
      </button>
      <span v-if="!sources.length" class="none-src">
        没有启用的书源，请到 管理 → 书源管理 导入。
      </span>
    </div>

    <!-- 控件矩阵：按 legado 的 flex 算法排行，每行 6 格 -->
    <div v-if="kinds.length" class="kind-grid">
      <div v-for="(row, ri) in kindRows" :key="ri" class="kind-row-flex">
        <template v-for="cell in row" :key="cell.item.title + ri">
          <!-- 分类按钮 -->
          <button
            v-if="cell.item.type === 'url'"
            class="ktile"
            :class="{ selected: cell.item.url === activeKindUrl }"
            :style="{ flex: `${cell.span} 1 0` }"
            :disabled="!cell.item.url"
            @click="pickKind(cell.item.url ?? '')"
          >
            {{ cell.item.title }}
          </button>

          <!-- 动作按钮（⚙ / 搜索 …） -->
          <button
            v-else-if="cell.item.type === 'button'"
            class="ktile action"
            :style="{ flex: `${cell.span} 1 0` }"
            :disabled="acting"
            @click="runAction(cell.item)"
          >
            {{ cell.item.title }}
          </button>

          <!-- 文本输入：失焦 / 回车时提交值并执行 action -->
          <input
            v-else-if="cell.item.type === 'text'"
            v-model="values[cell.item.title]"
            class="kinput"
            :style="{ flex: `${cell.span} 1 0` }"
            :placeholder="cell.item.title"
            @keyup.enter="runAction(cell.item, values[cell.item.title])"
            @change="runAction(cell.item, values[cell.item.title])"
          />

          <!-- 下拉选择：变化后执行 action -->
          <div
            v-else-if="cell.item.type === 'select'"
            class="ksel-wrap"
            :style="{ flex: `${cell.span} 1 0` }"
          >
            <div class="sel-outer">
              <select
                class="ksel"
                :value="valueOf(cell.item)"
                :disabled="acting"
                @change="
                  onSelectChange(
                    cell.item,
                    ($event.target as HTMLSelectElement).value,
                  )
                "
              >
                <option v-for="c in cell.item.chars ?? []" :key="c" :value="c">
                  {{ c }}
                </option>
              </select>
              <span class="caret" aria-hidden="true"></span>
            </div>
          </div>

          <!-- 开关：点击在 chars 间循环并执行 action -->
          <button
            v-else-if="cell.item.type === 'toggle'"
            class="ktile"
            :style="{ flex: `${cell.span} 1 0` }"
            :disabled="acting"
            @click="toggleKind(cell.item)"
          >
            {{ valueOf(cell.item) }}{{ cell.item.title }}
          </button>

          <!-- 未知类型：按普通分类按钮处理 -->
          <button
            v-else
            class="ktile"
            :style="{ flex: `${cell.span} 1 0` }"
            :disabled="!cell.item.url"
            @click="pickKind(cell.item.url ?? '')"
          >
            {{ cell.item.title }}
          </button>
        </template>
        <span
          v-if="rowSpan(row) < MAX_SPAN"
          class="kind-fill"
          :style="{ flex: `${MAX_SPAN - rowSpan(row)} 1 0` }"
        ></span>
      </div>
    </div>

    <div v-if="loadingKinds" class="center">
      <MiuixProgressIndicator />
    </div>
    <p v-if="error" class="err">{{ error }}</p>

    <details v-if="logs.length" class="logbox">
      <summary>书源日志（{{ logs.length }}）</summary>
      <pre class="mono">{{ logs.join("\n") }}</pre>
    </details>

    <div v-if="books.length" class="cover-grid">
      <button
        v-for="(b, i) in books"
        :key="b.origin + b.bookUrl + i"
        type="button"
        class="ctile"
        @click="openBook(b)"
      >
        <span class="ctile-cover">
          <LoadingImage
            :src="b.coverUrl ? coverProxyUrl(b.coverUrl) : FALLBACK_COVER_SVG"
            @error="onCoverError($event, b.coverUrl)"
          />
        </span>
        <span class="ctile-name">{{ b.name }}</span>
        <span class="ctile-meta">{{ b.author || "佚名" }}</span>
      </button>
    </div>

    <div class="more-row" v-if="activeKindUrl && (books.length || loading)">
      <MiuixButton v-if="!done" :disabled="loading" @click="fetchBooks">
        {{ loading ? "加载中…" : "加载更多" }}
      </MiuixButton>
      <span v-else-if="books.length" class="none-src">没有更多了</span>
    </div>

    <div
      v-if="!loading && !loadingKinds && !error && activeKindUrl && !books.length"
      class="center empty"
    >
      这个分类暂时没有内容。
    </div>

    <div
      v-if="!loadingKinds && kinds.length && !urlKinds.length && !activeKindUrl"
      class="center empty"
    >
      该书源没有可浏览的分类，试试上面的控件。
    </div>

    <SourceLoginDialog
      v-model="showLogin"
      :source-url="activeSource"
      :source-name="activeSourceName"
    />
  </div>
</template>

<style scoped>
.src-row {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  margin-bottom: 14px;
}
.page-head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 12px;
}
.none-src {
  color: var(--m-color-on-background-variant);
  font-size: 13px;
}
/* 控件矩阵：每行 6 格，span 经 flex-grow 分配宽度 */
.kind-grid {
  display: flex;
  flex-direction: column;
  gap: 6px;
  margin-bottom: 14px;
}
.kind-row-flex {
  display: flex;
  gap: 6px;
  align-items: stretch;
}
.ktile {
  min-width: 0;
  border: 1px solid var(--m-color-outline);
  background: var(--m-color-surface-container);
  color: var(--m-color-on-surface);
  border-radius: 10px;
  padding: 7px 10px;
  font-size: 12px;
  cursor: pointer;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.ktile.action {
  font-size: 13px;
}
.ktile.selected {
  background: var(--m-color-secondary-container);
  border-color: transparent;
  color: var(--m-color-on-secondary-container);
  font-weight: 600;
}
.ktile:disabled {
  opacity: 0.55;
  cursor: default;
}
.kind-fill {
  /* 行尾补白，对齐 legado 的 Spacer(6 - totalSpan) */
  pointer-events: none;
}
.kinput {
  min-width: 0;
  border: 1px solid var(--m-color-outline);
  background: transparent;
  color: var(--m-color-on-surface);
  border-radius: 10px;
  padding: 7px 10px;
  font: inherit;
  font-size: 13px;
}
.kinput:focus-visible {
  outline: none;
  border-color: var(--m-color-primary);
  box-shadow: 0 0 0 1px var(--m-color-primary);
}
.ksel-wrap {
  min-width: 0;
  display: flex;
}
.sel-outer {
  position: relative;
  display: inline-flex;
  align-items: center;
  width: 100%;
}
.ksel {
  appearance: none;
  -webkit-appearance: none;
  border: 1px solid var(--m-color-outline);
  border-radius: 10px;
  background: transparent;
  color: var(--m-color-on-surface);
  font: inherit;
  font-size: 12px;
  padding: 7px 26px 7px 10px;
  cursor: pointer;
  width: 100%;
}
.ksel:focus-visible {
  outline: none;
  border-color: var(--m-color-primary);
  box-shadow: 0 0 0 1px var(--m-color-primary);
}
.caret {
  position: absolute;
  right: 10px;
  width: 6px;
  height: 6px;
  border-right: 2px solid var(--m-color-on-surface-secondary);
  border-bottom: 2px solid var(--m-color-on-surface-secondary);
  transform: translateY(-2px) rotate(45deg);
  pointer-events: none;
}
.err {
  color: var(--m-color-error);
  font-size: 13px;
  margin: 6px 0 12px;
}
.logbox {
  margin: 6px 0 12px;
  font-size: 12px;
  color: var(--m-color-on-surface-secondary);
}
.logbox summary {
  cursor: pointer;
}
.mono {
  font-family: Consolas, monospace;
  font-size: 12px;
  background: var(--m-color-surface-container, rgba(127, 127, 127, 0.08));
  border-radius: 8px;
  padding: 8px 10px;
  margin: 6px 0 0;
  white-space: pre-wrap;
  word-break: break-all;
  max-height: 160px;
  overflow: auto;
}
.more-row {
  display: flex;
  justify-content: center;
  margin: 22px 0 6px;
}
</style>
