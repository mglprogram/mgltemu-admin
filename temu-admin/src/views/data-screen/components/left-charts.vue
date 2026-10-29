<template>
  <div class="leftChart flex flex-col gap-3 h-full">
    <!-- 重点关注 SPU 限流监控 -->
    <FocusWatchCard />

    <!-- 库存跟踪：九部快售罄 + 十部库存前三（2026-10-29 替换原「限流状态分布与调价差额」） -->
    <StockRiskPanel />
  </div>
</template>

<script lang="ts" setup>
import FocusWatchCard from "./focus-watch-card.vue";
import StockRiskPanel from "./stock-risk-panel.vue";

defineOptions({ name: "LeftCharts" });
</script>
