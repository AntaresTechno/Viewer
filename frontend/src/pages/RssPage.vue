<script setup lang="ts">
import { computed, onMounted, ref } from "vue";
import { MiuixButton, MiuixProgressIndicator } from "miuix-vue";
import { api, errMsg } from "@/api/client";
import type { RssArticle, RssArticleContent, RssSort, RssSource } from "@/api/client";
import { useAuth } from "@/stores/auth";

const auth = useAuth();
const sources = ref<RssSource[]>([]);
const groups = ref<string[]>([]);
const group = ref("");
const activeSource = ref<RssSource | null>(null);
const sorts = ref<RssSort[]>([]);
const activeSort = ref<RssSort | null>(null);
const articles = ref<RssArticle[]>([]);
const loadingSources = ref(true);
const loading = ref(false);
const loadingMore = ref(false);
const error = ref("");
const warning = ref("");
const page = ref(1);
const nextUrl = ref<string | null>(null);
const search = ref("");
const searchMode = ref(false);
const favoritesMode = ref(false);
const reader = ref<RssArticleContent | null>(null);
const readerLoading = ref(false);

const visibleSources = computed(() => sources.value.filter((s) =>
  s.enabled && (!group.value || s.sourceGroup.split(/[,，;；]/).map((x) => x.trim()).includes(group.value)),
));

onMounted(loadSources);

async function loadSources() {
  loadingSources.value = true;
  error.value = "";
  try {
    const data = await api.rssSources();
    sources.value = data.items;
    groups.value = data.groups;
  } catch (e) {
    error.value = errMsg(e);
  } finally {
    loadingSources.value = false;
  }
}

async function selectSource(source: RssSource) {
  if (!source.enabled) return;
  activeSource.value = source;
  favoritesMode.value = false;
  searchMode.value = false;
  search.value = "";
  error.value = "";
  warning.value = "";
  articles.value = [];
  sorts.value = [];
  activeSort.value = null;
  if (source.singleUrl) {
    const data = await api.rssSorts(source.sourceUrl);
    window.open(data.items[0]?.url || source.sourceUrl, "_blank", "noopener,noreferrer");
    return;
  }
  try {
    const data = await api.rssSorts(source.sourceUrl);
    sorts.value = data.items;
    activeSort.value = data.items[0] ?? { name: "", url: source.sourceUrl };
    await loadArticles(true);
  } catch (e) {
    error.value = errMsg(e);
  }
}

async function selectSort(sort: RssSort) {
  activeSort.value = sort;
  searchMode.value = false;
  search.value = "";
  await loadArticles(true);
}

async function loadArticles(reset = false) {
  const source = activeSource.value;
  const sort = activeSort.value;
  if (!source || (!sort && !searchMode.value)) return;
  if (reset) {
    page.value = 1;
    nextUrl.value = null;
    articles.value = [];
    loading.value = true;
  } else {
    loadingMore.value = true;
  }
  error.value = "";
  warning.value = "";
  try {
    const data = await api.rssArticles({
      sourceUrl: source.sourceUrl,
      sortName: searchMode.value ? "搜索" : (sort?.name ?? ""),
      sortUrl: page.value > 1 && nextUrl.value ? nextUrl.value : (sort?.url ?? ""),
      page: page.value,
      searchKey: searchMode.value ? search.value.trim() : "",
    });
    const known = new Set(articles.value.map((a) => a.id));
    articles.value.push(...data.items.filter((a) => !known.has(a.id)));
    nextUrl.value = data.nextUrl;
    warning.value = data.warning;
    page.value += 1;
  } catch (e) {
    error.value = errMsg(e);
  } finally {
    loading.value = false;
    loadingMore.value = false;
  }
}

async function runSearch() {
  if (!activeSource.value?.hasSearch || !search.value.trim()) return;
  searchMode.value = true;
  await loadArticles(true);
}

async function showFavorites() {
  favoritesMode.value = true;
  searchMode.value = false;
  activeSource.value = null;
  activeSort.value = null;
  loading.value = true;
  error.value = "";
  try {
    articles.value = (await api.rssFavorites()).items;
  } catch (e) {
    error.value = errMsg(e);
  } finally {
    loading.value = false;
  }
}

async function openArticle(article: RssArticle) {
  readerLoading.value = true;
  article.read = true;
  try {
    reader.value = await api.rssArticleContent(article.id);
  } catch (e) {
    error.value = errMsg(e);
  } finally {
    readerLoading.value = false;
  }
}

async function toggleFavorite(article: RssArticle, event?: Event) {
  event?.stopPropagation();
  try {
    article.favorite = (await api.rssToggleFavorite(article.id)).favorite;
    if (favoritesMode.value && !article.favorite) {
      articles.value = articles.value.filter((a) => a.id !== article.id);
    }
    if (reader.value?.id === article.id) reader.value.favorite = article.favorite;
  } catch (e) {
    error.value = errMsg(e);
  }
}

function formatDate(value: string) {
  if (!value) return "";
  const d = new Date(value);
  return Number.isNaN(d.getTime()) ? value : new Intl.DateTimeFormat("zh-CN", {
    month: "short", day: "numeric", hour: "2-digit", minute: "2-digit",
  }).format(d);
}

function closeReader() {
  reader.value = null;
}
</script>

<template>
  <div class="rss-page">
    <div class="page-head head">
      <div>
        <h2 class="page-title">订阅</h2>
        <p class="page-sub">聚合 RSS、Atom 与 Legado 规则订阅</p>
      </div>
      <div class="head-actions">
        <MiuixButton v-if="auth.can('rss.favorite')" @click="showFavorites">收藏</MiuixButton>
        <MiuixButton v-if="auth.can('rss.manage')" @click="$router.push('/admin/rss-sources')">管理订阅源</MiuixButton>
      </div>
    </div>

    <div v-if="groups.length" class="group-row">
      <button class="chip" :class="{ selected: !group }" @click="group = ''">全部</button>
      <button v-for="g in groups" :key="g" class="chip" :class="{ selected: group === g }" @click="group = g">{{ g }}</button>
    </div>

    <div v-if="loadingSources" class="center"><MiuixProgressIndicator /></div>
    <div v-else-if="!visibleSources.length && !favoritesMode" class="empty-state">
      <div class="empty-icon">⌁</div>
      <h3>还没有可用的订阅源</h3>
      <p>导入 Legado 订阅源 JSON 后，文章会出现在这里。</p>
      <MiuixButton v-if="auth.can('rss.manage')" type="primary" @click="$router.push('/admin/rss-sources')">导入订阅源</MiuixButton>
    </div>

    <template v-else>
      <div class="source-strip" aria-label="订阅源">
        <button
          v-for="source in visibleSources" :key="source.id"
          class="source-pill" :class="{ active: source.id === activeSource?.id }"
          @click="selectSource(source)"
        >
          <img v-if="source.sourceIcon" :src="source.sourceIcon" alt="" @error="($event.target as HTMLImageElement).style.display='none'" />
          <span v-else class="source-mark">{{ (source.sourceName || 'R').slice(0, 1) }}</span>
          <span>{{ source.sourceName || source.sourceUrl }}</span>
        </button>
      </div>

      <div v-if="activeSource" class="feed-tools">
        <div class="sort-row">
          <button
            v-for="sort in sorts" :key="sort.name + sort.url"
            class="chip" :class="{ selected: !searchMode && activeSort?.url === sort.url }"
            @click="selectSort(sort)"
          >{{ sort.name || "最新" }}</button>
        </div>
        <form v-if="activeSource.hasSearch" class="feed-search" @submit.prevent="runSearch">
          <input v-model="search" placeholder="搜索此订阅源" aria-label="搜索此订阅源" />
          <button type="submit">搜索</button>
        </form>
      </div>

      <p v-if="warning" class="warning">当前显示缓存内容：{{ warning }}</p>
      <p v-if="error" class="err error-box">{{ error }}</p>
      <div v-if="loading" class="center"><MiuixProgressIndicator /></div>

      <div v-else-if="!activeSource && !favoritesMode" class="empty-state compact">
        <h3>选择一个订阅源</h3>
        <p>点击上方的订阅源标签加载文章。</p>
      </div>

      <div v-else-if="!articles.length" class="empty-state compact">
        <h3>{{ favoritesMode ? "还没有收藏文章" : "这里暂时没有文章" }}</h3>
        <p>{{ favoritesMode ? "阅读文章时点一下星标即可收藏。" : "可以刷新，或切换其他分类。" }}</p>
      </div>

      <div v-else class="article-list">
        <article
          v-for="article in articles" :key="article.id"
          class="article-card" :class="{ read: article.read }" tabindex="0"
          @click="openArticle(article)" @keyup.enter="openArticle(article)"
        >
          <img v-if="article.image" class="article-image" :src="article.image" alt="" loading="lazy" />
          <div class="article-main">
            <div class="article-meta">
              <span>{{ favoritesMode ? sources.find((s) => s.sourceUrl === article.origin)?.sourceName : (activeSource?.sourceName || article.sort) }}</span>
              <time>{{ formatDate(article.pubDate) }}</time>
            </div>
            <h3>{{ article.title }}</h3>
            <p v-if="article.description">{{ article.description }}</p>
          </div>
          <button v-if="auth.can('rss.favorite')" class="star" :class="{ on: article.favorite }" :aria-label="article.favorite ? '取消收藏' : '收藏'" @click="toggleFavorite(article, $event)">
            {{ article.favorite ? "★" : "☆" }}
          </button>
        </article>
      </div>

      <div v-if="nextUrl && !favoritesMode" class="load-more">
        <MiuixButton :disabled="loadingMore" @click="loadArticles(false)">
          {{ loadingMore ? "加载中…" : "加载更多" }}
        </MiuixButton>
      </div>
    </template>

    <div v-if="readerLoading" class="reader-loading"><MiuixProgressIndicator /></div>
    <Teleport to="body">
      <div v-if="reader" class="reader-backdrop" @click.self="closeReader">
        <article class="rss-reader" role="dialog" aria-modal="true" :aria-label="reader.title">
          <header>
            <button class="reader-close" aria-label="关闭" @click="closeReader">←</button>
            <div class="reader-title"><h2>{{ reader.title }}</h2><p>{{ formatDate(reader.pubDate) }}</p></div>
            <button v-if="auth.can('rss.favorite')" class="reader-star" :class="{ on: reader.favorite }" @click="toggleFavorite(reader)">{{ reader.favorite ? "★" : "☆" }}</button>
          </header>
          <div class="reader-content" v-html="reader.content"></div>
          <footer><a :href="reader.link" target="_blank" rel="noopener noreferrer">在原网页中打开 ↗</a></footer>
        </article>
      </div>
    </Teleport>
  </div>
</template>

<style scoped>
.head { display:flex; justify-content:space-between; gap:20px; align-items:flex-start; }
.head-actions,.group-row,.sort-row { display:flex; flex-wrap:wrap; gap:8px; }
.group-row { margin-bottom:14px; }
.source-strip { display:flex; gap:10px; overflow-x:auto; padding:2px 2px 14px; scrollbar-width:thin; }
.source-pill { flex:none; display:flex; align-items:center; gap:9px; min-height:44px; max-width:240px; padding:6px 14px 6px 7px; border:1px solid var(--m-color-outline); border-radius:999px; background:var(--m-color-surface); color:var(--m-color-on-surface); font:inherit; cursor:pointer; }
.source-pill.active { border-color:transparent; background:var(--m-color-primary-container); color:var(--m-color-on-primary-container); }
.source-pill img,.source-mark { width:30px; height:30px; border-radius:50%; object-fit:cover; flex:none; }
.source-mark { display:grid; place-items:center; background:var(--m-color-secondary-container); font-weight:700; }
.source-pill span:last-child { overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
.feed-tools { display:flex; justify-content:space-between; align-items:center; gap:14px; margin:4px 0 16px; }
.feed-search { display:flex; min-width:240px; border:1px solid var(--m-color-outline); border-radius:999px; overflow:hidden; }
.feed-search input { min-width:0; flex:1; border:0; outline:0; padding:9px 13px; color:var(--m-color-on-surface); background:transparent; font:inherit; }
.feed-search button { border:0; padding:0 14px; background:var(--m-color-secondary-container); color:var(--m-color-on-secondary-container); cursor:pointer; }
.warning,.error-box { padding:10px 14px; border-radius:12px; background:var(--m-color-surface-container); }
.warning { color:var(--m-color-on-surface-secondary); font-size:13px; }
.article-list { display:grid; gap:10px; }
.article-card { position:relative; display:flex; gap:16px; min-height:118px; padding:16px; border:1px solid var(--m-color-divider-line); border-radius:var(--app-shape-lg,20px); background:var(--m-color-surface); cursor:pointer; outline:none; transition:background .15s ease, transform .15s ease; }
.article-card:hover,.article-card:focus-visible { background:var(--m-color-surface-container); }
.article-card:active { transform:scale(.992); }
.article-card.read { opacity:.68; }
.article-image { width:132px; height:92px; border-radius:12px; object-fit:cover; flex:none; background:var(--m-color-surface-container); }
.article-main { min-width:0; flex:1; }
.article-meta { display:flex; gap:12px; color:var(--m-color-on-surface-secondary); font-size:12px; }
.article-main h3 { margin:7px 36px 6px 0; font-size:17px; line-height:1.35; color:var(--m-color-on-surface); }
.article-main p { margin:0; display:-webkit-box; -webkit-line-clamp:2; -webkit-box-orient:vertical; overflow:hidden; color:var(--m-color-on-surface-secondary); font-size:14px; line-height:1.55; }
.star { position:absolute; right:13px; top:12px; border:0; background:transparent; color:var(--m-color-on-surface-secondary); font-size:23px; cursor:pointer; }
.star.on,.reader-star.on { color:var(--m-color-primary); }
.load-more { display:flex; justify-content:center; padding:20px; }
.empty-state { min-height:320px; display:grid; place-content:center; justify-items:center; text-align:center; color:var(--m-color-on-surface-secondary); }
.empty-state.compact { min-height:220px; }.empty-state h3 { color:var(--m-color-on-surface); margin:8px 0 0; }.empty-icon { font-size:52px; color:var(--m-color-primary); }
.reader-loading { position:fixed; inset:0; z-index:1200; display:grid; place-items:center; background:rgba(0,0,0,.18); }
.reader-backdrop { position:fixed; inset:0; z-index:1100; display:flex; justify-content:flex-end; background:rgba(0,0,0,.36); backdrop-filter:blur(3px); }
.rss-reader { width:min(760px,100%); height:100%; overflow:auto; background:var(--m-color-background); color:var(--m-color-on-background); box-shadow:-10px 0 40px rgba(0,0,0,.18); }
.rss-reader header { position:sticky; top:0; z-index:2; display:flex; align-items:center; gap:14px; padding:16px 22px; background:color-mix(in srgb,var(--m-color-background) 88%,transparent); backdrop-filter:blur(18px); border-bottom:1px solid var(--m-color-divider-line); }
.reader-close,.reader-star { flex:none; width:42px; height:42px; border:0; border-radius:50%; background:var(--m-color-surface-container); color:var(--m-color-on-surface); cursor:pointer; font-size:20px; }
.reader-title { min-width:0; flex:1; }.reader-title h2 { margin:0; font-size:18px; white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }.reader-title p { margin:3px 0 0; color:var(--m-color-on-surface-secondary); font-size:12px; }
.reader-content { max-width:680px; margin:0 auto; padding:38px 28px; font-size:17px; line-height:1.85; overflow-wrap:anywhere; }
.reader-content :deep(img) { max-width:100%; height:auto; border-radius:10px; }.reader-content :deep(a) { color:var(--m-color-primary); }.reader-content :deep(pre) { overflow:auto; }
.rss-reader footer { max-width:680px; margin:0 auto; padding:0 28px 40px; }.rss-reader footer a { color:var(--m-color-primary); }
@media (max-width:680px) { .head { align-items:stretch; flex-direction:column; }.feed-tools { align-items:stretch; flex-direction:column; }.feed-search { width:100%; }.article-image { width:88px; height:88px; }.article-card { padding:13px; gap:12px; }.article-main h3 { font-size:16px; }.rss-reader header { padding:10px 12px; }.reader-content { padding:26px 19px; font-size:16px; } }
</style>
