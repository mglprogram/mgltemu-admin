<template>
  <div class="panel-box flex flex-col">
    <div class="panel-title-bar">
      <div class="title-left">
        <span class="indicator-bar" />
        <span class="title-text">双店运营核心大盘与品类结构</span>
      </div>
      <div class="title-right">
        <span class="store-badge bel">BEL: TM-BEL-MGL</span>
        <span class="store-badge bhy">BHY: TM-BHY-YJF</span>
      </div>
    </div>

    <!-- 双店对比卡片 -->
    <div class="store-cards-grid">
      <!-- BEL 店卡片 -->
      <div
        class="store-stat-card bel-box"
        :class="{ active: currentStore === 'BEL' }"
        role="button"
        tabindex="0"
        @click="currentStore = currentStore === 'BEL' ? 'ALL' : 'BEL'"
      >
        <div class="card-head">
          <span class="store-name">BEL 店</span>
          <span class="focus-safe-tag" :class="storeComparison.bel.focusHits ? 'danger' : 'safe'">
            重点款 {{ storeComparison.bel.focusHits ? `${storeComparison.bel.focusHits} 款限流` : '全部安全' }}
          </span>
        </div>
        <div class="stat-columns">
          <div class="stat-col">
            <span class="col-label">在售款数</span>
            <span class="col-val font-din text-[#00eaff]">{{ storeComparison.bel.skc }}</span>
          </div>
          <div class="stat-col">
            <span class="col-label">可售库存</span>
            <span class="col-val font-din text-[#409eff]">{{ formatNum(storeComparison.bel.stock) }}</span>
          </div>
          <div class="stat-col">
            <span class="col-label">7日销量</span>
            <span class="col-val font-din text-[#67c23a]">{{ formatNum(storeComparison.bel.sales7) }}</span>
          </div>
          <div class="stat-col">
            <span class="col-label">限流商品</span>
            <span class="col-val font-din text-[#ff5252]">{{ storeComparison.bel.limited }}</span>
          </div>
        </div>
      </div>

      <!-- BHY 店卡片 -->
      <div
        class="store-stat-card bhy-box"
        :class="{ active: currentStore === 'BHY' }"
        role="button"
        tabindex="0"
        @click="currentStore = currentStore === 'BHY' ? 'ALL' : 'BHY'"
      >
        <div class="card-head">
          <span class="store-name">BHY 店</span>
          <span class="focus-safe-tag" :class="storeComparison.bhy.focusHits ? 'danger' : 'safe'">
            重点款 {{ storeComparison.bhy.focusHits ? `${storeComparison.bhy.focusHits} 款限流` : '全部安全' }}
          </span>
        </div>
        <div class="stat-columns">
          <div class="stat-col">
            <span class="col-label">在售款数</span>
            <span class="col-val font-din text-[#00eaff]">{{ storeComparison.bhy.skc }}</span>
          </div>
          <div class="stat-col">
            <span class="col-label">可售库存</span>
            <span class="col-val font-din text-[#409eff]">{{ formatNum(storeComparison.bhy.stock) }}</span>
          </div>
          <div class="stat-col">
            <span class="col-label">7日销量</span>
            <span class="col-val font-din text-[#67c23a]">{{ formatNum(storeComparison.bhy.sales7) }}</span>
          </div>
          <div class="stat-col">
            <span class="col-label">限流商品</span>
            <span class="col-val font-din text-[#ff5252]">{{ storeComparison.bhy.limited }}</span>
          </div>
        </div>
      </div>
    </div>

    <!-- 品类在售款式分布柱状图 -->
    <div class="cat-chart-container relative">
      <div ref="catChartRef" class="wh-full" />
    </div>
  </div>
</template>

<script lang="ts" setup>
import { ref, onMounted, onUnmounted, watch } from "vue";
import * as echarts from "echarts/core";
import { BarChart } from "echarts/charts";
import { TooltipComponent, GridComponent } from "echarts/components";
import { CanvasRenderer } from "echarts/renderers";
import { useDataScreenData } from "../composables/useDataScreenData";

echarts.use([BarChart, TooltipComponent, GridComponent, CanvasRenderer]);

defineOptions({ name: "StoreOverviewCard" });

const { storeComparison, categoryDist, currentStore } = useDataScreenData();
const catChartRef = ref<HTMLElement | null>(null);
let catChartInstance: echarts.ECharts | null = null;

function formatNum(n: number) {
  return (n || 0).toLocaleString();
}

function initCatChart() {
  if (!catChartRef.value) return;
  if (!catChartInstance) {
    catChartInstance = echarts.init(catChartRef.value, undefined, { renderer: "canvas" });
  }
  updateCatChart();
}

function updateCatChart() {
  if (!catChartInstance) return;
  const list = categoryDist.value;
  const names = list.map((i) => i.name.slice(0, 8));
  const counts = list.map((i) => i.count);
  const stocks = list.map((i) => i.stock);

  const option: echarts.EChartsCoreOption = {
    backgroundColor: "transparent",
    tooltip: {
      trigger: "axis",
      axisPointer: { type: "shadow" },
      backgroundColor: "rgba(10, 24, 52, 0.9)",
      borderColor: "#00eaff",
      textStyle: { color: "#fff", fontSize: 11 },
      formatter: (params: any) => {
        const p1 = params[0];
        const idx = p1.dataIndex;
        return `
          <div style="font-weight:bold;color:#00eaff;margin-bottom:4px">${list[idx]?.name}</div>
          <div style="color:#8fb2dc">在售款数: <b style="color:#fff">${counts[idx]} 款</b></div>
          <div style="color:#8fb2dc">当前库存: <b style="color:#409eff">${formatNum(stocks[idx])} 件</b></div>
        `;
      },
    },
    grid: {
      left: 10,
      right: 16,
      top: 14,
      bottom: 6,
      containLabel: true,
    },
    xAxis: {
      type: "category",
      data: names,
      axisLabel: { color: "#8fb2dc", fontSize: 10, interval: 0 },
      axisLine: { lineStyle: { color: "rgba(46, 96, 153, 0.4)" } },
      axisTick: { show: false },
    },
    yAxis: {
      type: "value",
      name: "在售款数",
      nameTextStyle: { color: "#6b8cb5", fontSize: 10 },
      axisLabel: { color: "#6b8cb5", fontSize: 10 },
      splitLine: { lineStyle: { color: "rgba(46, 96, 153, 0.2)", type: "dashed" } },
    },
    series: [
      {
        name: "在售款数",
        type: "bar",
        data: counts,
        barMaxWidth: 20,
        itemStyle: {
          borderRadius: [4, 4, 0, 0],
          color: new echarts.graphic.LinearGradient(0, 0, 0, 1, [
            { offset: 0, color: "#00eaff" },
            { offset: 1, color: "rgba(0, 120, 255, 0.2)" },
          ]),
        },
        label: {
          show: true,
          position: "top",
          color: "#00eaff",
          fontSize: 10,
          fontFamily: "DIN",
        },
      },
    ],
  };

  catChartInstance.setOption(option);
}

function handleResize() {
  catChartInstance?.resize();
}

watch(categoryDist, () => updateCatChart(), { deep: true });

onMounted(() => {
  setTimeout(initCatChart, 60);
  window.addEventListener("resize", handleResize);
});

onUnmounted(() => {
  window.removeEventListener("resize", handleResize);
  catChartInstance?.dispose();
  catChartInstance = null;
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
  margin-bottom: 8px;
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

.title-right {
  display: flex;
  align-items: center;
  gap: 8px;
}

.store-badge {
  font-size: 10px;
  padding: 1px 6px;
  border-radius: 3px;
  font-family: DIN, sans-serif;

  &.bel {
    background: rgba(0, 234, 255, 0.15);
    color: #00eaff;
    border: 1px solid rgba(0, 234, 255, 0.3);
  }

  &.bhy {
    background: rgba(64, 158, 255, 0.15);
    color: #409eff;
    border: 1px solid rgba(64, 158, 255, 0.3);
  }
}

.store-cards-grid {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 10px;
  margin-bottom: 8px;
}

.store-stat-card {
  background: rgba(14, 34, 72, 0.65);
  border: 1px solid rgba(46, 96, 153, 0.5);
  border-radius: 5px;
  padding: 8px 10px;
  cursor: pointer;
  transition: all 0.2s;

  &:hover {
    border-color: #00eaff;
    box-shadow: 0 0 12px rgba(0, 234, 255, 0.25);
  }

  &.active {
    border-color: #00eaff;
    background: rgba(20, 50, 100, 0.85);
    box-shadow: 0 0 14px rgba(0, 234, 255, 0.4);
  }
}

.card-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 6px;
}

.store-name {
  font-size: 12px;
  font-weight: 700;
  color: #ffffff;
}

.focus-safe-tag {
  font-size: 9px;
  padding: 1px 5px;
  border-radius: 10px;

  &.safe {
    background: rgba(103, 194, 58, 0.2);
    color: #67c23a;
    border: 1px solid rgba(103, 194, 58, 0.35);
  }

  &.danger {
    background: rgba(245, 108, 108, 0.2);
    color: #f56c6c;
    border: 1px solid rgba(245, 108, 108, 0.35);
  }
}

.stat-columns {
  display: grid;
  grid-template-columns: repeat(4, 1fr);
  gap: 6px;
  text-align: center;
}

.stat-col {
  display: flex;
  flex-direction: column;
}

.col-label {
  font-size: 9px;
  color: #8fb2dc;
}

.col-val {
  font-size: 15px;
  font-weight: 700;
  margin-top: 1px;
}

.cat-chart-container {
  height: 140px;
}
</style>
