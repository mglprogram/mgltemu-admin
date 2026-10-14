<template>
  <div class="dataScreen-header">
    <div flex-center h-full overflow-hidden px-4 relative z-10>
      <!-- 左侧控制区 -->
      <div class="leftHead" items-center gap-3>
        <!-- 返回后台按钮 -->
        <button
          type="button"
          class="nav-btn"
          title="返回运营管理控制台"
          @click="goDashboard"
        >
          <span class="icon i-svg:homepage" />
          <span>返回后台</span>
        </button>

        <!-- 店铺切换按钮组 -->
        <div class="store-tabs">
          <button
            type="button"
            class="tab-btn"
            :class="{ active: currentStore === 'ALL' }"
            @click="currentStore = 'ALL'"
          >
            全店汇总
          </button>
          <button
            type="button"
            class="tab-btn"
            :class="{ active: currentStore === 'BEL' }"
            @click="currentStore = 'BEL'"
          >
            BEL 店
          </button>
          <button
            type="button"
            class="tab-btn"
            :class="{ active: currentStore === 'BHY' }"
            @click="currentStore = 'BHY'"
          >
            BHY 店
          </button>
        </div>

        <!-- 数据刷新按钮 -->
        <button
          type="button"
          class="refresh-btn"
          :class="{ 'is-loading': loading }"
          title="重新拉取最新运营数据"
          @click="loadAllData"
        >
          <span class="i-svg:refresh text-14px" />
          <span text-12px>同步 ({{ lastUpdated }})</span>
        </button>
      </div>

      <!-- 中间主标题 -->
      <div class="title-wrap">
        <h1 class="title">TEMU 跨境电商运营监控中心</h1>
        <div class="subtitle">TEMU E-COMMERCE OPERATION & RISK INTELLIGENCE DASHBOARD</div>
      </div>

      <!-- 右侧时间与系统状态 -->
      <div class="rightHead" items-center justify-end gap-4>
        <!-- 状态指示 -->
        <div class="system-status">
          <span class="status-dot animate-pulse" />
          <span class="status-text">自动化服务在线</span>
        </div>

        <!-- 全屏按钮 -->
        <button
          type="button"
          class="nav-btn"
          :title="isFullscreen ? '退出全屏' : '全屏展示'"
          @click="toggleFullscreen"
        >
          <span :class="isFullscreen ? 'i-svg:fullscreen-exit' : 'i-svg:fullscreen'" />
        </button>

        <!-- 美国时间：按出单州归并的 4 个时区（当前标签页不换行，下方是北京时间对照） -->
        <div class="clock-stack">
          <div class="us-clocks">
            <span
              v-for="z in clocks"
              :key="z.key"
              class="us-clock"
              :class="{ 'is-active': z.active }"
              :title="z.hint"
            >
              <i class="us-clock__dot" />
              <span class="us-clock__label">{{ z.label }}</span>
              <span class="us-clock__time font-din">{{ z.time }}</span>
            </span>
          </div>
          <!-- 北京时间（对照用，你本人在国内） -->
          <div class="bj-clock">北京 {{ beijing }}</div>
        </div>
      </div>
    </div>

    <!-- 顶部科技装饰线 -->
    <div class="lines" left="[10%]" />
    <div class="lines" right="[10%]" rotate="180" />
    <div class="ray" />
  </div>
</template>

<script lang="ts" setup>
import { ref } from "vue";
import { useRouter } from "vue-router";
import { useKeepTicking } from "../composables/useKeepTicking";
import { useDataScreenData } from "../composables/useDataScreenData";
import { useUsClocks } from "../composables/useUsClocks";

defineOptions({ name: "Cephalosome" });

const router = useRouter();
const { currentStore, loading, lastUpdated, loadAllData } = useDataScreenData();

/**
 * 右上角时间（2026-10-14 用户要求：改为美国时间，按出单州分时区）
 *
 * 4 个时区来自真实订单分布（Top10 出单州全部落在这 4 个里）：
 *   美东 FL NY PA GA NC NJ / 美中 TX IL / 美西 CA / 波多黎各 PR
 * 详见 composables/useUsClocks.ts 顶部说明（含波多黎各不看夏令时的坑）。
 */
const { clocks, beijing, tick } = useUsClocks();
// useKeepTicking 只起定时器、不会立刻回调，所以先手动跑一次，避免首秒显示 "--"
tick();
useKeepTicking(tick, 1000);

// 全屏状态
const isFullscreen = ref(false);
function toggleFullscreen() {
  if (!document.fullscreenElement) {
    document.documentElement.requestFullscreen().then(() => {
      isFullscreen.value = true;
    }).catch(() => {});
  } else {
    document.exitFullscreen().then(() => {
      isFullscreen.value = false;
    }).catch(() => {});
  }
}

function goDashboard() {
  // 大屏现在是首页（/dashboard），「返回后台」去白底运营控制台 /console
  router.push("/console");
}
</script>

<style lang="scss" scoped>
.title-wrap {
  width: 42%; /* 原 46%：右侧要放 4 个时区时钟，收窄一点给它让位 */
  text-align: center;
  position: relative;
  top: -4px;
}

.title {
  margin: 0;
  font-family: YouSheBiaoTiHei, "PingFang SC", sans-serif;
  font-size: 34px;
  color: #00eaff;
  letter-spacing: 6px;
  text-shadow: 0 0 20px rgba(0, 234, 255, 0.6), 0 0 40px rgba(0, 140, 255, 0.4);
}

.subtitle {
  font-family: DIN, sans-serif;
  font-size: 10px;
  color: rgba(140, 200, 255, 0.65);
  letter-spacing: 2px;
  margin-top: -2px;
}

.nav-btn {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  padding: 4px 10px;
  background: rgba(16, 44, 92, 0.6);
  border: 1px solid rgba(0, 234, 255, 0.35);
  border-radius: 4px;
  color: #c8e4ff;
  font-size: 12px;
  cursor: pointer;
  transition: all 0.2s;

  &:hover {
    background: rgba(0, 234, 255, 0.2);
    border-color: #00eaff;
    color: #ffffff;
    box-shadow: 0 0 10px rgba(0, 234, 255, 0.4);
  }
}

.store-tabs {
  display: inline-flex;
  background: rgba(10, 28, 64, 0.7);
  border: 1px solid rgba(46, 96, 153, 0.6);
  border-radius: 4px;
  padding: 2px;
  gap: 2px;
}

.tab-btn {
  padding: 3px 12px;
  background: transparent;
  border: none;
  border-radius: 3px;
  color: #8fb2dc;
  font-size: 12px;
  cursor: pointer;
  transition: all 0.2s;

  &:hover {
    color: #ffffff;
  }

  &.active {
    background: linear-gradient(180deg, #1875d1, #0a4d94);
    color: #ffffff;
    font-weight: 600;
    box-shadow: 0 0 8px rgba(0, 180, 255, 0.5);
  }
}

.refresh-btn {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  padding: 4px 8px;
  background: rgba(8, 30, 68, 0.6);
  border: 1px solid rgba(70, 130, 200, 0.3);
  border-radius: 4px;
  color: #88b0dd;
  cursor: pointer;
  transition: all 0.2s;

  &:hover {
    color: #00eaff;
    border-color: #00eaff;
  }

  &.is-loading .i-svg\:refresh {
    animation: spin 1s linear infinite;
  }
}

@keyframes spin {
  from { transform: rotate(0deg); }
  to { transform: rotate(360deg); }
}

.system-status {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 3px 8px;
  background: rgba(10, 40, 25, 0.45);
  border: 1px solid rgba(80, 220, 120, 0.3);
  border-radius: 12px;
  font-size: 11px;
  color: #67c23a;
}

.status-dot {
  width: 7px;
  height: 7px;
  border-radius: 50%;
  background-color: #67c23a;
  box-shadow: 0 0 8px #67c23a;
}

/* 右上角时间区：上行美国四时区（主），下行北京时间（对照）
   头部只有 60px 高，.rightHead 可用宽度约 518px，所以必须两行排，单行放不下 */
.clock-stack {
  display: flex;
  flex-direction: column;
  align-items: flex-end;
  justify-content: center;
  gap: 2px;
  padding-right: 10px;
  line-height: 1.15;
}

.us-clocks {
  display: flex;
  align-items: center;
  gap: 8px;
}

.us-clock {
  display: inline-flex;
  align-items: baseline;
  gap: 3px;
  font-size: 12px;
  color: #6b8cb5; /* 休息时段：暗 */
  transition: color 0.3s;

  /* 活跃时段（当地 8:00–22:00）：亮青色 + 圆点点亮 */
  &.is-active {
    color: #00eaff;

    .us-clock__dot {
      background-color: #00eaff;
      box-shadow: 0 0 6px #00eaff;
    }

    .us-clock__time {
      text-shadow: 0 0 8px rgba(0, 234, 255, 0.45);
    }
  }
}

.us-clock__dot {
  align-self: center;
  width: 5px;
  height: 5px;
  border-radius: 50%;
  background-color: #4a5a70;
  transition: background-color 0.3s;
}

.us-clock__label {
  font-size: 10px;
  opacity: 0.85;
}

.us-clock__time {
  font-family: YouSheBiaoTiHei, DIN, Arial, sans-serif;
  font-size: 13px;
  letter-spacing: 0.5px;
}

/* 北京时间：比美国那行更小更淡，只作对照，不抢视觉
   （用独立类名，不复用全局的 .rightHead .dateTime —— 那个是 20px 青色大字，
     复用会被它的样式盖住） */
.clock-stack .bj-clock {
  font-size: 10px;
  color: #4f6b8c;
  letter-spacing: 0.3px;
}
</style>
