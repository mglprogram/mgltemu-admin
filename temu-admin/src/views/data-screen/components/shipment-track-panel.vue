<!--
  货件跟踪速览（大屏用）

  2026-10-30 用户要求：把大屏左侧「库存跟踪 · 九部快售罄 / 十部库存前三」这块
  换成「货件跟踪」，展示形式按「已到港 - 产品 - 轨迹」。

  数据源 = public/data/shipment-track.json
  （scripts/goods/fetch_shipment_track.py 每天 18:40 生成）

  三列口径：
    已到港 = 追踪表「到港日期」有值（业务口径，不是物流口径 ——
             所以会出现「到港了但物流还在提柜待送仓」）
    产品   = 领星 OMS 入库单明细的产品名 × 数量（不是表里那个粗分类的「发货品名」）
    轨迹   = 恒信最新节点（traces[0]），带时间

  ⚠ 大屏只有一块小面板，全量 242 票放不下 ⇒ 默认只列**已到港**、
    按到港日期倒序取前 8 条，可滚；点「货件跟踪 →」进详情页看全量。
-->
<template>
  <div class="panel-box flex-1 flex flex-col min-h-0">
    <div class="panel-title-bar">
      <div class="title-left">
        <span class="indicator-bar" />
        <span class="title-text">货件跟踪 · 已到港</span>
      </div>
      <button type="button" class="more-link" @click="goTrack">货件跟踪 →</button>
    </div>

    <!-- 三个数字：已到港 / 在途 / 异常 -->
    <div class="stat-strip">
      <span class="stat">
        <i class="dot dot--ok" />已到港 <b class="font-din">{{ summary.arrived }}</b>
      </span>
      <span class="stat">
        <i class="dot dot--run" />在途 <b class="font-din">{{ summary.notArrived }}</b>
      </span>
      <span class="stat">
        <i class="dot dot--bad" />异常 <b class="font-din">{{ summary.abnormal }}</b>
      </span>
      <span class="stat stat--right">
        OMS 已上架 <b class="font-din">{{ summary.omsOnShelf }}</b>
      </span>
    </div>

    <el-empty
      v-if="!rows.length"
      description="货件数据未就绪"
      :image-size="50"
      class="empty-mini"
    />

    <!-- 表头 -->
    <div v-else class="grid-head">
      <span>已到港</span>
      <span>产品</span>
      <span>轨迹</span>
    </div>

    <div class="list-container flex-1 overflow-y-auto">
      <div v-for="r in rows" :key="r.otherNo" class="grid-row">
        <!-- ① 已到港 -->
        <div class="cell cell--port">
          <div class="port-date font-din">{{ shortDate(r.arriveDate) }}</div>
          <div class="port-no font-din">{{ r.otherNo }}</div>
          <div class="port-oms">
            <span :class="['oms-pill', omsCls(r.oms.status)]">{{ r.oms.statusText }}</span>
            <span class="port-wh">{{ shortWh(r.oms.whName) }}</span>
          </div>
        </div>

        <!-- ② 产品 -->
        <div class="cell cell--prod">
          <template v-if="r.oms.skus.length">
            <div v-for="s in r.oms.skus.slice(0, 3)" :key="s.sku" class="prod-line">
              <span class="prod-name">{{ s.productName }}</span>
              <span class="prod-qty font-din">×{{ s.qty }}</span>
            </div>
            <div v-if="r.oms.skus.length > 3" class="prod-more">
              等 {{ r.oms.skus.length }} 个 SKU · 共 {{ num(r.oms.totalQty) }} 件
            </div>
          </template>
          <span v-else class="muted">OMS 无明细</span>
        </div>

        <!-- ③ 轨迹 -->
        <div class="cell cell--trace">
          <div class="trace-info" :class="{ 'is-bad': r.logistics.currentAbnormal }" :title="r.logistics.latest.info">
            {{ r.logistics.latest.info || "—" }}
          </div>
          <div class="trace-time font-din">{{ shortTime(r.logistics.latest.time) }}</div>
          <div v-if="r.issues.length" class="trace-flag">
            <span v-for="i in r.issues" :key="i" :class="['flag', i.includes('未上架') ? 'flag--warn' : 'flag--info']">
              {{ i }}
            </span>
          </div>
        </div>
      </div>
    </div>
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
    router.push("/temu/shipment-track");
  }

  /** 大屏只有一小块，全量 242 票放不下 ⇒ 已到港按日期倒序取前 6 条（不滚动也能看全） */
  const MAX_ROWS = 6;

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

  const summary = computed(
    () =>
      data.value?.summary ?? {
        arrived: 0,
        notArrived: 0,
        abnormal: 0,
        omsOnShelf: 0,
      }
  );

  /**
   * 已到港的票，按到港日期倒序（最近的先看）。
   * 同一天内按 OMS 状态排：已上架在后、待入库在前 —— 待入库才是要催的。
   */
  const rows = computed<ShipmentRow[]>(() => {
    const all = data.value?.rows ?? [];
    return all
      .filter((r) => r.arrived)
      .sort((a, b) => {
        const d = (b.arriveDate || "").localeCompare(a.arriveDate || "");
        if (d) return d;
        return (a.oms.status ?? 99) - (b.oms.status ?? 99);
      })
      .slice(0, MAX_ROWS);
  });

  const shortDate = (d?: string) => (d ? String(d).slice(5) : "—");
  const shortTime = (t?: string) => (t ? String(t).slice(5, 16) : "");

  const omsCls = (s?: number) =>
    s === 4 ? "oms-pill--ok" : s === 1 ? "oms-pill--wait" : s === 5 ? "oms-pill--bad" : "oms-pill--info";

  /** 仓库名太长（ZOO WAREHOUSE ⑥美西），大屏只留能区分的短名 */
  const shortWh = (w?: string) => {
    const s = String(w || "");
    if (s.includes("ZOO") || s.includes("⑥")) return "恒信美西";
    return s.replace(/^.*·/, "");
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

  /* ---------------- 顶部三个数 ---------------- */
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

    &--run {
      background: #6b8cb5;
    }

    &--bad {
      background: #ff5252;
      box-shadow: 0 0 5px #ff5252;
    }
  }

  /* ---------------- 三列表 ---------------- */
  .grid-head,
  .grid-row {
    display: grid;
    /* 已到港 26% / 产品 38% / 轨迹 36% —— 轨迹那列字最多，给它留够 */
    grid-template-columns: 26% 38% 36%;
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
    /* 面板是定高的，最后一行常被截半截 —— 底部加一层渐隐，读起来像"还有更多"，
       而不是像渲染坏了（点右上角「货件跟踪 →」看全量） */
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

  /* ① 已到港 */
  .port-date {
    font-size: 12px;
    font-weight: 700;
    color: #00eaff;
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

  /* ③ 轨迹 */
  .trace-info {
    color: #a8c6e8;
    line-height: 1.35;
    /* 最新节点最长一句能到 30 多个字，两行截断，悬停看全文 */
    display: -webkit-box;
    -webkit-line-clamp: 2;
    -webkit-box-orient: vertical;
    overflow: hidden;

    &.is-bad {
      color: #ff8a3d;
      font-weight: 600;
    }
  }

  .trace-time {
    font-size: 9px;
    color: #5f80a8;
  }

  .trace-flag {
    display: flex;
    flex-wrap: wrap;
    gap: 3px;
    margin-top: 1px;
  }

  .flag {
    font-size: 8px;
    padding: 0 3px;
    border-radius: 2px;
    line-height: 12px;

    &--warn {
      color: #e6a23c;
      background: rgba(230, 162, 60, 0.14);
    }

    &--info {
      color: #6b8cb5;
      background: rgba(107, 140, 181, 0.14);
    }
  }

  .muted {
    color: #5f80a8;
  }
</style>
