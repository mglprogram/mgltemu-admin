import { ref, computed } from "vue";
import LimitedAPI, { type LimitedData, type FocusHit } from "@/api/temu/limited";
import GoodsAPI, { type GoodsData, type GoodsItem } from "@/api/temu/goods";
import FluxBoostAPI, { type FluxBoostData, type FluxBoostItem } from "@/api/temu/flux-boost";
import PresaleAPI, { type PresaleData, type PresaleRow } from "@/api/temu/presale";
import AutomationAPI, { type AutomationData, type AutomationTask, type AutomationSummary } from "@/api/automation";

export type StoreType = "ALL" | "BEL" | "BHY";

// 全局单例响应式状态，避免多个子组件重复触发 API 请求
const currentStore = ref<StoreType>("ALL");
const loading = ref(false);
const lastUpdated = ref<string>("--");

const limitedData = ref<LimitedData | null>(null);
const goodsData = ref<GoodsData | null>(null);
const fluxBoostData = ref<FluxBoostData | null>(null);
const presaleData = ref<PresaleData | null>(null);
const automationData = ref<AutomationData | null>(null);

/** 加载所有数据接口 */
async function loadAllData() {
  loading.value = true;
  try {
    const [lim, gds, flux, pre, auto] = await Promise.allSettled([
      LimitedAPI.getLimitedData(),
      GoodsAPI.getGoodsData(),
      FluxBoostAPI.getFluxBoostData(),
      PresaleAPI.getPresaleData(),
      AutomationAPI.getAutomationData(),
    ]);

    if (lim.status === "fulfilled") limitedData.value = lim.value;
    if (gds.status === "fulfilled") goodsData.value = gds.value;
    if (flux.status === "fulfilled") fluxBoostData.value = flux.value;
    if (pre.status === "fulfilled") presaleData.value = pre.value;
    if (auto.status === "fulfilled") automationData.value = auto.value;

    const now = new Date();
    const pad = (n: number) => String(n).padStart(2, "0");
    lastUpdated.value = `${pad(now.getHours())}:${pad(now.getMinutes())}:${pad(now.getSeconds())}`;
  } catch (err) {
    console.error("加载数据大屏数据异常:", err);
  } finally {
    loading.value = false;
  }
}

export function useDataScreenData() {
  /** 1. 顶部总计指标 */
  const metrics = computed(() => {
    const g = goodsData.value;
    const l = limitedData.value;
    const p = presaleData.value;
    const f = fluxBoostData.value;
    const a = automationData.value;

    // 在售总款数与总库存（根据当前选店筛选）
    let onSaleSkc = 0;
    let onSaleSpu = 0;
    let onSaleSku = 0;
    let totalStock = 0;

    if (g) {
      if (currentStore.value === "ALL") {
        onSaleSkc = g.totals?.onSaleSkc || 0;
        onSaleSpu = g.totals?.spu || 0;
        onSaleSku = g.totals?.sku || 0;
        totalStock = g.totals?.stock || 0;
      } else {
        const storeObj = g.stores?.[currentStore.value];
        if (storeObj?.counts) {
          onSaleSkc = storeObj.counts.onSaleSkc || 0;
          onSaleSpu = storeObj.counts.spu || 0;
          onSaleSku = storeObj.counts.sku || 0;
          totalStock = storeObj.counts.stock || 0;
        }
      }
    }

    // 限流商品数
    let limitedCount = 0;
    let focusHitCount = 0;
    let focusTotal = 0;
    if (l) {
      if (currentStore.value === "ALL") {
        limitedCount = Object.values(l.stores || {}).reduce((acc, s) => acc + (s.total || 0), 0);
        focusHitCount = (l.watch || []).reduce((acc, w) => acc + (w.hitCount || 0), 0);
        focusTotal = (l.watch || []).reduce((acc, w) => acc + (w.focusTotal || 0), 0);
      } else {
        const s = l.stores?.[currentStore.value];
        limitedCount = s?.total || 0;
        const w = (l.watch || []).find((x) => x.store === currentStore.value);
        focusHitCount = w?.hitCount || 0;
        focusTotal = w?.focusTotal || 0;
      }
    }

    // 款-库存预警
    let lowStockCount = 0;
    let canRestockCount = 0;
    let outOfStockCount = 0;
    if (g) {
      const allRows =
        currentStore.value === "ALL"
          ? g.lowStock?.rows || []
          : g.stores?.[currentStore.value]?.lowStock?.rows || [];
      lowStockCount = allRows.length;
      canRestockCount = allRows.filter((r) => r.verdict === "可加库存" || r.canRestock).length;
      outOfStockCount = allRows.filter((r) => r.stock === 0).length;
    }

    // 预售建议
    let presaleCount = 0;
    let presaleReady = 0;
    if (p?.stores) {
      const stores =
        currentStore.value === "ALL"
          ? p.stores
          : p.stores.filter((s) => s.store === currentStore.value);
      const allRows = stores.flatMap((s) => s.rows || []);
      presaleCount = allRows.length;
      presaleReady = allRows.filter((r) => r.verdict === "可开预售" || r.canPresale).length;
    }

    // 流量加速
    let fluxActiveCount = 0;
    let fluxPendingCount = 0;
    if (f?.stores) {
      if (currentStore.value === "ALL") {
        fluxActiveCount = Object.values(f.stores).reduce(
          (acc, s) => acc + (s.sections?.accelerating?.total || s.sections?.accelerating?.list?.length || 0),
          0
        );
        fluxPendingCount = Object.values(f.stores).reduce(
          (acc, s) => acc + (s.sections?.pending?.total || s.sections?.pending?.list?.length || 0),
          0
        );
      } else {
        const s = f.stores[currentStore.value];
        fluxActiveCount = s?.sections?.accelerating?.total || s?.sections?.accelerating?.list?.length || 0;
        fluxPendingCount = s?.sections?.pending?.total || s?.sections?.pending?.list?.length || 0;
      }
    }

    // 自动化任务
    const autoSummary: AutomationSummary = a?.summary || {
      total: 0,
      ok: 0,
      fail: 0,
      alert: 0,
      warn: 0,
      never: 0,
      disabled: 0,
      running: 0,
      waiting: 0,
      problem: 0,
    };

    return {
      onSaleSkc,
      onSaleSpu,
      onSaleSku,
      totalStock,
      limitedCount,
      focusHitCount,
      focusTotal,
      lowStockCount,
      canRestockCount,
      outOfStockCount,
      presaleCount,
      presaleReady,
      fluxActiveCount,
      fluxPendingCount,
      autoSummary,
    };
  });

  /** 2. 重点限流 SPU 名单 */
  const focusHits = computed<FocusHit[]>(() => {
    if (!limitedData.value?.watch) return [];
    if (currentStore.value === "ALL") {
      return limitedData.value.watch.flatMap((w) => w.hits || []);
    }
    const w = limitedData.value.watch.find((x) => x.store === currentStore.value);
    return w?.hits || [];
  });

  /** 3. 限流状态分布 */
  const limitedFlowDist = computed(() => {
    const l = limitedData.value;
    if (!l?.stores) return [];
    const map = new Map<string, number>();

    const targetStores =
      currentStore.value === "ALL" ? Object.values(l.stores) : [l.stores[currentStore.value]].filter(Boolean);

    targetStores.forEach((st) => {
      (st.flowDist || []).forEach((item) => {
        map.set(item.name, (map.get(item.name) || 0) + item.value);
      });
    });

    return Array.from(map.entries()).map(([name, value]) => ({ name, value }));
  });

  /** 4. 在售款销量 Top 10 榜单 */
  const topSalesGoods = computed<GoodsItem[]>(() => {
    const g = goodsData.value;
    if (!g?.stores) return [];
    let items: GoodsItem[] = [];
    if (currentStore.value === "ALL") {
      items = Object.values(g.stores).flatMap((s) => s.items || []);
    } else {
      items = g.stores[currentStore.value]?.items || [];
    }
    return [...items].sort((a, b) => (b.sales7 || 0) - (a.sales7 || 0)).slice(0, 10);
  });

  /** 5. 双店对比指标 */
  const storeComparison = computed(() => {
    const g = goodsData.value;
    const l = limitedData.value;

    const belStore = g?.stores?.BEL;
    const bhyStore = g?.stores?.BHY;

    const belWatch = (l?.watch || []).find((w) => w.store === "BEL");
    const bhyWatch = (l?.watch || []).find((w) => w.store === "BHY");

    return {
      bel: {
        name: "BEL 店 (TM-BEL-MGL)",
        skc: belStore?.counts?.onSaleSkc || belStore?.items?.length || 0,
        stock: belStore?.counts?.stock || 0,
        sales7: (belStore?.items || []).reduce((acc: number, i: GoodsItem) => acc + (i.sales7 || 0), 0),
        limited: l?.stores?.BEL?.total || 0,
        focusHits: belWatch?.hitCount || 0,
        focusTotal: belWatch?.focusTotal || 0,
      },
      bhy: {
        name: "BHY 店 (TM-BHY-YJF)",
        skc: bhyStore?.counts?.onSaleSkc || bhyStore?.items?.length || 0,
        stock: bhyStore?.counts?.stock || 0,
        sales7: (bhyStore?.items || []).reduce((acc: number, i: GoodsItem) => acc + (i.sales7 || 0), 0),
        limited: l?.stores?.BHY?.total || 0,
        focusHits: bhyWatch?.hitCount || 0,
        focusTotal: bhyWatch?.focusTotal || 0,
      },
    };
  });

  /** 6. 品类在售分布 */
  const categoryDist = computed(() => {
    const g = goodsData.value;
    if (!g?.stores) return [];

    let items: GoodsItem[] = [];
    if (currentStore.value === "ALL") {
      items = Object.values(g.stores).flatMap((s) => s.items || []);
    } else {
      items = g.stores[currentStore.value]?.items || [];
    }

    const map = new Map<string, { count: number; stock: number }>();
    items.forEach((i: GoodsItem) => {
      const cat = i.category || "未分类";
      const curr = map.get(cat) || { count: 0, stock: 0 };
      curr.count += 1;
      curr.stock += i.stock || 0;
      map.set(cat, curr);
    });

    return Array.from(map.entries())
      .map(([name, data]) => ({ name, ...data }))
      .sort((a, b) => b.count - a.count)
      .slice(0, 8);
  });

  /** 7. 预售建议行列表 */
  const presaleRows = computed<PresaleRow[]>(() => {
    const p = presaleData.value;
    if (!p?.stores) return [];
    if (currentStore.value === "ALL") {
      return p.stores.flatMap((s) => s.rows || []);
    }
    const st = p.stores.find((s) => s.store === currentStore.value);
    return st?.rows || [];
  });

  /** 8. 流量加速数据 */
  const fluxBoostItems = computed<FluxBoostItem[]>(() => {
    const f = fluxBoostData.value;
    if (!f?.stores) return [];
    if (currentStore.value === "ALL") {
      return Object.values(f.stores).flatMap((s) => s.sections?.accelerating?.list || []);
    }
    return f.stores[currentStore.value]?.sections?.accelerating?.list || [];
  });

  /** 9. 自动化任务列表 */
  const automationTasks = computed<AutomationTask[]>(() => {
    return automationData.value?.tasks || [];
  });

  return {
    currentStore,
    loading,
    lastUpdated,
    loadAllData,
    metrics,
    focusHits,
    limitedFlowDist,
    topSalesGoods,
    storeComparison,
    categoryDist,
    presaleRows,
    fluxBoostItems,
    automationTasks,
    limitedData,
    goodsData,
    fluxBoostData,
    presaleData,
    automationData,
  };
}
