<template>
  <div class="panel-box flex flex-col">
    <div class="panel-title-bar">
      <div class="title-left">
        <span class="indicator-bar green" />
        <span class="title-text">补货与预售待办</span>
        <span class="title-hint" :title="toneHint">口径同「在售款明细 · 今日待办」</span>
      </div>
      <button
        type="button"
        class="more-link"
        @click="jump('todo')"
      >
        今日待办 →
      </button>
    </div>

    <!--
      三个数字的口径（2026-10-14 修）：
      原先取 presale.json 的 verdict，但那条链路的输入**只有已售罄/即将售罄的 6 行**，
      而且「可开预售」在那 6 行里恒为 0 —— 与「在售款明细 · 今日待办」的
      80 / 2 / 24 完全对不上（用户 2026-10-14 指出）。
      现统一改用 goods.json 的 lowStock.restock.verdictCounts（今日待办同一份数据），
      点进去的数字也对得上。
    -->
    <div class="verdict-bar">
      <button
        type="button"
        class="verdict-item presale"
        title="点击查看可开预售清单"
        @click="jump('todo')"
      >
        <span class="v-num font-din">{{ restockCount("可开预售") }}</span>
        <span class="v-label">可开预售</span>
      </button>
      <button
        type="button"
        class="verdict-item add-stock"
        title="点击查看可加库存清单"
        @click="jump('todo')"
      >
        <span class="v-num font-din">{{ restockCount("可加库存") }}</span>
        <span class="v-label">可加库存</span>
      </button>
      <button
        type="button"
        class="verdict-item restock"
        title="点击查看需补货清单"
        @click="jump('low')"
      >
        <span class="v-num font-din">{{ restockCount("需补货") }}</span>
        <span class="v-label">海外需补货</span>
      </button>
    </div>

    <!-- 可补库存总量 + 到货清单入口 -->
    <div class="topup-bar">
      <span class="topup-label">海外仓可补</span>
      <span class="topup-num font-din">{{ restock?.suggestAddTotal ?? 0 }}</span>
      <span class="topup-unit">件</span>
      <span class="topup-sub">
        （{{ restockCount("可加库存") }} 个尺码 · 目标覆盖 {{ restock?.targetDays ?? 30 }} 天）
      </span>
    </div>

    <!--
      到货/售罄清单：**列出全部**已售罄 + 即将售罄（不再只放前 3 条），
      容器限高 + 滚动，往下拉可以看全（用户 2026-10-15 口径）。
    -->
    <div class="inbound-head">
      <span>已售罄 · 即将售罄清单</span>
      <span class="inbound-head__count">{{ trackList.length }} 个尺码 · 可下拉</span>
    </div>
    <div class="inbound-list overflow-y-auto">
      <div
        v-for="item in trackList"
        :key="item.store + item.skuCode"
        class="inbound-row"
        :class="{ 'is-soldout': item.status === '已售罄' }"
      >
        <div class="inbound-left">
          <div class="sku-head">
            <!--
              款号可点：进「销量-库存」页并带上该货号做搜索词
              （用户 2026-10-15：点这个款号直接进销量-库存页看它）
            -->
            <span
              class="sku-code font-din is-link"
              role="button"
              tabindex="0"
              title="点击到「销量-库存」页查看该货号"
              @click.stop="goPresale(item.skuCode)"
              @keydown.enter.stop="goPresale(item.skuCode)"
            >
              {{ item.skuCode }}
            </span>
            <span class="size-tag">{{ item.size || '均码' }}</span>
            <span
              v-if="item.status"
              class="status-tag"
              :class="item.status === '已售罄' ? 'is-danger' : 'is-warn'"
            >
              {{ item.status }}
            </span>
          </div>
          <div class="arrival-info">
            <span class="text-[#8fb2dc]">预计到货:</span>
            <span class="font-din text-[#00eaff]">{{ item.arrivalDate || '近期到货' }}</span>
            <span v-if="item.arrivalDays !== null" class="days-pill" :class="item.arrivalDays <= 3 ? 'urgent' : 'normal'">
              {{ item.arrivalDays > 0 ? `${item.arrivalDays}天后` : '今日/已到' }}
            </span>
          </div>
        </div>

        <div class="inbound-right text-right">
          <div class="qty font-din text-[#67c23a] font-bold">
            +{{ item.transit || item.arrivalTicketQty || 0 }} 件
          </div>
          <div class="daily-need font-din text-[#6b8cb5] text-9px">
            日均需求: {{ item.dailyNeed ?? 0 }}/天
          </div>
        </div>
      </div>
    </div>
  </div>
</template>

<script lang="ts" setup>
import { computed } from "vue";
import { useRouter } from "vue-router";
import { useDataScreenData } from "../composables/useDataScreenData";

defineOptions({ name: "PresaleInboundPanel" });

const router = useRouter();
const { presaleRows, restockSummary } = useDataScreenData();

/**
 * 今天的补货/预售待办汇总（与「在售款明细 · 今日待办」**同一份数据**）。
 *
 * 这是本面板三个数字的唯一来源 —— 不再用 presale.json 的 verdict，
 * 那条链路的输入只有已售罄/即将售罄的 6 行，口径与今日待办完全对不上。
 *
 * ⚠ 必须用 `restockSummary`（随店铺切换），不能直接读 `goodsData.lowStock.restock`
 *   —— 后者是两店合计，切到 BEL/BHY 时数字不会变。
 */
const restock = restockSummary;

/** 读待办结论的条数（取不到按 0，避免面板显示 undefined） */
function restockCount(verdict: string): number {
  return restock.value?.verdictCounts?.[verdict] ?? 0;
}

const toneHint = computed(() => {
  const r = restock.value;
  if (!r) return "数据未加载";
  return (
    `口径与「在售款明细 · 今日待办」一致：可加库存 = 加州仓有现货且算得出量；` +
    `可开预售 = 仓内无货但 7 天内有到货；海外需补货 = 仓里不够但还没到货。` +
    `建议加库存合计 ${r.suggestAddTotal ?? 0} 件`
  );
});

/**
 * 列表内容与顺序：**已售罄/即将售罄全部列出**（2026-10-15 用户要求）。
 *
 * 用户口径：
 *   · 全店汇总时，把「已售罄」和「即将售罄」的**全部**展示出来；
 *   · 列表内可下拉（滚动）看到所有款号。
 *
 * ⚠ 原先这里有个 `.slice(0, 3)` —— 只把前 3 条放进 DOM，
 *   第 4 条起**根本不存在**，所以怎么滚都看不到（用户反馈的正是这个）。
 *   现不再截断，改由容器限高 + 滚动来容纳：`.inbound-list` 设 max-height
 *   并 overflow-y:auto，全部行都在 DOM 里，滚到底即可看全。
 *
 * 排序：已售罄 → 即将售罄 → 其余；同档按预测销量降序（销量大的先看）。
 */
const trackList = computed(() => {
  const rank = (s: string) =>
    s === "已售罄" ? 0 : s === "即将售罄" ? 1 : 2;
  return [...presaleRows.value].sort((a, b) => {
    const d = rank(a.status) - rank(b.status);
    if (d !== 0) return d;
    return (b.forecast7 ?? 0) - (a.forecast7 ?? 0);
  });
});

/**
 * 跳到「在售款明细」页对应视图。
 *
 * 用户口径：预售/加库存的明细都能从 /temu/on-sale 看得到，
 * 所以三块都指过去，用 ?view= 落到具体视图。
 */
function jump(view: "todo" | "low") {
  router.push({ path: "/temu/on-sale", query: { view } });
}

/**
 * 点款号 → 进「销量-库存」页（/temu/presale），并带上该货号当搜索词。
 *
 * 用户 2026-10-15：点这个款号直接进销量-库存页看它。
 * 该页本身有 keyword 搜索框，这里用 ?code= 深链把它填好并切到「全部」页签，
 * 这样即使该行不在默认的「待办」筛选里也一定能看到。
 */
function goPresale(code: string) {
  if (!code) return;
  router.push({ path: "/temu/presale", query: { code } });
}
</script>

<style lang="scss" scoped>
.panel-box {
  background: rgba(10, 24, 52, 0.75);
  border: 1px solid rgba(46, 96, 153, 0.45);
  border-radius: 6px;
  padding: 10px 12px;
  backdrop-filter: blur(8px);
  // 三个面板挤在一列固定高度里，必须允许自己被压缩，
  // 否则内容溢出会把标题顶出可视区（用户截图里标题不见了就是这个原因）
  min-height: 0;
  overflow: hidden;
}

.panel-title-bar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding-bottom: 6px;
  border-bottom: 1px solid rgba(46, 96, 153, 0.3);
  margin-bottom: 6px;
  // 标题栏永远不参与压缩：宁可压缩下面的列表，也不能让标题被挤没
  flex-shrink: 0;
}

.title-left {
  display: flex;
  align-items: center;
  gap: 6px;
}

.indicator-bar {
  width: 3px;
  height: 14px;
  background: #67c23a;
  box-shadow: 0 0 8px #67c23a;
  border-radius: 2px;
}

.title-text {
  font-size: 13px;
  font-weight: 600;
  color: #67c23a;
  letter-spacing: 0.5px;
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

.verdict-bar {
  display: grid;
  grid-template-columns: repeat(3, 1fr);
  gap: 6px;
  margin-bottom: 8px;
  flex-shrink: 0;
}

.verdict-item {
  display: flex;
  flex-direction: column;
  align-items: center;
  padding: 4px 0;
  border-radius: 4px;
  // 现在是 <button>，去掉浏览器默认外观
  font-family: inherit;
  cursor: pointer;
  transition: all 0.2s ease;

  &:hover {
    transform: translateY(-1px);
    filter: brightness(1.35);
  }

  &.presale {
    background: rgba(0, 234, 255, 0.12);
    border: 1px solid rgba(0, 234, 255, 0.3);
    .v-num { color: #00eaff; }
  }

  &.add-stock {
    background: rgba(103, 194, 58, 0.12);
    border: 1px solid rgba(103, 194, 58, 0.3);
    .v-num { color: #67c23a; }
  }

  &.restock {
    background: rgba(230, 162, 60, 0.12);
    border: 1px solid rgba(230, 162, 60, 0.3);
    .v-num { color: #e6a23c; }
  }
}

.v-num {
  font-size: 15px;
  font-weight: 700;
  line-height: 1.2;
}

.v-label {
  font-size: 9px;
  color: #8fb2dc;
}

// 标题旁的口径小标签
.title-hint {
  font-size: 9px;
  color: #6b8cb5;
  border: 1px solid rgba(46, 96, 153, 0.5);
  border-radius: 3px;
  padding: 0 4px;
  cursor: help;
  white-space: nowrap;
}

// 海外仓可补：一行总览
.topup-bar {
  display: flex;
  align-items: baseline;
  gap: 4px;
  padding: 5px 8px;
  margin-bottom: 6px;
  border-radius: 4px;
  background: rgba(230, 162, 60, 0.1);
  border: 1px solid rgba(230, 162, 60, 0.25);
  flex-shrink: 0;
}

.topup-label {
  font-size: 10px;
  color: #8fb2dc;
}

.topup-num {
  font-size: 15px;
  font-weight: 700;
  color: #e6a23c;
  line-height: 1.1;
}

.topup-unit {
  font-size: 9px;
  color: #8fb2dc;
}

.topup-sub {
  font-size: 9px;
  color: #6b8cb5;
  margin-left: auto;
}

/* 清单小标题：说明这一列是什么 + 共几条（"可下拉"提示还有更多） */
.inbound-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  font-size: 10px;
  color: #8fb2dc;
  padding: 2px 2px 4px;
  flex-shrink: 0;
}

.inbound-head__count {
  color: #6b8cb5;
}

.inbound-list {
  display: flex;
  flex-direction: column;
  gap: 5px;
  // 2026-10-15：原来是 125px（只露 3 行且 DOM 里也只有 3 行）。
  // 现在全部行都在 DOM 里，这里给足高度让大部分直接可见，其余滚动查看。
  // 仍用 flex 收缩 + min-height:0，保证右栏三块并列时不会把兄弟面板挤爆。
  max-height: 200px;
  // 只有列表参与压缩；标题/三数字/可补总览都固定住
  flex: 1 1 auto;
  min-height: 88px;
  overflow-y: auto;
  // 滚动条常显（大屏是常驻页面，鼠标可能不在上面，隐藏滚动条就看不出还有内容）
  scrollbar-width: thin;
  scrollbar-color: rgba(103, 194, 58, 0.45) transparent;

  &::-webkit-scrollbar {
    width: 6px;
  }
  &::-webkit-scrollbar-track {
    background: rgba(255, 255, 255, 0.04);
    border-radius: 3px;
  }
  &::-webkit-scrollbar-thumb {
    background: rgba(103, 194, 58, 0.45);
    border-radius: 3px;

    &:hover {
      background: rgba(103, 194, 58, 0.75);
    }
  }
}

.inbound-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  background: rgba(14, 32, 68, 0.5);
  border: 1px solid rgba(103, 194, 58, 0.2);
  border-radius: 4px;
  padding: 4px 8px;
  font-size: 11px;

  // 已售罄：换红边，一眼看出这条最急
  &.is-soldout {
    border-color: rgba(245, 108, 108, 0.45);
    background: rgba(60, 20, 28, 0.45);
  }
}

// 售罄状态小标签
.status-tag {
  font-size: 8px;
  padding: 0 4px;
  border-radius: 2px;

  &.is-danger {
    background: rgba(245, 108, 108, 0.22);
    color: #ff5252;
  }
  &.is-warn {
    background: rgba(230, 162, 60, 0.22);
    color: #e6a23c;
  }
}

.inbound-left {
  display: flex;
  flex-direction: column;
  gap: 2px;
}

.sku-head {
  display: flex;
  align-items: center;
  gap: 5px;
}

.sku-code {
  font-size: 11px;
  font-weight: 600;
  color: #ffffff;
}

/* 可点的款号：手型 + 下划线暗示可点，悬浮变青色 */
.sku-code.is-link {
  cursor: pointer;
  text-decoration: underline;
  text-decoration-color: rgba(0, 234, 255, 0.35);
  text-underline-offset: 2px;
  transition:
    color 0.2s,
    text-decoration-color 0.2s;

  &:hover,
  &:focus-visible {
    color: #00eaff;
    text-decoration-color: #00eaff;
    outline: none;
  }
}

.size-tag {
  font-size: 9px;
  padding: 0 4px;
  border-radius: 2px;
  background: rgba(0, 234, 255, 0.15);
  color: #00eaff;
}

.arrival-info {
  display: flex;
  align-items: center;
  gap: 4px;
  font-size: 10px;
}

.days-pill {
  font-size: 8px;
  padding: 0 4px;
  border-radius: 2px;
  margin-left: 2px;

  &.urgent {
    background: rgba(245, 108, 108, 0.2);
    color: #ff5252;
  }
  &.normal {
    background: rgba(103, 194, 58, 0.2);
    color: #67c23a;
  }
}
</style>
