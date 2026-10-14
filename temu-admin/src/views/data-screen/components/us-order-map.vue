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
      <!--
        标题栏是**绝对定位浮层**，右侧 168px 留给「主要出单州」榜单，
        所以总数（31,651 单 / 52 州）不能放右边 —— 会压到榜单第一行，
        改贴在标题后面。
      -->
      <div class="title-left">
        <span class="indicator-bar" />
        <span class="title-text">订单分布 · 美国</span>
        <span class="title-sub">
          {{ geoReady ? `${num(geoTotal)} 单 / ${geoStates.length} 州` : "暂无数据" }}
        </span>
      </div>
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

/**
 * 美国地图（含阿拉斯加 / 夏威夷内嵌）在 ECharts 默认投影下的**长宽比**。
 *
 * 实测（2026-10-14，本机 1920×1080）：`layoutSize` 给 483px（正方形外框边长）时，
 * canvas 里非透明像素的包围盒是 **484×283** → A = 484/283 ≈ **1.71**。
 * （推算值也是 1.7 左右：usa.json 经度跨度 112.2°、纬度跨度 52.5°，
 *   ECharts aspectScale 默认 0.75 → 112.2/52.5×0.75 ≈ 1.60，实测取 1.71 更准。）
 */
const MAP_ASPECT = 1.71;

/**
 * 让地图**撑满**面板的地图布局（而不是缩在正中一小块）。
 *
 * ⚠ 为什么不能直接写 `layoutSize: "140%"`（2026-10-14 第一版踩的坑）：
 *   `layoutSize` 的百分比是相对 **min(容器宽, 容器高)** 算的，而且它定义的是一块
 *   **正方形**外框，地图按 contain 塞进方框。数据大屏里这块面板是「宽扁」的
 *   （实测 594×345），拿 min=345 当方框 → 地图只有 483px 宽，**左右各空 ~55px**，
 *   用户反馈「可视化太小」。
 *
 * 正解：按像素给方框边长。地图渲染出来是 `宽=S、高=S/A`，所以
 *   `S = min(容器宽, A × 容器高)`
 * 时地图恰好贴住其中一边：
 *   · 容器偏宽（w ≥ A·h）→ 高度顶满，宽 = A·h；
 *   · 容器偏高（w < A·h）→ 宽度顶满，高 = w/A。
 * ⚠ 由此可知：**面板高度才是地图尺寸的唯一瓶颈**（宽扁容器下 地图高=容器高）。
 *   所以同列里给了 map 面板最大的 flex 权重，并把右侧榜单压窄，
 *   避免出现「宽度不够 → 反过来把地图压小」。
 */
function mapLayout(): { layoutCenter?: [string, string]; layoutSize?: number } {
  const w = chart?.getWidth() || 0;
  const h = chart?.getHeight() || 0;
  if (w <= 0 || h <= 0) return {};
  const size = Math.min(w, MAP_ASPECT * h);
  return { layoutCenter: ["50%", "50%"], layoutSize: Math.floor(size) };
}

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
          ...mapLayout(),
          label: { show: false },
          itemStyle: { borderColor: "rgba(0, 234, 255, 0.35)", borderWidth: 0.6, areaColor: "#10243f" },
          emphasis: {
            label: { show: true, fontSize: 9, color: "#fff" },
            itemStyle: { areaColor: "#ffd666" },
          },
          /**
           * 只给订单数据里出现过的州上色，**没数据的州保持 areaColor 深色**。
           * 地图 series 的 data 直接换成 state 列表即可（名字与底图 feature 一一对应）。
           */
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
  // layoutSize 是按容器尺寸算出来的像素值，resize 后必须重算（否则地图还用着旧尺寸）
  updateChart();
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
   * flex 权重（实测 1920×1080，中栏 824×912：上=双店大盘 286 固定，剩下 602 在
   * 「销量 Top10」和本面板之间按 1 : flex 分）：
   *
   *   flex=2.5  → 本面板 419px，但 Top10 只剩 183px（截图里第 2 行被切断）
   *   flex=1.85 → 本面板 389px，地图仍拿满 626×366，Top10 恢复到 213px（约 3 行）
   *
   * ⚠ 为什么 1.85 就够：地图是**宽度受限**的 —— 中栏 824 减去右侧榜单 168 + 内边距后
   *   map-box 只有 626px 宽，地图最多 626×366（1.71 比例）。**再给高度也不会变大**，
   *   只会白占「销量 Top10」的空间。
   *
   * ⚠ 模板里**不要**再加 `flex-1` 工具类：UnoCSS 的 `.flex-1{flex:1 1 0%}` 会把这个权重盖掉。
   */
  position: relative;
  flex: 1.85 1 0%;
  background: rgba(10, 24, 52, 0.75);
  border: 1px solid rgba(46, 96, 153, 0.45);
  border-radius: 6px;
  padding: 4px 10px 6px;
  backdrop-filter: blur(8px);
}

/**
 * 标题栏**绝对定位浮在地图上**，不占布局高度：省下的 ~29px 全部给地图。
 * 这是「既要地图最大、又不想把销量 Top10 挤没」的关键一步 ——
 * 地图是宽度受限的，把省下的高度给地图没用，但**给 Top10 有用**，
 * 所以这里的做法是：标题叠上去（省高度）→ 面板 flex 调小（还高度给 Top10）。
 * 叠放在地图上方的空白区（左上角是阿拉斯加、上方是海），加投影保证可读。
 */
.panel-title-bar {
  position: absolute;
  top: 5px;
  left: 12px;
  right: 12px;
  z-index: 3;
  display: flex;
  align-items: center;
  justify-content: space-between;
  pointer-events: none; /* 别挡住地图的 hover tooltip */
  text-shadow: 0 1px 3px rgba(0, 0, 0, 0.95);
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

.title-sub {
  font-size: 11px;
  color: #8fb2dc;
  padding-left: 4px;
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

/**
 * 右侧榜单压到 150px：它是**宽度**上的竞争者 —— 一旦把 map-box 挤到
 * `宽 < 1.71×高`，地图就会反过来被宽度压小（宽度不够时地图高 = 宽/1.71）。
 * 7 行的信息量靠缩小字号 + 缩短条形保留，数字列不动。
 */
.state-list {
  width: 168px;
  flex-shrink: 0;
  display: flex;
  flex-direction: column;
  gap: 1px;
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
  width: 66px;
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
  width: 42px;
  text-align: right;
  color: #fff;
  font-weight: 600;
}
</style>
