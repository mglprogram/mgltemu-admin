import { ref, computed } from "vue";
import LimitedAPI, { type LimitedData, type FocusHit } from "@/api/temu/limited";
import GoodsAPI, { type GoodsData, type GoodsItem } from "@/api/temu/goods";
import FluxBoostAPI, { type FluxBoostData, type FluxBoostItem } from "@/api/temu/flux-boost";
import PresaleAPI, { type PresaleData, type PresaleRow } from "@/api/temu/presale";
import AutomationAPI, { type AutomationData, type AutomationTask, type AutomationSummary } from "@/api/automation";
import OrderGeoAPI, { type OrderGeoData, type GeoStateItem } from "@/api/temu/order-geo";

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
const orderGeoData = ref<OrderGeoData | null>(null);

/** 加载所有数据接口 */
async function loadAllData() {
  loading.value = true;
  try {
    const [lim, gds, flux, pre, auto, geo] = await Promise.allSettled([
      LimitedAPI.getLimitedData(),
      GoodsAPI.getGoodsData(),
      FluxBoostAPI.getFluxBoostData(),
      PresaleAPI.getPresaleData(),
      AutomationAPI.getAutomationData(),
      OrderGeoAPI.getOrderGeoData(),
    ]);

    if (lim.status === "fulfilled") limitedData.value = lim.value;
    if (gds.status === "fulfilled") goodsData.value = gds.value;
    if (flux.status === "fulfilled") fluxBoostData.value = flux.value;
    if (pre.status === "fulfilled") presaleData.value = pre.value;
    if (auto.status === "fulfilled") automationData.value = auto.value;
    // ⚠ 订单分布失败**不能**把上一次的图清空：地图是慢变量（一天才更新一次），
    //   某次刷新 404/网络抖动时保留旧数据比显示空白好。
    if (geo.status === "fulfilled") orderGeoData.value = geo.value;

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
    // ⚠ 口径（2026-10-14 修）：lowStock.rows 是**款×尺码**粒度，同一个款会按尺码
    //   重复出现 3~5 次。大字必须以「款」为单位（summary.style），否则 183 会被
    //   误读成"183 个款要处理"，实际只有 62 个款（183 是尺码行数）。
    //   这里两个数都给出去，卡片上分别标明单位，与「在售款明细」页
    //   「N 个款 / M 个尺码」的说法保持一致。
    let lowStockCount = 0;
    let lowStockStyleCount = 0;
    let canRestockCount = 0;
    let outOfStockCount = 0;
    if (g) {
      const storeObj = currentStore.value === "ALL" ? null : g.stores?.[currentStore.value];
      const allRows = storeObj ? storeObj.lowStock?.rows || [] : g.lowStock?.rows || [];
      const summary = storeObj ? storeObj.lowStock?.summary : g.lowStock?.summary;
      const restock = storeObj ? storeObj.lowStock?.restock : g.lowStock?.restock;
      lowStockCount = allRows.length; // SKU（款×尺码）行数
      // 涉及多少个款：优先用构建期算好的 summary.style（口径唯一），
      // 缺失时按 (store, code) 去重回退，避免两处各算一套
      lowStockStyleCount = summary?.style ?? new Set(allRows.map((r) => `${r.store}|${r.code}`)).size;
      // ⚠ 口径（2026-10-14 自检修）：必须用构建期的权威计数，**不能**用
      //   `r.verdict === "可加库存" || r.canRestock` ——
      //   `canRestock` 只是「仓里有货」的宽松标记（实测 ALL=89 行），
      //   而「可加库存」还要求算得出加量（80 行）。用 `||` 会把 9 行
      //   「库存够卖(不用加) / 仓里有货(未定量)」也算进来，
      //   导致大屏显示 89、今日待办显示 80，两处对不上。
      canRestockCount =
        restock?.verdictCounts?.["可加库存"] ??
        allRows.filter((r) => r.verdict === "可加库存").length;
      outOfStockCount = allRows.filter((r) => r.stock === 0).length;
    }

    // 预售建议
    // ⚠ 口径（2026-10-14 修）：原来读 presale.json 的 rows.length / verdict，
    //   但那条链路的输入**只有「已售罄/即将售罄」的几行**，与「在售款明细 ·
    //   今日待办」是两套数据 —— 实测大屏显示「6 款 / 建议开预售 0 款」，
    //   而 on-sale 页今日待办里明明是「可开预售 2」（BEL-102503-5SetA XL/XXL）。
    //   现统一改用 goods.json 的 lowStock.restock（今日待办同一份数据），
    //   两个页面的数字从此对得上。
    let presaleCount = 0;
    let presaleReady = 0;
    let needRestockCount = 0;
    let presaleRowsDetail: { store: string; code: string; size?: string; arrivalDate?: string }[] = [];
    {
      const storeObj = currentStore.value === "ALL" ? null : g?.stores?.[currentStore.value];
      const restock = storeObj ? storeObj.lowStock?.restock : g?.lowStock?.restock;
      const counts = restock?.verdictCounts || {};
      presaleReady = counts["可开预售"] ?? 0;
      needRestockCount = counts["需补货"] ?? 0;
      // 「待办款式」口径 = 可开预售 + 可加库存 + 需补货（都是今天要动的）
      presaleCount =
        (counts["可开预售"] ?? 0) + (counts["可加库存"] ?? 0) + (counts["需补货"] ?? 0);
      presaleRowsDetail = (restock?.preSaleRows || []).map((r) => ({
        store: r.store,
        code: r.code,
        size: r.size,
        arrivalDate: r.arrivalDate,
      }));
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
      lowStockStyleCount,
      canRestockCount,
      outOfStockCount,
      presaleCount,
      presaleReady,
      needRestockCount,
      presaleRowsDetail,
      fluxActiveCount,
      fluxPendingCount,
      autoSummary,
    };
  });

  /**
   * 「今日待办」补货汇总（**随店铺切换**）。
   *
   * ⚠ 2026-10-14 自检修：各组件**不要**再直接读 `goodsData.value.lowStock.restock`
   *   —— 那是两店合计，切到 BEL/BHY 时数字不变（面板会一直显示全店口径）。
   *   统一从这里取，单店时读该店自己的 restock。
   */
  const restockSummary = computed(() => {
    const g = goodsData.value;
    if (!g?.lowStock) return undefined;
    const storeObj = currentStore.value === "ALL" ? null : g.stores?.[currentStore.value];
    return storeObj?.lowStock?.restock ?? g.lowStock.restock;
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

  /** 7. 预售建议行列表（每行补上所属店铺，供列表做 key / 展示） */
  const presaleRows = computed<PresaleRow[]>(() => {
    const p = presaleData.value;
    if (!p?.stores) return [];
    if (currentStore.value === "ALL") {
      return p.stores.flatMap((s) => (s.rows || []).map((r) => ({ ...r, store: s.store })));
    }
    const st = p.stores.find((s) => s.store === currentStore.value);
    return (st?.rows || []).map((r) => ({ ...r, store: st!.store }));
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

  /**
   * 10. 订单分布（美国州/市）—— 随店铺切换
   *
   * 数据来自帕沃尔海外仓（不是 TEMU 后台）：两店出库单的收件州/市。
   * 口径说明见 views/temu/order-geo/index.vue 顶部注释。
   */
  const geoStates = computed<GeoStateItem[]>(() => {
    const g = orderGeoData.value;
    if (!g) return [];
    if (currentStore.value === "ALL") return g.states || [];
    return g.stores?.[currentStore.value]?.states || [];
  });

  return {
    currentStore,
    loading,
    lastUpdated,
    loadAllData,
    metrics,
    restockSummary,
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
    orderGeoData,
    geoStates,
  };
}
