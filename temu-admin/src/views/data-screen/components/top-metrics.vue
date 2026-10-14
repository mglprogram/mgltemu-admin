<template>
  <div class="metrics-grid">
    <!-- 1. 在售款式 -->
    <div
      class="metric-card cyan"
      role="button"
      tabindex="0"
      title="点击跳转至在售款明细"
      @click="jump('/temu/on-sale')"
    >
      <div class="card-glow" />
      <div class="card-icon">
        <span class="i-svg:table text-26px text-[#00eaff]" />
      </div>
      <div class="card-content">
        <div class="card-title">在售款数 (SKC)</div>
        <div class="card-value font-din">
          {{ metrics.onSaleSkc }}
          <span class="unit">款</span>
        </div>
        <div class="card-sub">
          SPU: {{ metrics.onSaleSpu }} · SKU: {{ metrics.onSaleSku }}
        </div>
      </div>
      <div class="card-tag">在售中</div>
    </div>

    <!-- 2. 库存总量 -->
    <div
      class="metric-card blue"
      role="button"
      tabindex="0"
      title="点击跳转至在售款明细"
      @click="jump('/temu/on-sale')"
    >
      <div class="card-glow" />
      <div class="card-icon">
        <span class="i-svg:file text-26px text-[#409eff]" />
      </div>
      <div class="card-content">
        <div class="card-title">在售库存总量</div>
        <div class="card-value font-din">
          {{ formatNumber(metrics.totalStock) }}
          <span class="unit">件</span>
        </div>
        <div class="card-sub">
          双店仓内现货充分
        </div>
      </div>
      <div class="card-tag">仓储</div>
    </div>

    <!-- 3. 限流商品告警 -->
    <div
      class="metric-card red"
      role="button"
      tabindex="0"
      title="点击跳转至限流监控"
      @click="jump('/temu/limited')"
    >
      <div class="card-glow" />
      <div class="card-icon">
        <span class="i-svg:shield text-26px text-[#f56c6c]" />
      </div>
      <div class="card-content">
        <div class="card-title">限流监控商品</div>
        <div class="card-value font-din is-danger">
          {{ metrics.limitedCount }}
          <span class="unit">款</span>
        </div>
        <div class="card-sub">
          重点款命中:
          <b :class="metrics.focusHitCount ? 'text-[#ff4d4f]' : 'text-[#67c23a]'">
            {{ metrics.focusHitCount }}
          </b> / {{ metrics.focusTotal }}
        </div>
      </div>
      <div class="card-tag danger">需防范</div>
    </div>

    <!-- 4. 低库存与加库存提醒 -->
    <div
      class="metric-card orange"
      role="button"
      tabindex="0"
      title="点击查看低库存与补货判定"
      @click="jump('/temu/on-sale?view=low')"
    >
      <div class="card-glow" />
      <div class="card-icon">
        <span class="i-svg:monitor text-26px text-[#e6a23c]" />
      </div>
      <div class="card-content">
        <div class="card-title">款·库存预警</div>
        <div class="card-value font-din text-[#e6a23c]">
          {{ metrics.lowStockStyleCount }}
          <span class="unit">款</span>
          <span class="unit-sub">/ {{ metrics.lowStockCount }} 个尺码</span>
        </div>
        <div class="card-sub">
          可加库存: <span class="text-[#67c23a]">{{ metrics.canRestockCount }}</span> ·
          已断货: <span class="text-[#f56c6c]">{{ metrics.outOfStockCount }}</span> 个尺码
        </div>
      </div>
      <div class="card-tag warn">库存预警</div>
    </div>

    <!-- 5. 补货与预售待办（口径同「在售款明细 · 今日待办」，点击落到该视图） -->
    <div
      class="metric-card green"
      role="button"
      tabindex="0"
      title="点击查看在售款明细 · 今日待办（可开预售 / 可加库存清单）"
      @click="jump('/temu/on-sale?view=todo')"
    >
      <div class="card-glow" />
      <div class="card-icon">
        <span class="i-svg:flexible text-26px text-[#67c23a]" />
      </div>
      <div class="card-content">
        <div class="card-title">补货与预售待办</div>
        <div class="card-value font-din text-[#67c23a]">
          {{ metrics.presaleReady }}
          <span class="unit">款可开预售</span>
        </div>
        <div class="card-sub">
          可加库存: <span class="text-[#00eaff] font-bold">{{ metrics.canRestockCount }}</span> ·
          需补货: <span class="text-[#f56c6c]">{{ metrics.needRestockCount }}</span>
        </div>
      </div>
      <div class="card-tag success">到货联动</div>
    </div>

    <!-- 6. 流量加速器 -->
    <div
      class="metric-card purple"
      role="button"
      tabindex="0"
      title="点击跳转至流量加速"
      @click="jump('/temu/flux-boost')"
    >
      <div class="card-glow" />
      <div class="card-icon">
        <span class="i-svg:hot text-26px text-[#c084fc]" />
      </div>
      <div class="card-content">
        <div class="card-title">流量加速商品</div>
        <div class="card-value font-din text-[#c084fc]">
          {{ metrics.fluxActiveCount }}
          <span class="unit">款进行中</span>
        </div>
        <div class="card-sub">
          待开启商机: {{ metrics.fluxPendingCount }} 款
        </div>
      </div>
      <div class="card-tag purple">流量提速</div>
    </div>
  </div>
</template>

<script lang="ts" setup>
import { useRouter } from "vue-router";
import { useDataScreenData } from "../composables/useDataScreenData";

defineOptions({ name: "TopMetrics" });

const router = useRouter();
const { metrics } = useDataScreenData();

function formatNumber(num: number): string {
  return (num || 0).toLocaleString();
}

function jump(path: string) {
  router.push(path);
}
</script>

<style lang="scss" scoped>
.metrics-grid {
  display: grid;
  grid-template-columns: repeat(6, 1fr);
  gap: 12px;
  width: 100%;
}

.metric-card {
  position: relative;
  background: rgba(10, 24, 52, 0.75);
  border: 1px solid rgba(46, 96, 153, 0.45);
  border-radius: 6px;
  padding: 10px 12px;
  display: flex;
  align-items: center;
  gap: 10px;
  cursor: pointer;
  overflow: hidden;
  transition: all 0.25s ease;
  backdrop-filter: blur(8px);

  &:hover {
    transform: translateY(-2px);
    box-shadow: 0 4px 20px rgba(0, 180, 255, 0.25);
  }

  &.cyan {
    border-top: 2px solid #00eaff;
    &:hover { border-color: #00eaff; }
  }

  &.blue {
    border-top: 2px solid #409eff;
    &:hover { border-color: #409eff; }
  }

  &.red {
    border-top: 2px solid #f56c6c;
    &:hover { border-color: #f56c6c; }
  }

  &.orange {
    border-top: 2px solid #e6a23c;
    &:hover { border-color: #e6a23c; }
  }

  &.green {
    border-top: 2px solid #67c23a;
    &:hover { border-color: #67c23a; }
  }

  &.purple {
    border-top: 2px solid #c084fc;
    &:hover { border-color: #c084fc; }
  }
}

.card-icon {
  flex-shrink: 0;
  width: 42px;
  height: 42px;
  border-radius: 6px;
  background: rgba(14, 38, 78, 0.85);
  border: 1px solid rgba(50, 120, 200, 0.35);
  display: flex;
  align-items: center;
  justify-content: center;
}

.card-content {
  flex: 1;
  min-width: 0;
}

.card-title {
  font-size: 11px;
  color: #8fb2dc;
  line-height: 1.2;
  white-space: nowrap;
}

.card-value {
  font-size: 22px;
  font-weight: 700;
  color: #00eaff;
  line-height: 1.3;
  margin: 1px 0;

  .unit {
    font-size: 11px;
    font-weight: normal;
    color: #8fb2dc;
    margin-left: 2px;
  }

  // 副单位（如「/ 183 个尺码」）：比 unit 更小更淡，避免和主数字抢视觉
  .unit-sub {
    font-size: 10px;
    font-weight: normal;
    color: #6b8cb5;
    margin-left: 3px;
  }

  &.is-danger {
    color: #ff5252;
  }
}

.card-sub {
  font-size: 10px;
  color: #6b8cb5;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.card-tag {
  position: absolute;
  top: 6px;
  right: 8px;
  font-size: 9px;
  padding: 1px 5px;
  border-radius: 3px;
  background: rgba(0, 234, 255, 0.12);
  color: #00eaff;
  border: 1px solid rgba(0, 234, 255, 0.25);

  &.danger {
    background: rgba(245, 108, 108, 0.15);
    color: #f56c6c;
    border-color: rgba(245, 108, 108, 0.3);
  }

  &.warn {
    background: rgba(230, 162, 60, 0.15);
    color: #e6a23c;
    border-color: rgba(230, 162, 60, 0.3);
  }

  &.success {
    background: rgba(103, 194, 58, 0.15);
    color: #67c23a;
    border-color: rgba(103, 194, 58, 0.3);
  }

  &.purple {
    background: rgba(192, 132, 252, 0.15);
    color: #c084fc;
    border-color: rgba(192, 132, 252, 0.3);
  }
}
</style>
