<!--
  数据大屏 · 订单分布（美国地图）

  2026-10-14 用户要求：在 /dashboard 数据大屏加一个「订单分布的美国地图」。

  数据源：public/data/order-geo.json —— 由 scripts/pw_orders/run_pw_order_geo.py
  从帕沃尔海外仓(powerwarehouse.net)发货列表 + 逐单「收件地址」聚合而来。
  随大屏顶部的「店铺切换」(ALL/BEL/BHY) 联动。

  ⚠ 地图底图：public/map/usa.json（Apache ECharts examples 的 USA.json，52 个 feature，
     feature 名字就是州全名，与订单数据里的 state 全名一一对应）。
-->
<template>
  <div class="panel-box flex flex-col min-h-0">
    <div class="panel-title-bar">
      <div class="title-left">
        <span class="indicator-bar" />
        <span class="title-text">订单分布 · 美国</span>
      </div>
      <span class="title-right">
        {{ geoReady ? `${num(geoTotal)} 单 / ${geoStates.length} 州` : "暂无数据" }}
      </span>
    </div>

    <div class="map-row flex-1 min-h-0">
      <div ref="chartRef" class="map-box" />
      <div class="state-list">
        <div class="state-list__head">
          <span>主要出单州</span>
          <span>单量</span>
        </div>
        <div v-for="(s, i) in topStates" :key="s.code" class="state-row">
          <span class="state-row__idx" :class="{ 'is-top': i < 3 }">{{ i + 1 }}</span>
          <span class="state-row__name">{{ s.name }}</span>
          <span class="state-row__bar">
            <i :style="{ width: barWidth(s), background: barColor(i) }" />
          </span>
          <span class="state-row__num font-din">{{ num(s.orders) }}</span>
        </div>
        <div v-if="!topStates.length" class="state-row state-row--empty">暂无订单分布数据</div>
      </div>
    </div>
  </div>
</template>

<script lang="ts" setup>
import { computed, onMounted, onUnmounted, ref, watch, nextTick } from "vue";
import * as echarts from "echarts/core";
import { MapChart } from "echarts/charts";
import { TooltipComponent, VisualMapComponent } from "echarts/components";
import { CanvasRenderer } from "echarts/renderers";
import { useDataScreenData } from "../composables/useDataScreenData";

echarts.use([MapChart, TooltipComponent, VisualMapComponent, CanvasRenderer]);

defineOptions({ name: "UsOrderMap" });

const MAP_NAME = "temu-usa";

const { geoStates, orderGeoData } = useDataScreenData();
const chartRef = ref<HTMLElement | null>(null);
let chart: echarts.ECharts | null = null;
let registered = false;
let mapJson: unknown = null;

const num = (n?: number) => (n || 0).toLocaleString("en-US");

const geoReady = computed(() => !!orderGeoData.value);
const geoTotal = computed(() => {
  const g = orderGeoData.value;
  if (!g) return 0;
  return geoStates.value.reduce((a, s) => a + s.orders, 0);
});
const topStates = computed(() => geoStates.value.slice(0, 7));
const maxOrders = computed(() => geoStates.value[0]?.orders || 1);

const barWidth = (s: { orders: number }) => `${Math.max(4, (s.orders / maxOrders.value) * 100)}%`;
const barColor = (i: number) => ["#00eaff", "#409eff", "#67c23a", "#e6a23c", "#a855f7"][Math.min(i, 4)];

async function ensureMap(): Promise<boolean> {
  if (registered) return true;
  if (!mapJson) {
    try {
      const res = await fetch(`${import.meta.env.BASE_URL}map/usa.json`, { cache: "force-cache" });
      mapJson = await res.json();
    } catch (e) {
      console.error("[us-order-map] 加载美国地图失败:", e);
      return false;
    }
  }
  echarts.registerMap(MAP_NAME, mapJson as never);
  registered = true;
  return true;
}

function updateChart() {
  // ⚠ 地图没注册完就 setOption 会抛 "Cannot read properties of undefined (reading 'regions')"
  //   （geoStates 的 watch 可能早于 ensureMap 的 await 完成）→ 必须先判 registered。
  if (!chart || !registered) return;
  const states = geoStates.value;
  const max = Math.max(1, ...states.map((s) => s.orders));
  chart.setOption(
    {
      backgroundColor: "transparent",
      tooltip: {
        trigger: "item",
        backgroundColor: "rgba(10, 24, 52, 0.92)",
        borderColor: "#00eaff",
        textStyle: { color: "#fff", fontSize: 11 },
        formatter: (p: { name: string; data?: { value?: number } }) => {
          const v = p.data?.value;
          return v === undefined || v === null ? `${p.name}<br/>无出单` : `${p.name}<br/>${num(v)} 单`;
        },
      },
      visualMap: {
        show: false,
        min: 0,
        max,
        inRange: { color: ["#123a63", "#1d6fd6", "#00b7ff", "#00eaff"] },
      },
      series: [
        {
          name: "订单数",
          type: "map",
          map: MAP_NAME,
          roam: false,
          layoutCenter: ["50%", "52%"],
          /**
           * layoutSize 的百分比是相对**容器较短边**（这里高度）算的，而美国地图是「宽扁」比例。
           * 给 100% 时地图只有 ~1.6×高 的宽度，面板左右会空一大截（实测两侧各空 ~150px）；
           * 给 140% 让地图宽度吃掉面板宽度，视觉上与右侧榜单齐平（ECharts 会裁剪，不会溢出）。
           */
          layoutSize: "140%",
          label: { show: false },
          itemStyle: { borderColor: "rgba(0, 234, 255, 0.35)", borderWidth: 0.6, areaColor: "#10243f" },
          emphasis: {
            label: { show: true, fontSize: 9, color: "#fff" },
            itemStyle: { areaColor: "#ffd666" },
          },
          data: states.map((s) => ({ name: s.name, value: s.orders })),
        },
      ],
    },
    true
  );
}

async function init() {
  if (!chartRef.value) return;
  if (!chart) chart = echarts.init(chartRef.value);
  const ok = await ensureMap();
  if (ok) updateChart();
}

function onResize() {
  chart?.resize();
}

// 数据是异步来的、地图底图也是异步来的，谁先到都得能画上：
// 已注册 → 直接重画；还没注册（init 的 await 没回来）→ 走一次 init。
watch(geoStates, () => {
  if (registered) updateChart();
  else void init();
}, { deep: true });

onMounted(() => {
  nextTick(() => {
    setTimeout(init, 60);
  });
  window.addEventListener("resize", onResize);
});

onUnmounted(() => {
  window.removeEventListener("resize", onResize);
  chart?.dispose();
  chart = null;
});
</script>

<style lang="scss" scoped>
.panel-box {
  /**
   * flex 权重要比同列其它面板大：美国地图是「宽扁」比例（≈1.6:1），
   * 面板高度不够时 ECharts 会按高度缩放，图会缩得很小（实测 230px 高时
   * 地图只剩 300px 宽，两侧全是空白）。给 2 的权重让地图拿到 ~330px 高。
   *
   * ⚠ 模板里**不要**再加 `flex-1` 工具类：UnoCSS 的 `.flex-1{flex:1 1 0%}` 会把这个权重盖掉。
   */
  flex: 2 1 0%;
  background: rgba(10, 24, 52, 0.75);
  border: 1px solid rgba(46, 96, 153, 0.45);
  border-radius: 6px;
  padding: 8px 10px;
  backdrop-filter: blur(8px);
}

.panel-title-bar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding-bottom: 6px;
  border-bottom: 1px solid rgba(46, 96, 153, 0.3);
  margin-bottom: 4px;
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
  font-size: 11px;
  color: #8fb2dc;
}

.map-row {
  display: flex;
  gap: 8px;
  align-items: stretch;
}

.map-box {
  flex: 1;
  min-width: 0;
}

.state-list {
  width: 200px;
  flex-shrink: 0;
  display: flex;
  flex-direction: column;
  gap: 2px;
  padding-left: 6px;
  border-left: 1px dashed rgba(46, 96, 153, 0.35);
}

.state-list__head {
  display: flex;
  justify-content: space-between;
  font-size: 10px;
  color: #6b8cb5;
  padding-bottom: 2px;
}

.state-row {
  display: flex;
  align-items: center;
  gap: 5px;
  font-size: 11px;
  line-height: 16px;
}

.state-row--empty {
  color: #6b8cb5;
  font-size: 10px;
}

.state-row__idx {
  width: 14px;
  height: 14px;
  line-height: 14px;
  text-align: center;
  border-radius: 50%;
  font-size: 9px;
  background: rgba(46, 96, 153, 0.5);
  color: #c8e4ff;
  flex-shrink: 0;

  &.is-top {
    background: #00eaff;
    color: #04121f;
    font-weight: 700;
  }
}

.state-row__name {
  width: 78px;
  flex-shrink: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  color: #c8e4ff;
}

.state-row__bar {
  flex: 1;
  height: 5px;
  background: rgba(46, 96, 153, 0.35);
  border-radius: 3px;
  overflow: hidden;

  i {
    display: block;
    height: 100%;
    border-radius: 3px;
  }
}

.state-row__num {
  width: 44px;
  text-align: right;
  color: #fff;
  font-weight: 600;
}
</style>
