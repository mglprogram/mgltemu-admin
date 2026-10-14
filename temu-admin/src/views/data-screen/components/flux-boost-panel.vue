<template>
  <div class="panel-box flex flex-col">
    <div class="panel-title-bar">
      <div class="title-left">
        <span class="indicator-bar purple" />
        <span class="title-text">流量加速器 (Flux Boost) 监控</span>
      </div>
      <button
        type="button"
        class="more-link"
        @click="router.push('/temu/flux-boost')"
      >
        加速管理 →
      </button>
    </div>

    <!-- 加速效果概览 -->
    <div class="boost-summary-row">
      <div class="boost-metric">
        <span class="bm-label">加速进行中</span>
        <span class="bm-val text-[#c084fc] font-din">{{ metrics.fluxActiveCount }} 款</span>
      </div>
      <div class="boost-metric">
        <span class="bm-label">待开启商机</span>
        <span class="bm-val text-[#00eaff] font-din">{{ metrics.fluxPendingCount }} 款</span>
      </div>
      <div class="boost-metric">
        <span class="bm-label">覆盖店铺</span>
        <span class="bm-val text-[#67c23a] font-din">BEL · BHY</span>
      </div>
    </div>

    <!-- 已开启加速款式调价抽样对比 -->
    <div class="boost-items-list overflow-y-auto">
      <div
        v-for="item in activeBoostList.slice(0, 4)"
        :key="item.spuId"
        class="boost-item-row"
      >
        <div class="bi-left">
          <span class="spu font-din">SPU: {{ item.spuId }}</span>
          <span class="title">{{ item.title || '流量加速商品' }}</span>
        </div>
        <div class="bi-right">
          <div class="price-diff">
            <span class="orig font-din">${{ item.origPrice ?? '--' }}</span>
            <span class="arrow font-din">→</span>
            <span class="after font-din text-[#c084fc]">${{ item.afterPrice ?? '--' }}</span>
          </div>
          <span v-if="item.drop" class="drop-tag font-din">
            降 ${{ item.drop.toFixed(2) }}
          </span>
        </div>
      </div>
    </div>
  </div>
</template>

<script lang="ts" setup>
import { computed } from "vue";
import { useRouter } from "vue-router";
import { useDataScreenData } from "../composables/useDataScreenData";

defineOptions({ name: "FluxBoostPanel" });

const router = useRouter();
const { metrics, fluxBoostItems } = useDataScreenData();

const activeBoostList = computed(() => {
  return fluxBoostItems.value || [];
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
  background: #c084fc;
  box-shadow: 0 0 8px #c084fc;
  border-radius: 2px;
}

.title-text {
  font-size: 13px;
  font-weight: 600;
  color: #c084fc;
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

.boost-summary-row {
  display: grid;
  grid-template-columns: repeat(3, 1fr);
  gap: 6px;
  margin-bottom: 8px;
}

.boost-metric {
  background: rgba(14, 34, 72, 0.6);
  border-radius: 4px;
  padding: 4px 6px;
  text-align: center;
}

.bm-label {
  display: block;
  font-size: 9px;
  color: #8fb2dc;
}

.bm-val {
  font-size: 13px;
  font-weight: 700;
}

.boost-items-list {
  display: flex;
  flex-direction: column;
  gap: 5px;
  max-height: 125px;

  &::-webkit-scrollbar {
    width: 3px;
  }
  &::-webkit-scrollbar-thumb {
    background: rgba(192, 132, 252, 0.25);
    border-radius: 2px;
  }
}

.boost-item-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  background: rgba(14, 32, 68, 0.5);
  border: 1px solid rgba(192, 132, 252, 0.2);
  border-radius: 4px;
  padding: 4px 8px;
  font-size: 11px;
}

.bi-left {
  display: flex;
  flex-direction: column;
  max-width: 170px;
}

.spu {
  font-size: 10px;
  color: #c8e4ff;
  font-weight: 600;
}

.title {
  font-size: 9px;
  color: #6b8cb5;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.bi-right {
  display: flex;
  flex-direction: column;
  align-items: flex-end;
}

.price-diff {
  display: flex;
  align-items: center;
  gap: 4px;
  font-size: 11px;
}

.orig {
  color: #8fb2dc;
  text-decoration: line-through;
  font-size: 10px;
}

.arrow {
  color: #6b8cb5;
  font-size: 9px;
}

.after {
  font-weight: 700;
}

.drop-tag {
  font-size: 8px;
  color: #67c23a;
  background: rgba(103, 194, 58, 0.15);
  padding: 0 4px;
  border-radius: 2px;
  margin-top: 1px;
}
</style>
