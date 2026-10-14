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

        <!-- 实时时间 -->
        <div class="dateTime">{{ dateTime }}</div>
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
import { parseTime } from "../utils/time";
import { useDataScreenData } from "../composables/useDataScreenData";

defineOptions({ name: "Cephalosome" });

const router = useRouter();
const { currentStore, loading, lastUpdated, loadAllData } = useDataScreenData();

// 实时时间
const dateTime = ref("加载中...");
const getCurrentTime = () => {
  dateTime.value = parseTime(new Date(), "YYYY-MM-DD HH:mm:ss 周dd");
};
useKeepTicking(getCurrentTime, 1000);

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
  width: 46%;
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
</style>
