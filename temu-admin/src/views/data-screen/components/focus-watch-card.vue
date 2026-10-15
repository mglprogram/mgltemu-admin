<template>
  <div class="panel-box flex-1 flex flex-col min-h-0">
    <div class="panel-title-bar">
      <div class="title-left">
        <span class="indicator-bar" />
        <span class="title-text">重点关注 SPU 限流监控</span>
      </div>
      <div class="title-right">
        <span class="badge red">
          限流命中 {{ focusHits.length }}
        </span>
        <button
          type="button"
          class="more-link"
          @click.stop="router.push('/temu/limited')"
        >
          查看全量 →
        </button>
      </div>
    </div>

    <!-- 列表展示区 -->
    <div class="list-container flex-1 overflow-y-auto">
      <template v-if="focusHits.length > 0">
        <div
          v-for="item in focusHits"
          :key="item.spu_id"
          class="alert-item"
        >
          <div class="item-head">
            <span class="store-tag">{{ item.sku_code?.startsWith('BHY') ? 'BHY' : 'BEL' }}</span>
            <span class="code font-din">{{ item.sku_code }}</span>
            <span class="spu-id font-din">SPU: {{ item.spu_id }}</span>
            <span class="flow-pill">{{ item.flow_status || '流量受限' }}</span>
          </div>

          <div class="item-title" :title="item.title">
            {{ item.title }}
          </div>

          <div class="item-price-bar">
            <span class="price-label">当前申报:</span>
            <span class="price-val font-din text-[#c8e4ff]">${{ item.declare_price ?? '--' }}</span>
            <span class="price-label ml-2">建议日常:</span>
            <span class="price-val font-din text-[#00eaff]">${{ item.target_price ?? '--' }}</span>
            <span v-if="item.declare_price && item.target_price" class="gap-val">
              (需调 -${{ (item.declare_price - item.target_price).toFixed(2) }})
            </span>
          </div>
        </div>
      </template>

      <div v-else class="empty-state">
        <span class="i-svg:shield text-32px text-[#67c23a] mb-2" />
        <div class="text-[#67c23a] text-13px font-bold">所有重点关注 SPU 状态良好</div>
        <div class="text-[#6b8cb5] text-11px mt-1">当前未检出重点款被 TEMU 限流</div>
      </div>
    </div>
  </div>
</template>

<script lang="ts" setup>
import { useRouter } from "vue-router";
import { useDataScreenData } from "../composables/useDataScreenData";

defineOptions({ name: "FocusWatchCard" });

const router = useRouter();
const { focusHits } = useDataScreenData();
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

.badge {
  font-size: 10px;
  padding: 1px 6px;
  border-radius: 10px;

  &.red {
    background: rgba(245, 108, 108, 0.2);
    color: #f56c6c;
    border: 1px solid rgba(245, 108, 108, 0.35);
  }
}

.more-link {
  background: transparent;
  border: none;
  font-size: 11px;
  color: #8fb2dc;
  cursor: pointer;
  padding: 0;
  &:hover {
    color: #00eaff;
  }
}

.list-container {
  display: flex;
  flex-direction: column;
  gap: 8px;
  max-height: 280px;

  &::-webkit-scrollbar {
    width: 3px;
  }
  &::-webkit-scrollbar-thumb {
    background: rgba(0, 234, 255, 0.25);
    border-radius: 2px;
  }
}

.alert-item {
  background: rgba(14, 32, 68, 0.65);
  border: 1px solid rgba(245, 108, 108, 0.25);
  border-left: 3px solid #f56c6c;
  border-radius: 4px;
  padding: 6px 8px;
  transition: border-color 0.2s;

  &:hover {
    border-color: rgba(245, 108, 108, 0.6);
    background: rgba(20, 42, 85, 0.75);
  }
}

.item-head {
  display: flex;
  align-items: center;
  gap: 6px;
  margin-bottom: 3px;
}

.store-tag {
  font-size: 9px;
  padding: 0 4px;
  border-radius: 2px;
  background: rgba(0, 234, 255, 0.15);
  color: #00eaff;
}

.code {
  font-size: 11px;
  font-weight: 600;
  color: #ffffff;
}

.spu-id {
  font-size: 10px;
  color: #6b8cb5;
}

.flow-pill {
  margin-left: auto;
  font-size: 9px;
  padding: 1px 5px;
  border-radius: 10px;
  background: rgba(245, 108, 108, 0.2);
  color: #ff5252;
}

.item-title {
  font-size: 11px;
  color: #9ab4d6;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  margin-bottom: 4px;
}

.item-price-bar {
  display: flex;
  align-items: center;
  font-size: 10px;
  color: #6b8cb5;
}

.price-val {
  font-weight: 600;
  margin-left: 3px;
}

.gap-val {
  color: #ff5252;
  font-weight: 600;
  margin-left: 4px;
}

.empty-state {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  padding: 24px 0;
}
</style>
