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
        @click="router.push('/temu/presale')"
      >
        预售管理 →
      </button>
    </div>

    <!-- 预售判定标签栏 -->
    <div class="verdict-bar">
      <div class="verdict-item ready">
        <span class="v-num font-din">{{ readyList.length }}</span>
        <span class="v-label">建议开预售</span>
      </div>
      <div class="verdict-item add-stock">
        <span class="v-num font-din">{{ addStockList.length }}</span>
        <span class="v-label">建议先加库存</span>
      </div>
      <div class="verdict-item restock">
        <span class="v-num font-din">{{ restockList.length }}</span>
        <span class="v-label">海外需补货</span>
      </div>
    </div>

    <!-- 到货批次跟踪列表 -->
    <div class="inbound-list overflow-y-auto">
      <div
        v-for="item in readyList.slice(0, 3)"
        :key="item.skuId"
        class="inbound-row"
      >
        <div class="inbound-left">
          <div class="sku-head">
            <span class="sku-code font-din">{{ item.skuCode }}</span>
            <span class="size-tag">{{ item.size || '均码' }}</span>
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
