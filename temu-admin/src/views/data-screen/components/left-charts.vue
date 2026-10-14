<template>
  <div class="leftChart flex flex-col gap-3 h-full">
    <!-- 重点关注 SPU 限流监控 -->
    <FocusWatchCard />

    <!-- 限流状态分布与调价差额 -->
    <LimitedDistChart />
  </div>
</template>

<script lang="ts" setup>
import FocusWatchCard from "./focus-watch-card.vue";
import LimitedDistChart from "./limited-dist-chart.vue";

defineOptions({ name: "LeftCharts" });
</script>
