<template>
  <div class="dataScreen-container">
    <!-- 适配屏幕容器：按 1920×1080 设计稿等比缩放 -->
    <div v-fit-screen pt-6px flex flex-col>
      <!-- 头部区域 (标题 + 店铺切换 + 控制按钮) -->
      <Cephalosome />

      <!-- 顶部全局核心业务 KPI 卡片行 (6大指标) -->
      <div px-12px pt-2px pb-8px>
        <TopMetrics />
      </div>

      <!-- 图表区域 (三列科技布局) -->
      <div flex-1 px-12px pb-10px flex gap="12px" relative min-h-0>
        <!-- 左侧：限流防控与安全预警 (28%) -->
        <div class="col-left">
          <LeftCharts />
        </div>

        <!-- 中间：双店大盘与在售热销 (44%) -->
        <div class="col-center">
          <CenterCharts />
        </div>

        <!-- 右侧：增长策略与自动化执行 (28%) -->
        <div class="col-right">
          <RightCharts />
        </div>
      </div>
    </div>
  </div>
</template>

<script lang="ts" setup>
import { onMounted, type Directive } from "vue";
import "./assets/styles/index.scss";

import Cephalosome from "./components/cephalosome.vue";
import TopMetrics from "./components/top-metrics.vue";
import LeftCharts from "./components/left-charts.vue";
import CenterCharts from "./components/center-charts.vue";
import RightCharts from "./components/right-charts.vue";

import { useDataScreenData } from "./composables/useDataScreenData";
import { useKeepTicking } from "./composables/useKeepTicking";

defineOptions({ name: "DataScreen" });

const { loadAllData } = useDataScreenData();

// 挂载时立即加载全部真实 TEMU 业务数据
onMounted(() => {
  loadAllData();
});

// 每 60 秒自动静默轮询最新数据
useKeepTicking(() => {
  loadAllData();
}, 60000);

/** 设计稿宽度 */
const DESIGN_WIDTH = 1920;

/** 设计稿高度 */
const DESIGN_HEIGHT = 1080;

/** 元素与 resize 回调的映射，卸载时按引用移除监听 */
const resizeHandlers = new WeakMap<HTMLElement, () => void>();

/**
 * 按视口与设计稿的较小比例缩放，并平移回视口中心
 */
function applyScale(el: HTMLElement): void {
  const scale = Math.min(window.innerWidth / DESIGN_WIDTH, window.innerHeight / DESIGN_HEIGHT);
  el.style.transform = `scale(${scale}) translate(-50%, -50%)`;
}

/**
 * 大屏适配指令：元素固定为设计稿尺寸后等比缩放，版式不随窗口比例变形
 */
const vFitScreen: Directive<HTMLElement> = {
  mounted(el) {
    Object.assign(el.style, {
      position: "fixed",
      top: "50%",
      left: "50%",
      zIndex: "999",
      width: `${DESIGN_WIDTH}px`,
      height: `${DESIGN_HEIGHT}px`,
      transformOrigin: "top left",
      transition: "all 0.3s",
    });

    const resize = () => applyScale(el);
    resizeHandlers.set(el, resize);
    applyScale(el);
    window.addEventListener("resize", resize);
  },
  unmounted(el) {
    const resize = resizeHandlers.get(el);
    if (resize) {
      window.removeEventListener("resize", resize);
      resizeHandlers.delete(el);
    }
  },
};
</script>

<style lang="scss" scoped>
.col-left {
  width: 28%;
  height: 100%;
}

.col-center {
  width: 44%;
  height: 100%;
}

.col-right {
  width: 28%;
  height: 100%;
}
</style>
