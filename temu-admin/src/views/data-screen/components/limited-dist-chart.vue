<template>
  <div class="panel-box flex-1 flex flex-col min-h-0">
    <div class="panel-title-bar">
      <div class="title-left">
        <span class="indicator-bar" />
        <span class="title-text">限流状态分布与调价差额</span>
      </div>
    </div>

    <!-- 图表容器 -->
    <div class="chart-container flex-1 min-h-0 relative">
      <div ref="chartRef" class="wh-full" />
    </div>

    <!-- 底部建议调价 Top 3 -->
    <div class="bottom-gap-list">
      <div class="gap-header">
        <span class="text-10px text-[#8fb2dc]">需降价前列商品</span>
        <span class="text-10px text-[#6b8cb5]">差额 (当前 - 建议)</span>
      </div>
      <div
        v-for="item in topGapItems"
        :key="item.code"
        class="gap-row"
      >
        <div class="gap-info">
          <span class="code font-din">{{ item.code }}</span>
          <span class="flow-mini">{{ item.flowStatus }}</span>
        </div>
        <div class="gap-price font-din text-[#ff5252]">
          -${{ item.priceGap?.toFixed(2) }}
        </div>
      </div>
    </div>
  </div>
</template>

<script lang="ts" setup>
import { ref, onMounted, onUnmounted, watch, computed } from "vue";
import * as echarts from "echarts/core";
import { PieChart } from "echarts/charts";
import { TooltipComponent, LegendComponent } from "echarts/components";
import { CanvasRenderer } from "echarts/renderers";
import { useDataScreenData } from "../composables/useDataScreenData";

echarts.use([PieChart, TooltipComponent, LegendComponent, CanvasRenderer]);

defineOptions({ name: "LimitedDistChart" });

const { limitedFlowDist, limitedData, currentStore } = useDataScreenData();
const chartRef = ref<HTMLElement | null>(null);
let chartInstance: echarts.ECharts | null = null;

const topGapItems = computed(() => {
  const l = limitedData.value;
  if (!l?.stores) return [];
  const items =
    currentStore.value === "ALL"
      ? [...(l.stores.BEL?.items || []), ...(l.stores.BHY?.items || [])]
      : l.stores[currentStore.value]?.items || [];

  return [...items]
    .filter((i) => i.priceGap && i.priceGap > 0)
    .sort((a, b) => (b.priceGap || 0) - (a.priceGap || 0))
    .slice(0, 3);
});

function initChart() {
  if (!chartRef.value) return;
  if (!chartInstance) {
    chartInstance = echarts.init(chartRef.value, undefined, { renderer: "canvas" });
  }
  updateChart();
}

function updateChart() {
  if (!chartInstance) return;
  const data = limitedFlowDist.value;

  const colorPalette = ["#ff5252", "#e6a23c", "#00eaff", "#409eff", "#a855f7", "#67c23a"];

  const option: echarts.EChartsCoreOption = {
    backgroundColor: "transparent",
    tooltip: {
      trigger: "item",
      formatter: "{b}: {c} 款 ({d}%)",
      backgroundColor: "rgba(10, 24, 52, 0.9)",
      borderColor: "#00eaff",
      textStyle: { color: "#fff", fontSize: 11 },
    },
    legend: {
      orient: "vertical",
      right: 4,
      top: "middle",
      itemWidth: 8,
      itemHeight: 8,
      textStyle: { color: "#8fb2dc", fontSize: 10 },
    },
    series: [
      {
        name: "流量状态",
        type: "pie",
        radius: ["42%", "68%"],
        center: ["36%", "50%"],
        avoidLabelOverlap: false,
        itemStyle: {
          borderRadius: 4,
          borderColor: "#070c14",
          borderWidth: 2,
        },
        label: { show: false },
        emphasis: {
          label: {
            show: true,
            fontSize: 12,
            fontWeight: "bold",
            color: "#00eaff",
          },
        },
        data: data.map((d, index) => ({
          name: d.name,
          value: d.value,
          itemStyle: { color: colorPalette[index % colorPalette.length] },
        })),
      },
    ],
  };

  chartInstance.setOption(option);
}

function handleResize() {
  chartInstance?.resize();
}

watch(limitedFlowDist, () => updateChart(), { deep: true });

onMounted(() => {
  setTimeout(initChart, 50);
  window.addEventListener("resize", handleResize);
});

onUnmounted(() => {
  window.removeEventListener("resize", handleResize);
  chartInstance?.dispose();
  chartInstance = null;
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

.chart-container {
  height: 160px;
}

.bottom-gap-list {
  margin-top: 6px;
  padding-top: 6px;
  border-top: 1px dashed rgba(46, 96, 153, 0.35);
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.gap-header {
  display: flex;
  justify-content: space-between;
  padding: 0 4px;
}

.gap-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  background: rgba(14, 32, 68, 0.5);
  padding: 4px 6px;
  border-radius: 3px;
  font-size: 11px;
}

.gap-info {
  display: flex;
  align-items: center;
  gap: 6px;
}

.code {
  color: #c8e4ff;
  font-weight: 600;
}

.flow-mini {
  font-size: 9px;
  color: #6b8cb5;
}

.gap-price {
  font-weight: 700;
}
</style>
