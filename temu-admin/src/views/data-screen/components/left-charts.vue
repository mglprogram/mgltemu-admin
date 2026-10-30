<template>
  <div class="leftChart flex flex-col gap-3 h-full">
    <!-- 重点关注 SPU 限流监控 -->
    <FocusWatchCard />

    <!-- 货件跟踪：已到港 / 产品 / 轨迹（2026-10-30 替换原「库存跟踪」面板） -->
    <ShipmentTrackPanel />
  </div>
</template>

<script lang="ts" setup>
import FocusWatchCard from "./focus-watch-card.vue";
import ShipmentTrackPanel from "./shipment-track-panel.vue";

defineOptions({ name: "LeftCharts" });
</script>
