<!--
  货件跟踪速览（大屏用）

  2026-10-30 首次上线；**2026-11-02 按新口径重写**：
  用户要求「其他单号改成入库单号，直接从领星每天抓取入库单号跟踪数据更全」
  +「根据到港日期和签收日期预测到港之后平均几天能签收送仓上架」。

  所以这块面板现在回答一个问题：**哪些货到港了、该催了、还差几天**。

  数据源 = public/data/shipment-track.json（scripts/goods/fetch_shipment_track.py）

  排序（2026-10-30 用户指出过缺陷后定的，别改回去）：
    ① 超期天数降序 —— 过了预测上架日还没上架的排最前
    ② 到港已等天数降序 —— 等得久的在前
    ③ 其余按入库单号
  ⚠ 历史教训：一开始按「到港日期倒序」，10-27 那天 21 票直接把 6 个位置占满，
    10-21 那批（10/22 就提柜、等最久）一票都排不上。排序要按「该催的程度」，不是「日期新」。
-->
<template>
  <div class="panel-box flex-1 flex flex-col min-h-0">
    <div class="panel-title-bar">
      <div class="title-left">
        <span class="indicator-bar" />
        <span class="title-text">货件跟踪 · 到港待催</span>
        <span class="title-sub">超期 / 等最久的在最上</span>
      </div>
      <button type="button" class="more-link" @click="goTrack">货件跟踪 →</button>
    </div>

    <!-- 数字：超期 / 已到港未上架 / 在途 / 近3天刚上架 -->
    <div class="stat-strip">
      <span class="stat">
        <i class="dot dot--bad" />超期 <b class="font-din">{{ s.overdue }}</b>
      </span>
      <span class="stat">
        <i class="dot dot--warn" />到港未上架 <b class="font-din">{{ s.arrivedWaiting }}</b>
      </span>
      <span class="stat">
        <i class="dot dot--run" />在途 <b class="font-din">{{ s.notArrived }}</b>
      </span>
      <span class="stat stat--right">
        <i class="dot dot--ok" />近3天到港→上架 <b class="font-din">{{ recent.length }}</b>
      </span>
    </div>

    <el-empty
      v-if="!rows.length"
      description="货件数据未就绪"
      :image-size="50"
      class="empty-mini"
    />

    <template v-else>
      <!-- 表头 -->
      <div class="grid-head">
        <span>入库单 / 到港</span>
        <span>产品</span>
        <span>预测 / 轨迹</span>
      </div>

      <div class="list-container flex-1 overflow-y-auto">
        <div v-for="r in rows" :key="r.inboundNo" class="grid-row">
          <!-- ① 入库单 + 到港 + 等了多少天 -->
          <div class="cell cell--port">
            <div class="port-line">
              <span class="port-date font-din">{{ shortDate(r.arriveDate) }}</span>
              <span v-if="r.overdueDays > 0" class="over-pill">超{{ r.overdueDays }}天</span>
            </div>
            <div class="port-no font-din">{{ r.inboundNo }}</div>
            <div class="port-oms">
              <span :class="['oms-pill', omsCls(r.status)]">{{ r.statusText }}</span>
              <span class="port-wh">{{ r.channel || shortWh(r.whName) }}</span>
            </div>
          </div>

          <!-- ② 产品 -->
          <div class="cell cell--prod">
            <template v-if="r.skus.length">
              <div v-for="sk in r.skus.slice(0, 2)" :key="sk.sku" class="prod-line">
                <span class="prod-name">{{ sk.productName }}</span>
                <span class="prod-qty font-din">×{{ sk.qty }}</span>
              </div>
              <div v-if="r.skus.length > 2" class="prod-more">
                等 {{ r.skus.length }} 个 SKU · 共 {{ num(r.totalQty) }} 件
              </div>
            </template>
            <span v-else class="muted">无 SKU 明细</span>
          </div>

          <!-- ③ 预测上架 + 最新轨迹 -->
          <div class="cell cell--trace">
            <div class="pred-line">
              <span class="pred-label">预测上架</span>
              <span class="pred-date font-din">{{ shortDate(r.predictedShelf) }}</span>
              <span v-if="r.waitDays != null" class="pred-wait">已等 {{ r.waitDays }} 天</span>
            </div>
            <div
              v-if="r.logistics.ok"
              class="trace-info"
              :class="{ 'is-bad': r.logistics.currentAbnormal }"
              :title="r.logistics.latest.info"
            >
              {{ r.logistics.latest.info }}
            </div>
            <div v-else class="trace-info muted">恒信不覆盖（帕沃尔单）</div>
          </div>
        </div>

        <!-- 下半段：最近 3 天刚走完「到港 → 签收 → 上架」的（用户 2026-11-02 要求加） -->
        <div v-if="recent.length" class="recent-sec">
          <div class="recent-head">
            <span class="recent-title">近 3 天到港 → 上架</span>
            <span class="recent-count">{{ recent.length }} 票</span>
            <span class="recent-hint">{{ recentMore ? `还有 ${recentMore} 票，见详情页` : "看实际走了几天" }}</span>
          </div>
          <div v-for="d in recentShown" :key="d.inboundNo" class="recent-row">
            <span class="rc-no font-din">{{ d.inboundNo }}</span>
            <span class="rc-flow font-din">
              {{ shortDate(d.arriveDate) }} <i>→</i> {{ shortDate(d.shelfTime) }}
            </span>
            <span class="rc-days font-din" :class="d.days <= 8 ? 'rc-days--ok' : 'rc-days--slow'">
              {{ d.days }} 天
            </span>
          </div>
        </div>
      </div>
    </template>
  </div>
</template>

<script lang="ts" setup>
  import { ref, computed, onMounted } from "vue";
  import { useRouter } from "vue-router";
  import ShipmentTrackAPI from "@/api/temu/shipment-track";
  import type { ShipmentRow, ShipmentTrackData } from "@/api/temu/shipment-track";

  defineOptions({ name: "ShipmentTrackPanel" });

  const router = useRouter();

  function goTrack() {
    // 2026-11-02 改：这两个功能已搬到与 TEMU 运营同级的「产品-库存」模块
    // （旧地址 /temu/shipment-track 仍有 redirect 兜底，但这里直接用新地址）
    router.push("/product/shipment-track");
  }

  /** 面板定高：待催 3 条 + 近3天上架 3 条正好铺满，不用滚 */
  const MAX_ROWS = 3;

  const data = ref<ShipmentTrackData | null>(null);

  async function load() {
    try {
      data.value = await ShipmentTrackAPI.getShipmentTrack();
    } catch {
      data.value = null; // 拿不到就显示「未就绪」，不影响大屏其它面板
    }
  }
  onMounted(load);

  const num = (v?: number | null) =>
    v == null ? "—" : Math.round(Number(v)).toLocaleString("zh-CN");

  const s = computed(
    () =>
      data.value?.summary ?? {
        overdue: 0,
        arrivedWaiting: 0,
        notArrived: 0,
        onShelf: 0,
      }
  );

  /**
   * 只看「到港了但还没上架」的，按该催的程度排：
   *   超期天数降序 → 到港已等天数降序 → 入库单号
   * （已上架/已取消的不进这块面板 —— 那些不需要催）
   */
  const rows = computed<ShipmentRow[]>(() => {
    const all = data.value?.rows ?? [];
    return all
      .filter((r) => r.arriveDate && r.status !== 4 && r.status !== 5)
      .sort((a, b) => {
        const o = b.overdueDays - a.overdueDays;
        if (o) return o;
        const w = (b.waitDays ?? -1) - (a.waitDays ?? -1);
        if (w) return w;
        return a.inboundNo.localeCompare(b.inboundNo);
      })
      .slice(0, MAX_ROWS);
  });

  const shortDate = (d?: string) => (d ? String(d).slice(5, 10) : "—");

  /**
   * 近 3 天走完「到港 → 签收 → 上架」的票。
   *
   * 口径（用户 2026-11-02 要求）：
   *   · 上架时间落在近 3 天（今天 / 昨天 / 前天）—— 按**上架日**算
   *   · **必须要有到港日期**，否则算不出"到港之后用了几天"，
   *     帕沃尔那批没有恒信轨迹、拿不到到港日，单独占位没意义，直接不列
   *   · 按到港→上架用得少 → 多的顺序？不 —— 按上架时间倒序（最近的先看）
   */
  const RECENT_DAYS = 3;
  /** 面板定高，待催 4 条 + 这里 3 条正好铺满、不用滚 */
  const RECENT_MAX = 3;

  const recent = computed<
    { inboundNo: string; arriveDate: string; shelfTime: string; days: number }[]
  >(() => {
    const all = data.value?.rows ?? [];
    const today = new Date();
    today.setHours(0, 0, 0, 0);
    const dayOf = (s: string) => {
      if (!s) return null;
      const d = new Date(`${String(s).slice(0, 10)}T00:00:00`);
      return Number.isNaN(d.getTime()) ? null : d;
    };
    return all
      .map((r) => {
        const shelf = dayOf(r.shelfTime);
        const arrive = dayOf(r.arriveDate);
        if (!shelf || !arrive) return null;
        const back = Math.round((today.getTime() - shelf.getTime()) / 86400000);
        if (back < 0 || back >= RECENT_DAYS) return null;
        return {
          inboundNo: r.inboundNo,
          arriveDate: r.arriveDate,
          shelfTime: r.shelfTime,
          days: Math.round((shelf.getTime() - arrive.getTime()) / 86400000),
        };
      })
      .filter((x): x is { inboundNo: string; arriveDate: string; shelfTime: string; days: number } => !!x)
      .sort((a, b) => b.shelfTime.localeCompare(a.shelfTime));
  });

  /** 面板里只铺前 3 条，剩下的在「更多」里体现（总数照实报） */
  const recentShown = computed(() => recent.value.slice(0, RECENT_MAX));
  const recentMore = computed(() => Math.max(0, recent.value.length - RECENT_MAX));

  const omsCls = (st?: number) =>
    st === 4 ? "oms-pill--ok" : st === 1 ? "oms-pill--wait" : st === 5 ? "oms-pill--bad" : "oms-pill--info";

  /** 仓库名太长（ZOO WAREHOUSE ⑥美西），大屏只留能区分的短名 */
  const shortWh = (w?: string) => {
    const t = String(w || "");
    if (t.includes("ZOO") || t.includes("⑥")) return "恒信美西";
    if (t.includes("加州")) return "帕沃尔加州";
    return t.replace(/^.*·/, "");
  };
</script>

<style lang="scss" scoped>
  .panel-box {
    background: rgba(10, 24, 52, 0.75);
    border: 1px solid rgba(46, 96, 153, 0.45);
    border-radius: 6px;
    padding: 10px 12px;
    backdrop-filter: blur(8px);
  }

  .panel-title-bar {
    display: flex;
    align-items: center;
    justify-content: space-between;
    padding-bottom: 8px;
    border-bottom: 1px solid rgba(46, 96, 153, 0.3);
    margin-bottom: 6px;
  }

  .title-left {
    display: flex;
    align-items: center;
    gap: 6px;
  }

  .indicator-bar {
    width: 3px;
    height: 14px;
    background: #00eaff;
    box-shadow: 0 0 8px #00eaff;
    border-radius: 2px;
  }

  .title-text {
    font-size: 13px;
    font-weight: 600;
    color: #00eaff;
    letter-spacing: 0.5px;
  }

  .title-sub {
    font-size: 9.5px;
    color: #5f80a8;
    margin-left: 2px;
  }

  .more-link {
    background: transparent;
    border: none;
    font-size: 11px;
    color: #8fb2dc;
    cursor: pointer;
    padding: 0;

    &:hover {
      color: #00eaff;
    }
  }

  .empty-mini {
    padding: 12px 0;
  }

  /* ---------------- 顶部数字 ---------------- */
  .stat-strip {
    display: flex;
    align-items: center;
    gap: 10px;
    padding: 3px 2px 6px;
    font-size: 10px;
    color: #8fb2dc;
  }

  .stat {
    display: flex;
    align-items: center;
    gap: 3px;

    b {
      font-size: 12px;
      color: #c8e4ff;
    }

    &--right {
      margin-left: auto;
    }
  }

  .dot {
    width: 5px;
    height: 5px;
    border-radius: 50%;

    &--ok {
      background: #00eaff;
      box-shadow: 0 0 5px #00eaff;
    }

    &--warn {
      background: #e6a23c;
    }

    &--run {
      background: #6b8cb5;
    }

    &--bad {
      background: #ff5252;
      box-shadow: 0 0 5px #ff5252;
    }
  }

  /* ---------------- 三列 ---------------- */
  .grid-head,
  .grid-row {
    display: grid;
    /* 入库单 27% / 产品 36% / 预测+轨迹 37% */
    grid-template-columns: 27% 36% 37%;
    gap: 6px;
  }

  .grid-head {
    padding: 2px 6px 4px;
    font-size: 9px;
    color: #5f80a8;
    letter-spacing: 0.5px;
    border-bottom: 1px dashed rgba(46, 96, 153, 0.3);
  }

  .list-container {
    display: flex;
    flex-direction: column;
    gap: 4px;
    padding-top: 4px;
    min-height: 0;
    /* 面板定高，最后一行常被截半截 —— 底部渐隐，读起来像"还有更多" */
    mask-image: linear-gradient(to bottom, #000 calc(100% - 18px), transparent 100%);
  }

  .grid-row {
    padding: 5px 6px;
    background: rgba(14, 32, 68, 0.5);
    border-radius: 3px;
    border-left: 2px solid transparent;
    font-size: 10.5px;

    &:hover {
      background: rgba(20, 44, 90, 0.65);
      border-left-color: #00eaff;
    }
  }

  .cell {
    min-width: 0;
    display: flex;
    flex-direction: column;
    gap: 1px;
  }

  /* ① 入库单 / 到港 */
  .port-line {
    display: flex;
    align-items: center;
    gap: 4px;
  }

  .port-date {
    font-size: 12px;
    font-weight: 700;
    color: #00eaff;
  }

  .over-pill {
    font-size: 8.5px;
    line-height: 13px;
    padding: 0 4px;
    border-radius: 2px;
    color: #fff;
    background: #d93a3a;
    white-space: nowrap;
  }

  .port-no {
    font-size: 9px;
    color: #6b8cb5;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }

  .port-oms {
    display: flex;
    align-items: center;
    gap: 3px;
    margin-top: 1px;
  }

  .oms-pill {
    font-size: 8.5px;
    padding: 0 4px;
    border-radius: 2px;
    line-height: 13px;
    white-space: nowrap;

    &--ok {
      color: #67c23a;
      background: rgba(103, 194, 58, 0.14);
    }

    &--wait {
      color: #e6a23c;
      background: rgba(230, 162, 60, 0.14);
    }

    &--bad {
      color: #ff5252;
      background: rgba(255, 82, 82, 0.14);
    }

    &--info {
      color: #8fb2dc;
      background: rgba(143, 178, 220, 0.12);
    }
  }

  .port-wh {
    font-size: 8.5px;
    color: #5f80a8;
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
  }

  /* ② 产品 */
  .prod-line {
    display: flex;
    align-items: baseline;
    justify-content: space-between;
    gap: 4px;
  }

  .prod-name {
    color: #c8e4ff;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }

  .prod-qty {
    color: #00eaff;
    font-weight: 700;
    flex: none;
  }

  .prod-more {
    font-size: 9px;
    color: #5f80a8;
  }

  /* ③ 预测 + 轨迹 */
  .pred-line {
    display: flex;
    align-items: baseline;
    gap: 4px;
    white-space: nowrap;
  }

  .pred-label {
    font-size: 8.5px;
    color: #5f80a8;
  }

  .pred-date {
    font-size: 11px;
    font-weight: 700;
    color: #ffd24d;
  }

  .pred-wait {
    font-size: 8.5px;
    color: #6b8cb5;
  }

  .trace-info {
    color: #a8c6e8;
    line-height: 1.35;
    display: -webkit-box;
    -webkit-line-clamp: 2;
    -webkit-box-orient: vertical;
    overflow: hidden;

    &.is-bad {
      color: #ff8a3d;
      font-weight: 600;
    }
  }

  .muted {
    color: #5f80a8;
  }

  /* ---------- 近 3 天到港 → 上架 ---------- */
  .recent-sec {
    margin-top: 8px;
    padding-top: 6px;
    border-top: 1px dashed rgba(46, 96, 153, 0.35);
  }

  .recent-head {
    display: flex;
    align-items: baseline;
    gap: 6px;
    padding: 0 2px 4px;
  }

  .recent-title {
    font-size: 10px;
    font-weight: 600;
    color: #67c23a;
    letter-spacing: 0.3px;
  }

  .recent-count {
    font-size: 9.5px;
    color: #8fb2dc;
  }

  .recent-hint {
    margin-left: auto;
    font-size: 8.5px;
    color: #5f80a8;
  }

  .recent-row {
    display: flex;
    align-items: center;
    gap: 6px;
    padding: 3px 6px;
    margin-bottom: 3px;
    background: rgba(14, 32, 68, 0.4);
    border-radius: 3px;
    border-left: 2px solid rgba(103, 194, 58, 0.5);
    font-size: 10px;
  }

  .rc-no {
    flex: 0 0 40%;
    color: #9fbfe0;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }

  .rc-flow {
    flex: 1;
    color: #8fb2dc;
    white-space: nowrap;

    i {
      font-style: normal;
      color: #5f80a8;
      margin: 0 1px;
    }
  }

  .rc-days {
    flex: none;
    font-weight: 700;

    &--ok {
      color: #67c23a;
    }

    &--slow {
      color: #e6a23c;
    }

    &--na {
      color: #5f80a8;
      font-weight: 400;
      font-size: 8.5px;
    }
  }
</style>
