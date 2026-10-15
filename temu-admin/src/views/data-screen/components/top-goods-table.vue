<template>
  <div class="panel-box flex-1 flex flex-col min-h-0">
    <div class="panel-title-bar">
      <div class="title-left">
        <span class="indicator-bar" />
        <span class="title-text">在售商品销量排行 Top 10</span>
        <span class="title-hint" :title="salesHint">今日销量</span>
      </div>
      <button
        type="button"
        class="more-link"
        @click.stop="router.push('/temu/on-sale')"
      >
        查看在售明细 →
      </button>
    </div>

    <!-- 表格滚动区域 -->
    <div class="table-wrap flex-1 overflow-y-auto">
      <!-- 空态：没有可排序的销量时不要显示一张整列 "--" 的假榜单 -->
      <div v-if="!topSalesGoods.length" class="empty-tip">
        暂无销量数据（需先跑一次「销量分析」抓取刷新今日/近7日销量）
      </div>
      <table v-else class="temu-rank-table">
        <thead>
          <tr>
            <th width="40">排名</th>
            <th width="50">主图</th>
            <th>货号 / 商品标题</th>
            <th width="85">类目</th>
            <th width="78" class="text-right">今日销量</th>
            <th width="75" class="text-right">7日销量</th>
            <th width="75" class="text-right">总销量</th>
            <th width="70" class="text-right">在售库存</th>
            <th width="80" class="text-right">申报单价</th>
          </tr>
        </thead>
        <tbody>
          <tr
            v-for="(item, index) in topSalesGoods"
            :key="item.spuId + item.code"
            class="table-row"
            role="button"
            @click="router.push('/temu/on-sale')"
          >
            <!-- 排名 -->
            <td>
              <span class="rank-badge" :class="getRankClass(index)">
                {{ index + 1 }}
              </span>
            </td>

            <!-- 主图 -->
            <td>
              <div class="img-box">
                <img
                  v-if="item.mainImage"
                  :src="item.mainImage"
                  :alt="item.code"
                  loading="lazy"
                  class="goods-thumb"
                />
                <span v-else class="thumb-placeholder font-din">无图</span>
              </div>
            </td>

            <!-- 货号与标题（全店汇总时是「同款合并」行） -->
            <td class="goods-info-cell">
              <div class="code-line">
                <span class="store-tag">{{ item.store }}</span>
                <span class="goods-code font-din">{{ item.code }}</span>
                <span
                  v-if="(item.mergedCodes?.length || 0) > 1"
                  class="merge-badge"
                  :title="mergeTitle(item)"
                >
                  合并 {{ item.mergedCodes!.length }} 个变体/店
                </span>
              </div>
              <div class="goods-title" :title="item.title">
                {{ item.title }}
              </div>
              <div v-if="(item.mergedCodes?.length || 0) > 1" class="merge-line">
                {{ item.mergedCodes!.join(' + ') }}
              </div>
              <div v-if="item.mergeNotes?.length" class="merge-note">
                {{ item.mergeNotes.join('、') }}
              </div>
            </td>

            <!-- 类目 -->
            <td>
              <span class="cat-pill" :title="item.category">{{ item.category || '未分类' }}</span>
            </td>

            <!-- 今日销量（榜单排序依据） -->
            <td class="text-right font-din text-[#ffd04b] font-bold">
              {{ formatSales(item.todaySales) }}
            </td>

            <!-- 7日销量 -->
            <td class="text-right font-din text-[#00eaff] font-bold">
              {{ formatSales(item.sales7) }}
            </td>

            <!-- 总销量 -->
            <td class="text-right font-din text-[#67c23a]">
              {{ item.totalSales ? item.totalSales.toLocaleString() : '--' }}
            </td>

            <!-- 可售库存 -->
            <td class="text-right font-din" :class="item.stock < 20 ? 'text-[#ff5252]' : 'text-[#c8e4ff]'">
              {{ item.stock?.toLocaleString() || 0 }}
            </td>

            <!-- 价格 -->
            <td class="text-right font-din text-[#e6a23c]">
              ${{ item.priceMin ?? '--' }}
            </td>
          </tr>
        </tbody>
      </table>
    </div>
  </div>
</template>

<script lang="ts" setup>
import { computed } from "vue";
import { useRouter } from "vue-router";
import { useDataScreenData } from "../composables/useDataScreenData";

defineOptions({ name: "TopGoodsTable" });

const router = useRouter();
const { topSalesGoods, goodsData } = useDataScreenData();

/** 合并行的悬浮说明：默认列出成员货号；命中人工特例时把规则原话也带上（倍率可核对） */
function mergeTitle(item: { mergedCodes?: string[]; mergeNotes?: string[]; ruleNote?: string }) {
  const lines = [`同款合并：${(item.mergedCodes || []).join(" + ")}`];
  if (item.ruleNote) lines.push(`规则：${item.ruleNote}`);
  if (item.mergeNotes?.length) lines.push(...item.mergeNotes);
  return lines.join("\n");
}

/**
 * 销量数据来源提示（悬浮可见）。
 *
 * 为什么值得显示：商品列表接口与官方导出**都不返回销量**，
 * 这一列的数字来自「销量分析」页，是另一条抓取链路 —— 它可能比商品数据旧，
 * 让人一眼能看出"这份销量是什么时候的"。
 */
const salesHint = computed(() => {
  const g = goodsData.value;
  const src = g?.salesSource;
  const today = g?.todaySalesSource;
  const tParts = Object.entries(today || {})
    .filter(([, v]) => v)
    .map(([k, v]) => `${k} ${v}`);
  const parts = Object.entries(src || {})
    .filter(([, v]) => v)
    .map(([k, v]) => `${k} ${v}`);
  return (
    `榜单按【今日销量】降序（今日都为 0 时回落到近7日）。` +
    `同一产品的不同变体会相加成一行（如 204203-4SetA + 4SetC）：` +
    `全店汇总再额外把两店同款相加，BHY / BEL 页签只在各自店内合并。` +
    `两个人工作死的特例（跨店合并）：202504-10 全系列累加到 BHY-202504-10SetA；` +
    `H103903-4B(×2) / 6B(×3) 累加到 H103903-2B。` +
    `今日销量来源（TEMU 销量分析页·今日口径，取页面「今日SKC销量」合并格）：` +
    `${tParts.join(" · ") || "无"}；近7日销量来源：${parts.join(" · ") || "无"}`
  );
});

/** 销量显示：0 是有效值（确实没销量），不能像 null 那样显示成 "--" */
function formatSales(v: number | null | undefined): string {
  if (v === null || v === undefined) return "--";
  return Number(v).toLocaleString();
}

function getRankClass(index: number) {
  if (index === 0) return "rank-gold";
  if (index === 1) return "rank-silver";
  if (index === 2) return "rank-bronze";
  return "rank-normal";
}
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

// 销量口径小标签：提醒这一列不是商品接口来的
.title-hint {
  font-size: 9px;
  color: #6b8cb5;
  border: 1px solid rgba(46, 96, 153, 0.5);
  border-radius: 3px;
  padding: 0 4px;
  cursor: help;
  white-space: nowrap;
}

.empty-tip {
  padding: 24px 8px;
  text-align: center;
  font-size: 11px;
  color: #6b8cb5;
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

.table-wrap {
  max-height: 270px;

  &::-webkit-scrollbar {
    width: 3px;
  }
  &::-webkit-scrollbar-thumb {
    background: rgba(0, 234, 255, 0.25);
    border-radius: 2px;
  }
}

.temu-rank-table {
  width: 100%;
  border-collapse: collapse;
  font-size: 11px;

  th {
    color: #8fb2dc;
    font-weight: 500;
    padding: 6px 4px;
    border-bottom: 1px solid rgba(46, 96, 153, 0.4);
    text-align: left;
    white-space: nowrap;
  }

  td {
    padding: 5px 4px;
    border-bottom: 1px solid rgba(46, 96, 153, 0.15);
    color: #ffffff;
    vertical-align: middle;
  }
}

.table-row {
  cursor: pointer;
  transition: background 0.15s;

  &:hover {
    background: rgba(0, 234, 255, 0.08);
  }
}

.rank-badge {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 20px;
  height: 20px;
  border-radius: 50%;
  font-size: 11px;
  font-weight: 700;
  font-family: DIN, sans-serif;

  &.rank-gold {
    background: linear-gradient(135deg, #ffd700, #ffaa00);
    color: #000;
    box-shadow: 0 0 8px rgba(255, 215, 0, 0.6);
  }

  &.rank-silver {
    background: linear-gradient(135deg, #e0e0e0, #a0a0a0);
    color: #000;
    box-shadow: 0 0 6px rgba(200, 200, 200, 0.5);
  }

  &.rank-bronze {
    background: linear-gradient(135deg, #cd7f32, #8b4513);
    color: #fff;
    box-shadow: 0 0 6px rgba(205, 127, 50, 0.5);
  }

  &.rank-normal {
    background: rgba(255, 255, 255, 0.1);
    color: #8fb2dc;
  }
}

.img-box {
  width: 32px;
  height: 32px;
  border-radius: 4px;
  background: rgba(14, 34, 72, 0.8);
  overflow: hidden;
  border: 1px solid rgba(46, 96, 153, 0.4);
  display: flex;
  align-items: center;
  justify-content: center;
}

.goods-thumb {
  width: 100%;
  height: 100%;
  object-fit: cover;
}

.thumb-placeholder {
  font-size: 8px;
  color: #6b8cb5;
}

.goods-info-cell {
  max-width: 180px;
}

.code-line {
  display: flex;
  align-items: center;
  gap: 4px;
  margin-bottom: 1px;
}

.store-tag {
  font-size: 8px;
  padding: 0 3px;
  border-radius: 2px;
  background: rgba(0, 234, 255, 0.15);
  color: #00eaff;
}

.goods-code {
  font-size: 11px;
  font-weight: 600;
  color: #c8e4ff;
}

.goods-title {
  font-size: 10px;
  color: #6b8cb5;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

/* 全店汇总：同一产品不同变体/不同店合并后的标记与成员列表 */
.merge-badge {
  margin-left: 4px;
  padding: 0 4px;
  border-radius: 2px;
  font-size: 8px;
  background: rgba(230, 162, 60, 0.18);
  color: #e6a23c;
  cursor: help;
}

.merge-line {
  font-size: 9px;
  color: #4d6a8f;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

/* 带倍率的特例（H103903-4B ×2 这类）：把换算写在行内，避免看起来像加错了 */
.merge-note {
  font-size: 9px;
  color: #8a6d3b;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.cat-pill {
  font-size: 9px;
  color: #8fb2dc;
  background: rgba(14, 34, 72, 0.6);
  padding: 1px 4px;
  border-radius: 3px;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  display: inline-block;
  max-width: 80px;
}
</style>
