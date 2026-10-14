<template>
  <div class="panel-box flex flex-col">
    <div class="panel-title-bar">
      <div class="title-left">
        <span class="indicator-bar green" />
        <span class="title-text">预售建议与在途到货覆盖</span>
      </div>
      <button
        type="button"
        class="more-link"
        @click="router.push({ path: '/temu/on-sale', query: { view: 'soldout' } })"
      >
        售罄清单 →
      </button>
    </div>

    <!-- 预售判定标签栏：三块都可点击，落到在售款明细页对应视图 -->
    <div class="verdict-bar">
      <button
        type="button"
        class="verdict-item ready"
        title="点击查看已售罄/即将售罄清单（这些尺码有销量，优先处理）"
        @click="jump('soldout')"
      >
        <span class="v-num font-din">{{ readyList.length }}</span>
        <span class="v-label">建议开预售</span>
      </button>
      <button
        type="button"
        class="verdict-item add-stock"
        title="点击查看可加库存清单"
        @click="jump('todo')"
      >
        <span class="v-num font-din">{{ addStockList.length }}</span>
        <span class="v-label">建议先加库存</span>
      </button>
      <button
        type="button"
        class="verdict-item restock"
        title="点击查看低库存/补货清单"
        @click="jump('low')"
      >
        <span class="v-num font-din">{{ restockList.length }}</span>
        <span class="v-label">海外需补货</span>
      </button>
    </div>

    <!-- 到货批次跟踪列表：优先展示**已售罄/即将售罄**（有销量，最该先处理） -->
    <div class="inbound-list overflow-y-auto">
      <div
        v-for="item in trackList"
        :key="item.store + item.skuCode"
        class="inbound-row"
        :class="{ 'is-soldout': item.status === '已售罄' }"
      >
        <div class="inbound-left">
          <div class="sku-head">
            <span class="sku-code font-din">{{ item.skuCode }}</span>
            <span class="size-tag">{{ item.size || '均码' }}</span>
            <span
              v-if="item.status"
              class="status-tag"
              :class="item.status === '已售罄' ? 'is-danger' : 'is-warn'"
            >
              {{ item.status }}
            </span>
          </div>
          <div class="arrival-info">
            <span class="text-[#8fb2dc]">预计到货:</span>
            <span class="font-din text-[#00eaff]">{{ item.arrivalDate || '近期到货' }}</span>
            <span v-if="item.arrivalDays !== null" class="days-pill" :class="item.arrivalDays <= 3 ? 'urgent' : 'normal'">
              {{ item.arrivalDays > 0 ? `${item.arrivalDays}天后` : '今日/已到' }}
            </span>
          </div>
        </div>

        <div class="inbound-right text-right">
          <div class="qty font-din text-[#67c23a] font-bold">
            +{{ item.transit || item.arrivalTicketQty || 0 }} 件
          </div>
          <div class="daily-need font-din text-[#6b8cb5] text-9px">
            日均需求: {{ item.dailyNeed ?? 0 }}/天
          </div>
        </div>
      </div>
    </div>
  </div>
</template>

<script lang="ts" setup>
import { computed } from "vue";
import { useRouter } from "vue-router";
import { useDataScreenData } from "../composables/useDataScreenData";

defineOptions({ name: "PresaleInboundPanel" });

const router = useRouter();
const { presaleRows } = useDataScreenData();

const readyList = computed(() => {
  return presaleRows.value.filter((r) => r.verdict === "可开预售" || (r.arrivalDate && r.status === "已售罄"));
});

const addStockList = computed(() => {
  return presaleRows.value.filter((r) => r.verdict === "先加库存");
});

const restockList = computed(() => {
  return presaleRows.value.filter((r) => r.verdict === "要补货");
});

/**
 * 列表展示顺序：**已售罄/即将售罄排最前**。
 *
 * 用户口径（2026-10-14）：这些货号-尺码是"有销量的"，最该先处理 ——
 * 所以不能只显示「可开预售」（那一档经常是 0，面板就空了）。
 * 排序：已售罄 → 即将售罄 → 其余；同档按预测销量降序（销量大的先看）。
 */
const trackList = computed(() => {
  const rank = (s: string) =>
    s === "已售罄" ? 0 : s === "即将售罄" ? 1 : 2;
  return [...presaleRows.value]
    .sort((a, b) => {
      const d = rank(a.status) - rank(b.status);
      if (d !== 0) return d;
      return (b.forecast7 ?? 0) - (a.forecast7 ?? 0);
    })
    .slice(0, 3);
});

/**
 * 跳到「在售款明细」页对应视图。
 *
 * 用户口径：预售/加库存的明细都从 /temu/on-sale 看得到，
 * 所以三块都指过去（不再指向已废弃的 /temu/presale 独立页），
 * 用 ?view= 落到具体视图。
 */
function jump(view: "soldout" | "todo" | "low") {
  router.push({ path: "/temu/on-sale", query: { view } });
}
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
  padding-bottom: 6px;
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
  background: #67c23a;
  box-shadow: 0 0 8px #67c23a;
  border-radius: 2px;
}

.title-text {
  font-size: 13px;
  font-weight: 600;
  color: #67c23a;
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

.verdict-bar {
  display: grid;
  grid-template-columns: repeat(3, 1fr);
  gap: 6px;
  margin-bottom: 8px;
}

.verdict-item {
  display: flex;
  flex-direction: column;
  align-items: center;
  padding: 4px 0;
  border-radius: 4px;
  // 现在是 <button>，去掉浏览器默认外观
  font-family: inherit;
  cursor: pointer;
  transition: all 0.2s ease;

  &:hover {
    transform: translateY(-1px);
    filter: brightness(1.35);
  }

  &.ready {
    background: rgba(0, 234, 255, 0.12);
    border: 1px solid rgba(0, 234, 255, 0.3);
    .v-num { color: #00eaff; }
  }

  &.add-stock {
    background: rgba(103, 194, 58, 0.12);
    border: 1px solid rgba(103, 194, 58, 0.3);
    .v-num { color: #67c23a; }
  }

  &.restock {
    background: rgba(230, 162, 60, 0.12);
    border: 1px solid rgba(230, 162, 60, 0.3);
    .v-num { color: #e6a23c; }
  }
}

.v-num {
  font-size: 15px;
  font-weight: 700;
  line-height: 1.2;
}

.v-label {
  font-size: 9px;
  color: #8fb2dc;
}

.inbound-list {
  display: flex;
  flex-direction: column;
  gap: 5px;
  max-height: 125px;

  &::-webkit-scrollbar {
    width: 3px;
  }
  &::-webkit-scrollbar-thumb {
    background: rgba(103, 194, 58, 0.25);
    border-radius: 2px;
  }
}

.inbound-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  background: rgba(14, 32, 68, 0.5);
  border: 1px solid rgba(103, 194, 58, 0.2);
  border-radius: 4px;
  padding: 4px 8px;
  font-size: 11px;

  // 已售罄：换红边，一眼看出这条最急
  &.is-soldout {
    border-color: rgba(245, 108, 108, 0.45);
    background: rgba(60, 20, 28, 0.45);
  }
}

// 售罄状态小标签
.status-tag {
  font-size: 8px;
  padding: 0 4px;
  border-radius: 2px;

  &.is-danger {
    background: rgba(245, 108, 108, 0.22);
    color: #ff5252;
  }
  &.is-warn {
    background: rgba(230, 162, 60, 0.22);
    color: #e6a23c;
  }
}

.inbound-left {
  display: flex;
  flex-direction: column;
  gap: 2px;
}

.sku-head {
  display: flex;
  align-items: center;
  gap: 5px;
}

.sku-code {
  font-size: 11px;
  font-weight: 600;
  color: #ffffff;
}

.size-tag {
  font-size: 9px;
  padding: 0 4px;
  border-radius: 2px;
  background: rgba(0, 234, 255, 0.15);
  color: #00eaff;
}

.arrival-info {
  display: flex;
  align-items: center;
  gap: 4px;
  font-size: 10px;
}

.days-pill {
  font-size: 8px;
  padding: 0 4px;
  border-radius: 2px;
  margin-left: 2px;

  &.urgent {
    background: rgba(245, 108, 108, 0.2);
    color: #ff5252;
  }
  &.normal {
    background: rgba(103, 194, 58, 0.2);
    color: #67c23a;
  }
}
</style>
