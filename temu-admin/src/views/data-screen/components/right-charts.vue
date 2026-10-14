<template>
  <div class="rightChart flex flex-col gap-3 h-full">
    <!-- 流量加速器 (Flux Boost) 监控 -->
    <FluxBoostPanel />

    <!-- 预售建议与在途到货覆盖 -->
    <PresaleInboundPanel />

    <!-- 自动化任务与定时巡检 -->
    <AutomationTaskPanel />
  </div>
</template>

<script lang="ts" setup>
import FluxBoostPanel from "./flux-boost-panel.vue";
import PresaleInboundPanel from "./presale-inbound-panel.vue";
import AutomationTaskPanel from "./automation-task-panel.vue";

defineOptions({ name: "RightCharts" });
</script>
