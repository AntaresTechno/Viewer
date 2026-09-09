<script setup lang="ts">
import { onMounted, ref, watch } from "vue";
import { useRoute, useRouter } from "vue-router";
import { MiuixButton, MiuixDialog, MiuixProgressIndicator } from "miuix-vue";
import { api, errMsg } from "@/api/client";
import type { MediaKind, MediaLibraryItem } from "@/api/client";
import MediaCoverCard from "@/components/media/MediaCoverCard.vue";

const props = defineProps<{ kind: string }>();
const router = useRouter();
const route = useRoute();

const ACTIVE: MediaKind[] = ["comic", "audio", "video"];
const kind = ref<MediaKind>(
  ACTIVE.includes(props.kind as MediaKind) ? (props.kind as MediaKind) : "video",
);

const items = ref<MediaLibraryItem[]>([]);
const loading = ref(true);
const error = ref("");
const sort = ref<"added" | "updated" | "viewed">("added");
const order = ref<"desc" | "asc">("desc");

const SORTS: { key: "added" | "updated" | "viewed"; label: string }[] = [
  { key: "added", label: "加入时间" },
  { key: "updated", label: "最近更新" },
  { key: "viewed", label: "最后阅读" },
];

onMounted(load);
watch(() => props.kind, () => { kind.value = ACTIVE.includes(props.kind as MediaKind) ? (props.kind as MediaKind) : "video"; load(); });
watch(() => route.query.sort, () => { const s = route.query.sort as string; if ((SORTS as {key:string;label:string}[]).some(x=>x.key===s)) sort.value = s as typeof sort.value; });
watch([sort, order], () => void load());

const KIND_LABEL: Record<MediaKind, string> = { comic: "漫画", audio: "音频", video: "视频" };

async function load() {
  try {
    items.value = await api.mediaLibrary({ kind: kind.value, sort: sort.value, order: order.value });
    error.value = "";
  } catch (e) {
    error.value = errMsg(e);
  } finally {
    loading.value = false;
  }
}

const delTarget = ref<MediaLibraryItem | null>(null);
const notice = ref("");
async function doRemove() {
  const t = delTarget.value;
  if (!t) return;
  try {
    await api.mediaLibraryDelete(t.id);
    delTarget.value = null;
    await load();
  } catch (e) {
    notice.value = errMsg(e);
    delTarget.value = null;
  }
}

function open(it: MediaLibraryItem) {
  if (it.mediaKind === "comic") router.push(`/media/comic-read/${it.id}`);
  else router.push(`/media/play/${it.id}`);
}
function detail(it: MediaLibraryItem) {
  router.push(`/media/detail/${it.id}`);
}
function tagSwitch(k: string) {
  router.push(`/media/${k}`);
}
</script>

<template>
  <div class="media-lib">
    <div class="page-head">
      <div>
        <h2 class="page-title">{{ KIND_LABEL[kind] }}</h2>
        <p v-if="items.length" class="page-sub">{{ items.length }} 部 · 点击封面继续</p>
      </div>
      <MiuixButton @click="$router.push('/media/discover')">去发现添加</MiuixButton>
    </div>

    <div class="sort-row">
      <div class="sort-keys" role="tablist" aria-label="分类">
        <button
          v-for="k in ACTIVE"
          :key="k"
          type="button"
          role="tab"
          class="chip small sort-chip"
          :class="{ selected: kind === k }"
          @click="tagSwitch(k)"
        >{{ KIND_LABEL[k] }}</button>
      </div>
      <div class="sort-keys right">
        <button
          v-for="s in SORTS"
          :key="s.key"
          type="button"
          class="chip small sort-chip"
          :class="{ selected: sort === s.key }"
          @click="sort = s.key"
        >{{ s.label }}</button>
        <button type="button" class="chip small sort-chip dir-chip" @click="order = order === 'asc' ? 'desc' : 'asc'">
          {{ order === "asc" ? "正序" : "倒序" }}
        </button>
      </div>
    </div>

    <div v-if="loading" class="center"><MiuixProgressIndicator /></div>
    <div v-else-if="error" class="center err">{{ error }}</div>
    <div v-else-if="!items.length" class="empty-state">
      <span class="es-title">这里还没有{{ KIND_LABEL[kind] }}</span>
      <span>去媒体发现页浏览并收藏 {{ KIND_LABEL[kind] }}。</span>
      <span class="es-act"><MiuixButton @click="$router.push('/media/discover')">去发现</MiuixButton></span>
    </div>
    <div v-else class="cover-grid">
      <MediaCoverCard
        v-for="it in items"
        :key="it.id"
        :item="it"
        mode="library"
        @open="open(it)"
        @read="open(it)"
        @detail="detail(it)"
        @remove="(delTarget = it)"
      />
    </div>

    <MiuixDialog :model-value="!!delTarget" title="移出媒体库？"
      @update:model-value="(v:boolean)=>{ if(!v) delTarget=null }">
      <div class="dlg"><p class="dlg-text">将把<b>《{{ delTarget?.title }}》</b>从媒体库移出，阅览进度不再保留。</p></div>
      <div class="dlg-actions">
        <MiuixButton @click="delTarget = null">取消</MiuixButton>
        <MiuixButton class="del-btn" @click="doRemove">移出</MiuixButton>
      </div>
    </MiuixDialog>
    <MiuixDialog :model-value="!!notice" title="提示" @update:model-value="(v:boolean)=>{ if(!v) notice='' }">
      <div class="dlg"><p class="dlg-text">{{ notice }}</p></div>
      <div class="dlg-actions"><MiuixButton type="primary" @click="notice=''">我知道了</MiuixButton></div>
    </MiuixDialog>
  </div>
</template>

<style scoped>
.sort-row { display:flex; flex-wrap:wrap; align-items:center; gap:8px; margin:-6px 0 16px; }
.sort-keys { display:flex; flex-wrap:wrap; gap:8px; }
.sort-keys.right { margin-left:auto; }
.sort-chip { padding:5px 13px; font-size:12px; }
</style>