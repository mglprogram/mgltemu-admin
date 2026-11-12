<!--
  修改库存 · 预售 / 清零（用户 2026-11-09 要求：TEMU 运营下的子节点）

  TEMU 两个页签（SHEIN 没有预售，只有清零）：
    预售中   —— 正在预售的「款号-尺码」（SPU / 补货日 / 在途量 / 开预售时间 / 剩余天数）
    已过预售 —— 开满 7 天自动进来的记录；同一 SKU 满 7 天可重新开预售 ⇒ 该行标「可清除」
  一级筛选：TEMU / SHEIN（平台）
     TEMU  → 店铺 BEL / BHY / BK
     SHEIN → 店铺 BS / BD / BAN / BE，状态页签隐藏（用户 2026-11-12：SHEIN 不存在预售，只有清零）

  数据：public/data/presale-watch.json
    （scripts/goods/build_presale_watch.py 由开预售台账 data/presale-ledger.json
      + SHEIN 清零台账 data/shein-clear-ledger.json 生成）
-->
<template>
  <div class="pw-page app-container">
    <div class="pw-header">
      <div>
        <h2 class="pw-header__title">{{ isShein ? "修改库存 · 清零" : "修改库存 · 预售" }}</h2>
        <p class="pw-header__desc">
          <template v-if="isShein">
            SHEIN 半托管没有预售，只有清零：可售天数（仓内 ÷ 7 天日均）≤ 1 天 → 线上库存
            OVERWRITE 0。本页只列**现在线上仍为 0** 的「款号-变体-尺码」；已经补回去的
            （线上现值 > 0）由每轮 pipeline 对账后移出本页。「仓内可用」是清零当时的快照。
          </template>
          <template v-else>
            开预售满 {{ data.presaleDays }} 天自动进入「已过预售」；平台每 SKU
            {{ data.presaleDays }} 天内只允许开启一次，满期后该记录即「可清除」。
          </template>
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
        第二行：店铺 全部 / (TEMU) BEL BHY BK (SHEIN) BS BD BAN BE  +  状态（仅 TEMU）  +  搜索
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
          {{ isShein ? s : s + " 店" }}
          <span class="pw-switch__badge">{{ storeCount(s) }}</span>
        </el-radio-button>
      </el-radio-group>

      <el-radio-group v-if="!isShein" v-model="status" size="default">
        <el-radio-button value="预售中">
          预售中 <span class="pw-switch__badge">{{ countOf("预售中") }}</span>
        </el-radio-button>
        <el-radio-button value="已过预售">
          已过预售 <span class="pw-switch__badge">{{ countOf("已过预售") }}</span>
        </el-radio-button>
      </el-radio-group>

      <el-input
        v-model="keyword"
        :placeholder="isShein ? '款号 / 变体 / 商家SKU' : 'SPU / 货号'"
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

    <!-- SHEIN：清零明细（款号-变体-尺码） -->
    <el-table
      v-if="isShein"
      :data="rows"
      v-loading="loading"
      border
      stripe
      size="small"
      class="pw-table"
    >
      <el-table-column label="店铺" prop="store" width="70" />
      <el-table-column label="货号（款号）" prop="code" min-width="120" />
      <el-table-column label="变体" prop="variant" width="90" />
      <el-table-column label="尺码" prop="size" width="66" />
      <el-table-column label="商家SKU" prop="seller" min-width="190" show-overflow-tooltip />
      <el-table-column label="平台SKU" prop="sku" width="130" />
      <el-table-column label="清零前线上" width="100" align="right">
        <template #default="{ row }">{{ row.before ?? "—" }}</template>
      </el-table-column>
      <el-table-column label="线上现值" width="100" align="right">
        <template #default="{ row }">
          <span :style="{ color: (row.onlineNow ?? 0) > 0 ? '#67c23a' : undefined }">
            {{ row.onlineNow ?? "—" }}
          </span>
        </template>
      </el-table-column>
      <el-table-column label="仓内可用" width="90" align="right">
        <template #default="{ row }">{{ row.total ?? "—" }}</template>
      </el-table-column>
      <el-table-column label="7 天日均" width="90" align="right">
        <template #default="{ row }">{{ row.daily ? row.daily.toFixed(2) : "—" }}</template>
      </el-table-column>
      <el-table-column label="可售天数" width="90" align="right">
        <template #default="{ row }">{{ daysText(row.days) }}</template>
      </el-table-column>
      <el-table-column label="首次清零" prop="firstClearedAt" width="155" />
      <el-table-column label="最近清零" prop="clearedAt" width="155" />
      <el-table-column label="次数" width="66" align="right">
        <template #default="{ row }">{{ row.times ?? 1 }}</template>
      </el-table-column>
      <el-table-column label="状态" width="96">
        <template #default="{ row }">
          <el-tag :type="statusTagType(row)" size="small">{{ row.status }}</el-tag>
        </template>
      </el-table-column>
    </el-table>

    <!-- TEMU：预售明细 -->
    <el-table
      v-else
      :data="rows"
      v-loading="loading"
      border
      stripe
      size="small"
      class="pw-table"
    >
      <el-table-column label="平台" prop="platform" width="80" />
      <el-table-column label="店铺" prop="store" width="70" />
      <el-table-column label="货号（款号）" prop="code" min-width="180" />
      <el-table-column label="尺码" prop="size" width="70" />
      <el-table-column label="SPU ID" prop="spu" width="120" />
      <el-table-column label="在途量" width="90" align="right">
        <template #default="{ row }">
          <el-tooltip placement="top" content="领星 LM739 的真实在途件数（不是预售填进去的量）">
            <span>{{ row.transit ?? "—" }}</span>
          </el-tooltip>
        </template>
      </el-table-column>
      <el-table-column label="在途预售库存" width="120" align="right">
        <template #default="{ row }">
          <el-tooltip placement="top" content="开预售时填进弹窗的「在途预售库存」">
            <span>{{ row.qty ?? "—" }}</span>
          </el-tooltip>
        </template>
      </el-table-column>
      <el-table-column label="补货日期" prop="date" width="110" />
      <el-table-column label="开预售时间" prop="openedAt" width="160" />
      <el-table-column label="已开天数" width="90" align="right">
        <template #default="{ row }">{{ row.ageDays ?? "—" }}</template>
      </el-table-column>
      <el-table-column label="TEMU 现货库存" width="120" align="right">
        <template #default="{ row }">
          <el-tooltip
            placement="top"
            :content="`商品列表显示 ${row.temuStockRaw ?? '—'}（含在途预售 ${row.qty ?? 0}）→ 剔除预售后的现货 ${row.temuStockExPresale ?? '—'}`"
          >
            <span>{{ row.temuStockExPresale ?? "—" }}</span>
          </el-tooltip>
        </template>
      </el-table-column>
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
import { computed, onMounted, ref, watch } from "vue";
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

const isShein = computed(() => platform.value === "SHEIN");
const platformOptions = computed(() => data.value.platforms?.length ? data.value.platforms : ["TEMU", "SHEIN"]);

/**
 * 店铺筛选（用户 2026-11-10：TEMU 加 全部 / BEL / BHY / BK）
 * SHEIN 固定 BS / BD / BAN / BE（用户 2026-11-12 指定），不随数据有无而消失。
 */
const BASE_STORES = ["BEL", "BHY", "BK"];
const SHEIN_STORES = ["BS", "BD", "BAN", "BE"];
const storeOptions = computed(() => {
  if (isShein.value) {
    const fromData = data.value.sheinStores ?? [];
    const extra = (data.value.stores ?? []).filter(
      (s) => !SHEIN_STORES.includes(s) && (data.value.items ?? []).some((x) => x.platform === "SHEIN" && x.store === s),
    );
    return [...(fromData.length ? fromData : SHEIN_STORES), ...extra];
  }
  // ⚠ 2026-11-12 用户反馈：TEMU 页签下出现了 SHEIN 的 BAN店/BD店 按钮（都是 0）。
  //   原因：这里用了全局 data.stores，而它混了 SHEIN 主体。
  //   ⇒ 只从**当前平台**的 items 里取店铺，TEMU 就是 BEL/BHY/BK + 数据里真出现的 TEMU 店。
  const temuInData = new Set(
    (data.value.items ?? []).filter((x) => x.platform === "TEMU").map((x) => x.store),
  );
  const extra = [...temuInData].filter((s) => s && !BASE_STORES.includes(s));
  return [...BASE_STORES, ...extra];
});

// 切平台时，如果当前选中的店铺不属于新平台（比如 TEMU 选了 BAN），自动回到「全部」
watch([platform, storeOptions], () => {
  if (store.value && !storeOptions.value.includes(store.value)) store.value = "";
});

/**
 * 已开天数 / 状态在前端**按当前时间实时算**。
 * ⚠ 2026-11-12 用户反馈「已开天数没有随天数累加」：原来直接显示 JSON 里的 ageDays，
 *   而 JSON 是每天定时重建的，两次重建之间（或数据还没重跑时）天数就停在旧值。
 *   这里改成用 openedAt 现算：
 *     · 已开天数 = 自然日差（今天日期 − 开预售日期），11-10 开 → 11-12 显示 2
 *     · 已过预售 = 开满 presaleDays × 24 小时（平台按满 7 天锁，不能提前清）
 *   SHEIN 的清零记录不参与这套推算：状态固定「已清零」，只按 clearedAt 显示时间。
 */
function parseOpened(s?: string): number | null {
  if (!s) return null;
  const t = Date.parse(String(s).replace(" ", "T"));
  return Number.isFinite(t) ? t : null;
}

/** 自然日差：按本地零点算，避免「差 1 小时少 1 天」 */
function ageDaysOf(openedAt?: string): number | null {
  const t = parseOpened(openedAt);
  if (t == null) return null;
  const a = new Date(t);
  const n = new Date();
  const d0 = new Date(a.getFullYear(), a.getMonth(), a.getDate()).getTime();
  const d1 = new Date(n.getFullYear(), n.getMonth(), n.getDate()).getTime();
  return Math.max(0, Math.round((d1 - d0) / 86400000));
}

/** 已开小时数（决定是否满 7 天可重开） */
function ageHoursOf(openedAt?: string): number | null {
  const t = parseOpened(openedAt);
  return t == null ? null : (Date.now() - t) / 3600000;
}

function daysText(v?: number | null) {
  return v == null || Number.isNaN(Number(v)) ? "—" : Number(v).toFixed(2);
}

/**
 * SHEIN 清零记录的状态标签：
 *   已清零 —— 线上现在仍是 0（这一页只放这种）
 *   未核对 —— 台账还没跑过对账（onlineNow 缺失）；线上>0 的记录后端已过滤掉，不展示
 */
function statusTagType(row: PresaleWatchItem | any) {
  return row.onlineNow == null ? "info" : "danger";
}

const base = computed<PresaleWatchItem[]>(() => {
  const limit = (data.value.presaleDays ?? 7) * 24;
  return (data.value.items ?? []).map((x) => {
    if (x.platform === "SHEIN") return x; // 清零记录：状态/天数都由台账给定
    const h = ageHoursOf(x.openedAt);
    const expired = h == null ? x.status === "已过预售" : h >= limit;
    return {
      ...x,
      ageDays: ageDaysOf(x.openedAt) ?? x.ageDays,
      status: (expired ? "已过预售" : "预售中") as PresaleWatchItem["status"],
      clearable: expired,
    };
  });
});

function matchScope(x: PresaleWatchItem) {
  if (x.platform !== platform.value) return false;
  if (store.value && x.store !== store.value) return false;
  const k = keyword.value.trim().toLowerCase();
  if (k && !`${x.spu ?? ""} ${x.code} ${x.variant ?? ""} ${x.seller ?? ""}`.toLowerCase().includes(k))
    return false;
  return true;
}

function platformCount(p: string) {
  return base.value.filter((x) => x.platform === p).length;
}

/** 店铺按钮上的条数：SHEIN 无状态页签，按平台+店铺算；TEMU 只看当前页签状态 */
function storeCount(s: string) {
  return base.value.filter((x) => {
    if (x.platform !== platform.value) return false;
    if (s && x.store !== s) return false;
    if (!isShein.value && x.status !== status.value) return false;
    return true;
  }).length;
}

function countOf(s: "预售中" | "已过预售") {
  return base.value.filter((x) => x.status === s && matchScope(x)).length;
}

const rows = computed(() =>
  base.value.filter((x) => matchScope(x) && (isShein.value || x.status === status.value)),
);

function todayStr() {
  const n = new Date();
  const p = (v: number) => String(v).padStart(2, "0");
  return `${n.getFullYear()}-${p(n.getMonth() + 1)}-${p(n.getDate())}`;
}

const stats = computed(() => {
  const scoped = base.value.filter(matchScope);
  if (isShein.value) {
    const today = todayStr();
    return [
      { label: "已清零（线上仍为 0）", value: scoped.length, color: "#f56c6c" },
      {
        label: "今日清零",
        value: scoped.filter((x) => String(x.clearedAt ?? "").startsWith(today)).length,
        color: "#e6a23c",
      },
      { label: "涉及款数", value: new Set(scoped.map((x) => x.code)).size, color: "" },
      { label: "涉及店铺", value: new Set(scoped.map((x) => x.store)).size, color: "#909399" },
    ];
  }
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
