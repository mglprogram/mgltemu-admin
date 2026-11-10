<!--
  修改库存 · 预售（用户 2026-11-09 要求：TEMU 运营下的子节点）

  两个页签：
    预售中   —— 正在预售的「款号-尺码」（SPU / 补货日 / 在途量 / 开预售时间 / 剩余天数）
    已过预售 —— 开满 7 天自动进来的记录；同一 SKU 满 7 天可重新开预售 ⇒ 该行标「可清除」
  一级筛选：TEMU / SHEIN（平台），二级：店铺 BEL / BHY

  数据：public/data/presale-watch.json
    （scripts/goods/build_presale_watch.py 由开预售台账 data/presale-ledger.json 生成）
-->
<template>
  <div class="pw-page app-container">
    <div class="pw-header">
      <div>
        <h2 class="pw-header__title">修改库存 · 预售</h2>
        <p class="pw-header__desc">
          开预售满 {{ data.presaleDays }} 天自动进入「已过预售」；平台每 SKU
          {{ data.presaleDays }} 天内只允许开启一次，满期后该记录即「可清除」。
        </p>
      </div>
      <div class="pw-header__meta">
        <el-tag v-if="data.generatedAt" type="info" effect="plain" size="small">
          数据时间 {{ data.generatedAt }}
        </el-tag>
        <el-button :loading="loading" @click="load">
          <el-icon><Refresh /></el-icon><span>刷新</span>
        </el-button>
      </div>
    </div>

    <el-alert
      v-if="loadError"
      type="error"
      :closable="false"
      show-icon
      class="pw-alert"
      :title="loadError"
      description="先跑 python scripts/goods/build_presale_watch.py 生成 presale-watch.json"
    />

    <!--
      筛选分两行（用户 2026-11-10 要求：店铺按钮不要和 TEMU/SHEIN 并排）
        第一行：平台 TEMU / SHEIN
        第二行：店铺 全部 / BEL / BHY / BK  +  状态 预售中 / 已过预售  +  搜索
    -->
    <div class="pw-switch pw-switch--platform">
      <el-radio-group v-model="platform" size="default">
        <el-radio-button v-for="p in platformOptions" :key="p" :value="p">
          {{ p }}
          <span class="pw-switch__badge">{{ platformCount(p) }}</span>
        </el-radio-button>
      </el-radio-group>
    </div>

    <div class="pw-switch pw-switch--sub">
      <el-radio-group v-model="store" size="default">
        <el-radio-button value="">
          全部
          <span class="pw-switch__badge">{{ storeCount("") }}</span>
        </el-radio-button>
        <el-radio-button v-for="s in storeOptions" :key="s" :value="s">
          {{ s }} 店
          <span class="pw-switch__badge">{{ storeCount(s) }}</span>
        </el-radio-button>
      </el-radio-group>

      <el-radio-group v-model="status" size="default">
        <el-radio-button value="预售中">
          预售中 <span class="pw-switch__badge">{{ countOf("预售中") }}</span>
        </el-radio-button>
        <el-radio-button value="已过预售">
          已过预售 <span class="pw-switch__badge">{{ countOf("已过预售") }}</span>
        </el-radio-button>
      </el-radio-group>

      <el-input
        v-model="keyword"
        placeholder="SPU / 货号"
        clearable
        size="default"
        class="pw-switch__search"
      >
        <template #prefix><el-icon><Search /></el-icon></template>
      </el-input>
    </div>

    <el-row :gutter="12" class="pw-stats">
      <el-col v-for="s in stats" :key="s.label" :xs="12" :md="6">
        <el-card shadow="never" :body-style="{ padding: '14px 16px' }">
          <div class="pw-stat__label">{{ s.label }}</div>
          <div class="pw-stat__value" :style="{ color: s.color }">{{ s.value }}</div>
        </el-card>
      </el-col>
    </el-row>

    <el-table :data="rows" v-loading="loading" border stripe size="small" class="pw-table">
      <el-table-column label="平台" prop="platform" width="80" />
      <el-table-column label="店铺" prop="store" width="70" />
      <el-table-column label="货号（款号）" prop="code" min-width="180" />
      <el-table-column label="尺码" prop="size" width="70" />
      <el-table-column label="SPU ID" prop="spu" width="120" />
      <el-table-column label="在途量" prop="qty" width="80" align="right" />
      <el-table-column label="补货日期" prop="date" width="110" />
      <el-table-column label="开预售时间" prop="openedAt" width="160" />
      <el-table-column label="已开天数" width="90" align="right">
        <template #default="{ row }">{{ row.ageDays ?? "—" }}</template>
      </el-table-column>
      <el-table-column label="TEMU 库存" prop="temuStock" width="100" align="right" />
      <el-table-column label="7 天日均" width="90" align="right">
        <template #default="{ row }">{{ row.daily ? row.daily.toFixed(2) : "—" }}</template>
      </el-table-column>
      <el-table-column label="状态" width="100">
        <template #default="{ row }">
          <el-tag :type="row.status === '预售中' ? 'success' : 'info'" size="small">{{ row.status }}</el-tag>
        </template>
      </el-table-column>
      <el-table-column label="可清除" width="90" align="center">
        <template #default="{ row }">
          <el-tag v-if="row.clearable" type="warning" size="small">可清除</el-tag>
          <span v-else>—</span>
        </template>
      </el-table-column>
      <el-table-column label="商品标题" prop="title" min-width="260" show-overflow-tooltip />
    </el-table>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from "vue";
import { Refresh, Search } from "@element-plus/icons-vue";
import PresaleWatchAPI, { type PresaleWatchData, type PresaleWatchItem } from "@/api/temu/presale-watch";

defineOptions({ name: "TemuPresaleWatch" });

const data = ref<PresaleWatchData>({
  generatedAt: "",
  presaleDays: 7,
  rule: { note: "", openedFrom: "" },
  counts: {},
  platforms: ["TEMU", "SHEIN"],
  stores: [],
  items: [],
});
const loading = ref(false);
const loadError = ref("");
const platform = ref("TEMU");
const store = ref("");
const status = ref<"预售中" | "已过预售">("预售中");
const keyword = ref("");

const platformOptions = computed(() => data.value.platforms?.length ? data.value.platforms : ["TEMU", "SHEIN"]);

/**
 * 店铺筛选（用户 2026-11-10 要求：TEMU 下面加 全部 / BEL / BHY / BK，样式同「在售款明细」）
 * 固定三家 + 数据里出现过的其它店，避免某个店暂时没数据按钮就消失。
 * BK = BEBUTY（第三家 TEMU 店）；如果 BK 还没进在售款明细，按钮照样在（条数 0）。
 */
const BASE_STORES = ["BEL", "BHY", "BK"];
const storeOptions = computed(() => {
  const fromData = data.value.stores ?? [];
  const extra = fromData.filter((s) => !BASE_STORES.includes(s));
  return [...BASE_STORES, ...extra];
});

const base = computed<PresaleWatchItem[]>(() => data.value.items ?? []);

function matchScope(x: PresaleWatchItem) {
  if (x.platform !== platform.value) return false;
  if (store.value && x.store !== store.value) return false;
  const k = keyword.value.trim().toLowerCase();
  if (k && !`${x.spu ?? ""} ${x.code}`.toLowerCase().includes(k)) return false;
  return true;
}

function platformCount(p: string) {
  return base.value.filter((x) => x.platform === p).length;
}

/** 店铺按钮上的条数：只看当前平台 + 当前搜索词 + 当前页签状态 */
function storeCount(s: string) {
  return base.value.filter(
    (x) => x.platform === platform.value && (!s || x.store === s) && x.status === status.value,
  ).length;
}

function countOf(s: "预售中" | "已过预售") {
  return base.value.filter((x) => x.status === s && matchScope(x)).length;
}

const rows = computed(() => base.value.filter((x) => x.status === status.value && matchScope(x)));

const stats = computed(() => {
  const scoped = base.value.filter(matchScope);
  return [
    { label: "预售中（尺码）", value: scoped.filter((x) => x.status === "预售中").length, color: "#67c23a" },
    { label: "已过预售", value: scoped.filter((x) => x.status === "已过预售").length, color: "#909399" },
    { label: "可清除（满 7 天可重开）", value: scoped.filter((x) => x.clearable).length, color: "#e6a23c" },
    { label: "涉及款数", value: new Set(scoped.map((x) => x.code)).size, color: "" },
  ];
});

async function load() {
  loading.value = true;
  loadError.value = "";
  try {
    data.value = await PresaleWatchAPI.getPresaleWatchData();
  } catch (e: any) {
    loadError.value = e?.message || "加载 presale-watch.json 失败";
  } finally {
    loading.value = false;
  }
}

onMounted(load);
</script>

<style scoped>
.pw-page { padding-bottom: 32px; }
.pw-header { display: flex; justify-content: space-between; align-items: flex-start; margin-bottom: 12px; }
.pw-header__title { margin: 0; font-size: 20px; }
.pw-header__desc { margin: 6px 0 0; color: var(--el-text-color-secondary); font-size: 13px; }
.pw-header__meta { display: flex; gap: 8px; align-items: center; }
.pw-alert { margin-bottom: 12px; }
.pw-switch { display: flex; flex-wrap: wrap; gap: 12px; align-items: center; }
/* 第一行只有 TEMU / SHEIN，第二行放店铺+状态+搜索（用户 2026-11-10 要求分两行） */
.pw-switch--platform { margin-bottom: 8px; }
.pw-switch--sub { margin-bottom: 12px; }
.pw-switch__badge { margin-left: 6px; opacity: 0.75; }
.pw-switch__search { width: 220px; }
.pw-stats { margin-bottom: 12px; }
.pw-stat__label { color: var(--el-text-color-secondary); font-size: 13px; }
.pw-stat__value { font-size: 24px; font-weight: 600; margin-top: 4px; }
</style>
