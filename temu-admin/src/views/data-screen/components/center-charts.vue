<template>
  <div class="centerChart flex flex-col gap-3 h-full">
    <!-- 双店大盘概览与品类结构 -->
    <StoreOverviewCard />

    <!-- 在售热销榜 Top 10 -->
    <TopGoodsTable />

    <!-- 订单分布（美国地图）——2026-10-14 用户要求新增 -->
    <UsOrderMap />
  </div>
</template>

<script lang="ts" setup>
import StoreOverviewCard from "./store-overview-card.vue";
import TopGoodsTable from "./top-goods-table.vue";
import UsOrderMap from "./us-order-map.vue";

defineOptions({ name: "CenterCharts" });
</script>
