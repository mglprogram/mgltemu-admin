<!--
  库存跟踪速览（大屏用）

  2026-10-29 用户要求：把大屏左侧「限流状态分布与调价差额」这块
  换成「九部库存快售罄的产品 + 十部库存前三的产品」。

  数据源 = public/data/stock-track.json（scripts/goods/fetch_stock_track.py 每天 17:00 生成）
    · 九部「快售罄」= 按**现货可售天数**升序（实际库存 ÷ 七天日均），
      只取真的在卖（日均 > 0）且现货撑不过 30 天的；现货已断（0 件）排最前。
    · 十部「库存前三」= 按**实际库存（代发库存）**降序取 3 个款号。

  ⚠ 口径：实际库存 = 代发库存（已在海外仓能直接发）；不含代发途中。
-->
<template>
  <div class="panel-box flex-1 flex flex-col min-h-0">
    <div class="panel-title-bar">
      <div class="title-left">
        <span class="indicator-bar" />
        <span class="title-text">库存跟踪 · 九部快售罄 / 十部库存前三</span>
      </div>
      <button type="button" class="more-link" @click="goTrack">
        库存跟踪 →
      </button>
    </div>

    <el-empty
      v-if="!nineRisk.length && !tenTop.length"
      description="库存数据未就绪"
      :image-size="50"
      class="empty-mini"
    />

    <template v-else>
      <!-- 九部：已售罄（尺码级，现货 0 件） -->
      <div class="sec-head">
        <span class="sec-tag sec-tag--nine">九部</span>
        <span class="sec-sub">已售罄（尺码级，现货 0 件）</span>
        <span v-if="nineSoldOut.length" class="sec-badge sec-badge--dead">
          {{ nineSoldOut.length }} 个码
        </span>
      </div>
      <el-empty
        v-if="!nineSoldOut.length"
        description="暂无售罄尺码"
        :image-size="36"
        class="empty-mini"
      />
      <div v-else class="row-list">
        <div v-for="r in nineSoldOut" :key="r.sku" class="row">
          <div class="row-left">
            <span class="code font-din">{{ r.sku }}</span>
            <span class="meta">日均 {{ r.daily.toFixed(0) }}</span>
          </div>
          <span class="val val--dead">0 件</span>
        </div>
      </div>

      <!-- 九部：快售罄 -->
      <div class="sec-head sec-head--gap">
        <span class="sec-tag sec-tag--nine">九部</span>
        <span class="sec-sub">快售罄（现货可售天数）</span>
      </div>
      <div class="row-list">
        <div v-for="r in nineRisk" :key="r.code" class="row">
          <div class="row-left">
            <span class="code font-din">{{ r.code }}</span>
            <span class="meta">现货 {{ num(r.stock) }} · 日均 {{ r.daily.toFixed(0) }}</span>
          </div>
          <span class="val" :class="r.zero ? 'val--dead' : r.days < 15 ? 'val--hot' : 'val--warn'">
            {{ r.zero ? "已断" : r.days + " 天" }}
          </span>
        </div>
      </div>

      <!-- 十部：库存前三 -->
      <div class="sec-head sec-head--gap">
        <span class="sec-tag sec-tag--ten">十部</span>
        <span class="sec-sub">库存前三（实际库存）</span>
      </div>
      <div class="row-list">
        <div v-for="(r, i) in tenTop" :key="r.code" class="row">
          <div class="row-left">
            <span class="rank" :class="'rank--' + (i + 1)">{{ i + 1 }}</span>
            <span class="code font-din">{{ r.code }}</span>
            <span class="meta">日均 {{ r.daily.toFixed(0) }}</span>
          </div>
          <span class="val val--ok">{{ num(r.stock) }}</span>
        </div>
      </div>
    </template>
  </div>
</template>

<script lang="ts" setup>
  import { ref, computed, onMounted } from "vue";
  import { useRouter } from "vue-router";
  import StockTrackAPI from "@/api/temu/stock-track";
  import type { StockTrackData, TrackStyle } from "@/api/temu/stock-track";

  defineOptions({ name: "StockRiskPanel" });

  const router = useRouter();

  function goTrack() {
    router.push("/temu/stock-track");
  }

  const data = ref<StockTrackData | null>(null);

  async function load() {
    try {
      data.value = await StockTrackAPI.getStockTrack();
    } catch {
      data.value = null; // 拿不到就显示「未就绪」，不影响大屏其它面板
    }
  }
  onMounted(load);

  const num = (v?: number | null) =>
    v == null ? "—" : Math.round(Number(v)).toLocaleString("zh-CN");

  /** 一个款号的现货可售天数（实际库存 ÷ 七天日均） */
  function sellDays(st: TrackStyle) {
    const daily = st.totals.daily || 0;
    if (daily <= 0) return Number.POSITIVE_INFINITY;
    return Math.round((st.totals.stock || 0) / daily);
  }

  /**
   * 九部已售罄：**尺码级**（实际库存 = 0 的单个 SKU），不是款号级。
   *
   * 为什么按尺码算：九部这 7 个款号里没有整款售罄的，售罄都发生在个别尺码上
   * （实测 2026-10-29：204207-4SetB 的 S/M/L/XL/XXL 五个码全 0，
   *   204203-4SetB-XXL 也是 0）。按款号聚合会把这些完全淹没
   *   （204207 整款还有 1,637 件，看着一点也不紧张）。
   *
   * 排序：日均降序 —— 卖得越快还断着，越该优先补。最多 6 条。
   */
  const nineSoldOut = computed(() => {
    const src = data.value?.sources?.nine;
    if (!src) return [];
    const out: { sku: string; daily: number }[] = [];
    for (const st of src.styles) {
      for (const v of st.variants) {
        for (const r of v.rows) {
          if ((r.stock || 0) <= 0) out.push({ sku: r.sku, daily: r.daily || 0 });
        }
      }
    }
    return out.sort((a, b) => b.daily - a.daily).slice(0, 6);
  });

  /**
   * 九部快售罄：只保留「真的在卖 + 现货撑不过 30 天」的款号，
   * 按可售天数升序；现货已断的排最前。最多 4 条。
   */
  const nineRisk = computed(() => {
    const src = data.value?.sources?.nine;
    if (!src) return [];
    return src.styles
      .filter((st) => st.variants.length)
      .map((st) => ({
        code: st.code,
        stock: st.totals.stock || 0,
        daily: st.totals.daily || 0,
        zero: (st.totals.stock || 0) <= 0,
        days: sellDays(st),
      }))
      .filter((r) => r.zero || (r.daily > 0 && r.days < 30))
      .sort((a, b) => {
        if (a.zero !== b.zero) return a.zero ? -1 : 1;
        return a.days - b.days;
      })
      .slice(0, 4);
  });

  /** 十部库存前三：按实际库存降序 */
  const tenTop = computed(() => {
    const src = data.value?.sources?.ten;
    if (!src) return [];
    return src.styles
      .filter((st) => st.variants.length)
      .map((st) => ({
        code: st.code,
        stock: st.totals.stock || 0,
        daily: st.totals.daily || 0,
      }))
      .sort((a, b) => b.stock - a.stock)
      .slice(0, 3);
  });
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

  .sec-head {
    display: flex;
    align-items: center;
    gap: 6px;
    padding: 4px 2px;

    &--gap {
      margin-top: 6px;
      border-top: 1px dashed rgba(46, 96, 153, 0.35);
      padding-top: 8px;
    }
  }

  .sec-tag {
    font-size: 10px;
    font-weight: 700;
    padding: 1px 6px;
    border-radius: 3px;

    &--nine {
      color: #00eaff;
      background: rgba(0, 234, 255, 0.12);
      border: 1px solid rgba(0, 234, 255, 0.35);
    }

    &--ten {
      color: #a855f7;
      background: rgba(168, 85, 247, 0.12);
      border: 1px solid rgba(168, 85, 247, 0.35);
    }
  }

  .sec-sub {
    font-size: 10px;
    color: #6b8cb5;
  }

  .sec-badge {
    margin-left: auto;
    font-size: 9px;
    font-weight: 700;
    padding: 1px 6px;
    border-radius: 3px;

    &--dead {
      color: #ff5252;
      background: rgba(255, 82, 82, 0.14);
      border: 1px solid rgba(255, 82, 82, 0.4);
    }
  }

  .row-list {
    display: flex;
    flex-direction: column;
    gap: 4px;
  }

  .row {
    display: flex;
    align-items: center;
    justify-content: space-between;
    background: rgba(14, 32, 68, 0.5);
    padding: 4px 6px;
    border-radius: 3px;
    font-size: 11px;
  }

  .row-left {
    display: flex;
    align-items: center;
    gap: 6px;
    min-width: 0;
  }

  .code {
    color: #c8e4ff;
    font-weight: 600;
  }

  .meta {
    font-size: 9px;
    color: #6b8cb5;
  }

  .rank {
    width: 14px;
    height: 14px;
    line-height: 14px;
    text-align: center;
    border-radius: 3px;
    font-size: 9px;
    font-weight: 700;
    color: #070c14;
    background: #6b8cb5;

    &--1 {
      background: #ffd24d;
    }

    &--2 {
      background: #cfd8e3;
    }

    &--3 {
      background: #d08b4f;
    }
  }

  .val {
    font-weight: 700;
    font-variant-numeric: tabular-nums;

    &--dead {
      color: #ff5252;
    }

    &--hot {
      color: #ff8a3d;
    }

    &--warn {
      color: #e6a23c;
    }

    &--ok {
      color: #00eaff;
    }
  }
</style>
