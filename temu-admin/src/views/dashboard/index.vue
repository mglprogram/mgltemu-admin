<template>
  <div class="dash">
    <section class="dash-header">
      <div class="card dash-header__card">
        <div class="dash-header__start">
          <div class="dash-avatar">
            <img v-if="userStore.userInfo.avatar" :src="userStore.userInfo.avatar" alt="" />
            <el-icon v-else :size="22"><User /></el-icon>
          </div>
          <div class="dash-header__text">
            <h1 class="dash-header__greeting">{{ greetings }}</h1>
            <p class="dash-header__date">{{ currentDateStr }}</p>
          </div>
        </div>
        <div class="dash-header__end">
          <a
            href="https://github.com/youlaitech/vue3-element-admin"
            target="_blank"
            title="GitHub"
            class="quick-link"
          >
            <span class="i-svg:github" />
            <span>GitHub</span>
          </a>
          <a
            href="https://gitee.com/youlaiorg/vue3-element-admin"
            target="_blank"
            title="Gitee"
            class="quick-link"
          >
            <span class="i-svg:gitee" />
            <span>Gitee</span>
          </a>
          <a
            href="https://gitcode.com/youlai/vue3-element-admin"
            target="_blank"
            title="GitCode"
            class="quick-link"
          >
            <span class="i-svg:gitcode" />
            <span>GitCode</span>
          </a>
          <a
            href="https://juejin.cn/post/7228990409909108793"
            target="_blank"
            title="文档"
            class="quick-link"
          >
            <el-icon><Document /></el-icon>
            <span>文档</span>
          </a>
          <a
            href="https://www.bilibili.com/video/BV1eFUuYyEFj"
            target="_blank"
            title="视频"
            class="quick-link"
          >
            <el-icon><VideoPlay /></el-icon>
            <span>视频</span>
          </a>
        </div>
      </div>
    </section>

    <section class="dash-quick">
      <div
        class="quick-card quick-card--danger"
        role="link"
        tabindex="0"
        @click="go('/temu/limited')"
        @keydown.enter="go('/temu/limited')"
      >
        <div class="quick-card__icon">
          <el-icon :size="22"><Warning /></el-icon>
        </div>
        <div class="quick-card__body">
          <div class="quick-card__title">
            重点关注 SPU 被限流
            <el-icon class="quick-card__arrow" :size="13"><ArrowRight /></el-icon>
          </div>
          <div class="quick-card__sub">
            {{ focusChecked ? `${focusCheckedAt} 检查 · 共 ${focusTotal} 个重点 SPU` : "加载中…" }}
          </div>
          <div class="quick-card__stat">
            <span
              class="quick-card__stat-num"
              :class="focusHits.length ? 'is-danger' : 'is-ok'"
            >
              {{ focusChecked ? focusHits.length : "—" }}
            </span>
            <span class="quick-card__stat-label">个被限流</span>
            <el-tag
              v-if="focusChecked"
              :type="focusHits.length ? 'danger' : 'success'"
              size="small"
              effect="light"
            >
              {{ focusHits.length ? "需处理" : "全部正常" }}
            </el-tag>
          </div>
        </div>
      </div>
      <!-- 在售款 · 款-库存提醒（用户 2026-10-12 要求） -->
      <div
        class="quick-card"
        :class="lowCardTone"
        role="link"
        tabindex="0"
        @click="go('/temu/on-sale?view=low')"
        @keydown.enter="go('/temu/on-sale?view=low')"
      >
        <div class="quick-card__icon">
          <el-icon :size="22"><Box /></el-icon>
        </div>
        <div class="quick-card__body">
          <div class="quick-card__title">
            在售款 · 款-库存提醒
            <el-icon class="quick-card__arrow" :size="13"><ArrowRight /></el-icon>
          </div>

          <template v-if="lowLoaded">
            <div class="quick-card__sub">
              库存 < {{ lowThreshold }} 的款·尺码 · 数据 {{ lowGeneratedAt }}
            </div>
            <!--
              ⚠ 口径（2026-10-14 修）：lowTotal 是 lowStock.summary.sku，即**款×尺码**
                行数（同一个款按尺码重复 3~5 次）。原先大字直接显示它，161 会被读成
                "161 个款要补货"，实际只有 57 个款。改为主显示**款数**（lowStyle），
                尺码数作副标，与「在售款明细」页「N 个款 / M 个尺码」口径一致。
            -->
            <div class="quick-card__stat">
              <span
                class="quick-card__stat-num"
                :class="lowZero ? 'is-danger' : lowStyle ? 'is-warn' : 'is-ok'"
              >
                {{ lowStyle }}
              </span>
              <span class="quick-card__stat-label">个款待补货</span>
              <span class="quick-card__stat-sub">/ {{ lowTotal }} 个尺码</span>
              <el-tag v-if="lowCanRestock" type="success" size="small" effect="light">
                可加库存 {{ lowCanRestock }}
              </el-tag>
              <el-tag v-if="lowPreSale" type="primary" size="small" effect="light">
                {{ lowPreSale }} 个可开预售
              </el-tag>
              <el-tag v-if="lowZero" type="danger" size="small" effect="light">
                {{ lowZero }} 个已断货
              </el-tag>
            </div>

            <ul v-if="lowTop.length" class="hit-list">
              <li v-for="r in lowTop" :key="r.store + r.code + r.size" class="hit-list__item">
                <span class="hit-list__store">{{ r.store }}</span>
                <span class="hit-list__spu">{{ r.code }}</span>
                <span class="low-tag">{{ r.size || "—" }}</span>
                <span class="hit-list__gap">
                  现有 {{ r.stock }} · 仓内 {{ r.whStockRaw ?? 0 }} ·
                  <b class="low-add">建议加 {{ r.suggestAdd }}</b>
                </span>
              </li>
            </ul>

            <!-- 仓里没货但 7 天内有到货：提醒开预售 -->
            <ul v-if="lowPreSaleList.length" class="hit-list">
              <li v-for="r in lowPreSaleList" :key="'ps' + r.store + r.code + r.size"
                  class="hit-list__item">
                <span class="hit-list__store">{{ r.store }}</span>
                <span class="hit-list__spu">{{ r.code }}</span>
                <span class="low-tag">{{ r.size || "—" }}</span>
                <span class="hit-list__gap">
                  <b class="low-presale">可开预售</b> · {{ r.arrivalDate }} 到货
                  {{ r.inboundConfirmed ?? 0 }} 件（{{ r.daysToArrival }} 天）
                </span>
              </li>
            </ul>

            <div v-else class="quick-card__ok">
              <el-icon :size="14"><CircleCheck /></el-icon>
              <span>没有库存不高于 {{ lowThreshold }} 的在售款</span>
            </div>
          </template>

          <template v-else>
            <div class="quick-card__sub">加载中…</div>
          </template>
        </div>
      </div>

      <!-- 自动化任务执行情况 -->
      <div
        class="quick-card"
        :class="autoCardTone"
        role="link"
        tabindex="0"
        @click="go('/automation/timeline')"
        @keydown.enter="go('/automation/timeline')"
      >
        <div class="quick-card__icon">
          <el-icon :size="22"><AlarmClock /></el-icon>
        </div>
        <div class="quick-card__body">
          <div class="quick-card__title">
            自动化任务
            <el-icon class="quick-card__arrow" :size="13"><ArrowRight /></el-icon>
          </div>

          <template v-if="autoLoaded">
            <div class="quick-card__sub">
              共 {{ autoSummary.total }} 个任务 · 生成于 {{ autoGeneratedAt }}
              <span v-if="autoConflicts" class="quick-card__conflict">
                · ⚠ {{ autoConflicts }} 个时刻冲突
              </span>
            </div>

            <template v-if="autoProblems.length">
              <ul class="hit-list">
                <li v-for="t in autoProblems" :key="t.name" class="hit-list__item">
                  <span class="hit-list__store">{{ statusLabel(t.status) }}</span>
                  <span class="hit-list__spu">{{ t.label || t.name }}</span>
                  <span class="hit-list__gap">{{ t.reason }}</span>
                </li>
              </ul>
            </template>

            <div v-else class="quick-card__ok">
              <el-icon :size="14"><CircleCheck /></el-icon>
              <span>{{ autoSummary.ok }} 个任务近期执行正常，无需处理</span>
            </div>
          </template>

          <template v-else>
            <div class="quick-card__sub">加载中…</div>
          </template>
        </div>
      </div>
    </section>

    <section class="dash-stats">
      <div class="stat-card">
        <div class="stat-card__icon stat-card__icon--online">
          <el-icon :size="18"><Connection /></el-icon>
        </div>
        <div class="stat-card__body">
          <span class="stat-card__label">在线用户</span>
          <span class="stat-card__num">{{ onlineUserCount }}</span>
        </div>
        <span
          :class="[
            'stat-card__badge',
            isConnected ? 'stat-card__badge--on' : 'stat-card__badge--off',
          ]"
        >
          {{ isConnected ? "实时" : "离线" }}
        </span>
      </div>

      <div class="stat-card">
        <div class="stat-card__icon stat-card__icon--account">
          <span class="stat-card__svg i-svg:group" />
        </div>
        <div class="stat-card__body">
          <span class="stat-card__label">系统用户</span>
          <span class="stat-card__num">6</span>
        </div>
        <span :class="['stat-card__trend', `stat-card__trend--${systemTrendTone}`]">
          <el-icon :size="12"><ArrowUp /></el-icon>
          12.5%
        </span>
      </div>
    </section>

    <section class="dash-chart">
      <div class="card dash-chart__overview">
        <div class="card__head">
          <h3 class="card__title">待办概览</h3>
          <el-tag type="primary" size="small" effect="plain">5 项待处理</el-tag>
        </div>
        <div class="card__body overview-card">
          <div class="overview-summary">
            <div v-for="item in todoSummaryItems" :key="item.label" class="overview-summary__item">
              <span class="overview-summary__label">{{ item.label }}</span>
              <strong class="overview-summary__value">{{ item.value }}</strong>
            </div>
          </div>
          <div class="overview-bars">
            <div
              v-for="item in todoOverviewItems"
              :key="item.label"
              class="overview-bars__item"
              :style="{
                '--overview-percent': `${item.percent}%`,
              }"
            >
              <div class="overview-bars__meta">
                <span class="overview-bars__label">
                  <span class="overview-bars__dot" />
                  {{ item.label }}
                </span>
                <span class="overview-bars__value">{{ item.value }} 项</span>
              </div>
              <span class="overview-bars__track">
                <span class="overview-bars__bar" />
              </span>
            </div>
          </div>
        </div>
      </div>
    </section>

    <section class="dash-bottom">
      <div class="card">
        <div class="card__head">
          <h3 class="card__title">待办事项</h3>
          <el-tag size="small" round>5 项</el-tag>
        </div>
        <div class="card__body">
          <div
            v-for="todo in todoItems"
            :key="todo.id"
            class="todo-row"
            :class="{ 'todo-row--done': todo.done }"
          >
            <el-icon
              :size="16"
              :class="todo.done ? 'todo-row__icon--done' : 'todo-row__icon--pending'"
            >
              <CircleCheck v-if="todo.done" />
              <Clock v-else />
            </el-icon>
            <span class="todo-row__title">{{ todo.title }}</span>
            <el-tag
              :type="todo.done ? 'success' : todo.tag === '工单' ? 'warning' : 'info'"
              size="small"
              effect="plain"
              class="todo-row__tag"
            >
              {{ todo.tag }}
            </el-tag>
            <span class="todo-row__time">{{ todo.time }}</span>
          </div>
        </div>
      </div>

      <div class="card">
        <div class="card__head">
          <h3 class="card__title">系统动态</h3>
        </div>
        <div class="card__body card__body--scroll">
          <div class="feed">
            <div v-for="item in activities" :key="item.id" class="feed__item">
              <span class="feed__dot" />
              <span class="feed__text">{{ item.content }}</span>
              <span class="feed__time">{{ item.time }}</span>
            </div>
          </div>
        </div>
      </div>
    </section>
  </div>
</template>

<script setup lang="ts">
defineOptions({ name: "Dashboard", inheritAttrs: false });

import { ref } from "vue";
import { useUserStore } from "@/stores/user";
import { useSettingsStore } from "@/stores/settings";
import {
  User,
  Connection,
  ArrowUp,
  ArrowRight,
  Clock,
  CircleCheck,
  Document,
  VideoPlay,
  Warning,
  AlarmClock,
  Box,
} from "@element-plus/icons-vue";
import { useOnlineUsers } from "./composables/useOnlineUsers";
import LimitedAPI from "@/api/temu/limited";
import GoodsAPI from "@/api/temu/goods";
import AutomationAPI from "@/api/automation";
import { useRouter } from "vue-router";

const router = useRouter();

const userStore = useUserStore();
const settingsStore = useSettingsStore();
const { onlineUserCount, isConnected } = useOnlineUsers();

// ---------------------------------------------------------------- 重点关注 SPU 被限流
// 读同一份静态数据（public/data/limited.json）里的 watch 段，只取「被限流数量」。
// 卡片上不放明细，保持首页干净；要看清点进限流监控页。
const focusHitCount = ref(0);
const focusTotal = ref(0);
const focusCheckedAt = ref("");
const focusChecked = ref(false);

// 命中条数（模板里用 length，这里统一暴露成数组长度语义）
const focusHits = computed(() => new Array(focusHitCount.value).fill(null));

/** 进入限流监控页 */
function go(path: string) {
  router.push(path);
}

/** 读卡片数据，失败就留空（不影响跳转） */
async function loadQuickStats() {
  try {
    const d = await LimitedAPI.getLimitedData();
    focusHitCount.value = d.watch.reduce((a, w) => a + (w.hitCount || 0), 0);
    focusTotal.value = d.watch.reduce((a, w) => a + (w.focusTotal || 0), 0);
    // 快照时间取最新一次检查
    focusCheckedAt.value =
      d.watch
        .map((w) => w.checkedAt)
        .filter(Boolean)
        .sort()
        .pop() ?? "";
    focusChecked.value = true;
  } catch {
    focusChecked.value = false;
  }
}

onMounted(loadQuickStats);

// ---------------------------------------------------------------- 在售款 · 款-库存提醒
// 读同一份静态数据（public/data/goods.json）里构建期算好的 lowStock.summary，
// 首页只拿「几个待补货 + 前几条最紧急」，不遍历明细。
const lowLoaded = ref(false);
const lowGeneratedAt = ref("");
const lowThreshold = ref(10);
const lowTotal = ref(0);
const lowStyle = ref(0);
const lowZero = ref(0);
/** 「可加库存」条数（海外仓有货 + 算得出增加量） */
const lowCanRestock = ref(0);
/** 「可开预售」条数（仓里没货但 7 天内有到货） */
const lowPreSale = ref(0);
const lowPreSaleList = ref<
  {
    store: string;
    code: string;
    size?: string;
    arrivalDate?: string;
    daysToArrival?: number | null;
    inboundConfirmed?: number | null;
  }[]
>([]);
const lowTop = ref<
  {
    store: string;
    code: string;
    size: string;
    stock: number | null;
    whStockRaw?: number | null;
    suggestAdd?: number | null;
  }[]
>([]);

/** 有断货 → 红；只有低库存 → 橙；都没事 → 绿 */
const lowCardTone = computed(() => {
  if (!lowLoaded.value) return "";
  if (lowZero.value > 0) return "quick-card--danger";
  // 判色用 lowStyle（卡片大字显示的款数），与展示口径保持一致
  if (lowStyle.value > 0) return "quick-card--warn";
  return "quick-card--ok";
});

async function loadLowStock() {
  try {
    const d = await GoodsAPI.getGoodsData();
    lowGeneratedAt.value = d.generatedAt ?? "";
    lowThreshold.value = d.lowStockThreshold ?? 10;
    lowTotal.value = d.lowStock?.summary?.sku ?? 0;
    lowStyle.value = d.lowStock?.summary?.style ?? 0;
    lowZero.value = d.lowStock?.summary?.zero ?? 0;
    lowCanRestock.value = d.lowStock?.restock?.verdictCounts?.["可加库存"] ?? 0;
    lowPreSale.value = d.lowStock?.restock?.verdictCounts?.["可开预售"] ?? 0;
    lowPreSaleList.value = (d.lowStock?.restock?.preSaleRows ?? []).slice(0, 3);
    // 首页只列「建议加得最多」的前几条（后端已按建议加量降序给好）
    lowTop.value = (d.lowStock?.restock?.top ?? []).slice(0, 4).map((r) => ({
      store: r.store,
      code: r.code,
      size: r.size ?? "",
      stock: r.stock,
      whStockRaw: r.whStockRaw,
      suggestAdd: r.suggestAdd,
    }));
    lowLoaded.value = true;
  } catch {
    lowLoaded.value = false;
  }
}

onMounted(loadLowStock);

// ---------------------------------------------------------------- 自动化任务
// 读 public/data/automation.json，首页只列「失败 + 告警 + 未执行」的，点进详情页看全部。
const autoLoaded = ref(false);
const autoGeneratedAt = ref("");
const autoSummary = ref({
  total: 0,
  ok: 0,
  fail: 0,
  alert: 0,
  warn: 0,
  never: 0,
  problem: 0,
});
const autoProblems = ref<
  { name: string; label?: string; status: string; reason: string }[]
>([]);

/** 有失败 -> 红；只有未执行 -> 橙；全正常 -> 绿 */
const autoCardTone = computed(() => {
  if (!autoLoaded.value) return "";
  if (autoSummary.value.fail > 0) return "quick-card--danger";
  if (
    autoSummary.value.alert > 0 ||
    autoSummary.value.warn > 0 ||
    autoSummary.value.never > 0
  ) {
    return "quick-card--warn";
  }
  return "quick-card--ok";
});

const autoConflicts = ref(0);

/** 首页卡片里的状态短标签 */
function statusLabel(st: string) {
  return (
    {
      fail: "失败",
      alert: "告警",
      warn: "部分失败",
      never: "未执行",
    } as Record<string, string>
  )[st] ?? st;
}

async function loadAutomation() {
  try {
    const d = await AutomationAPI.getAutomationData();
    autoGeneratedAt.value = d.generatedAt;
    autoSummary.value = d.summary;
    autoConflicts.value = d.conflicts?.length ?? 0;
    autoProblems.value = d.tasks
      .filter((t) => ["fail", "alert", "warn", "never"].includes(t.status))
      .slice(0, 6)
      .map((t) => ({
        name: t.name,
        label: t.label,
        status: t.status,
        reason: t.reason,
      }));
    autoLoaded.value = true;
  } catch {
    autoLoaded.value = false;
  }
}

onMounted(loadAutomation);

const hours = new Date().getHours();
const greetings = computed(() => {
  const n = userStore.userInfo.nickname;
  if (hours >= 6 && hours < 8) return `早安，${n}`;
  if (hours >= 8 && hours < 12) return `上午好，${n}`;
  if (hours >= 12 && hours < 18) return `下午好，${n}`;
  if (hours >= 18 && hours < 24) return `晚上好，${n}`;
  return `夜深了，${n}`;
});

const currentDateStr = computed(() => {
  const d = new Date();
  const w = ["日", "一", "二", "三", "四", "五", "六"];
  return `${d.getFullYear()}年${d.getMonth() + 1}月${d.getDate()}日 星期${w[d.getDay()]}`;
});

interface TodoItem {
  id: number;
  title: string;
  tag: string;
  time: string;
  done: boolean;
}

const todoItems: TodoItem[] = [
  {
    id: 1,
    title: "审批：张三提交的请假申请",
    tag: "审批",
    time: "10分钟前",
    done: false,
  },
  {
    id: 2,
    title: "审核：新用户注册信息核实",
    tag: "审核",
    time: "30分钟前",
    done: false,
  },
  {
    id: 3,
    title: "发布：系统维护通知公告",
    tag: "通知",
    time: "1小时前",
    done: false,
  },
  {
    id: 4,
    title: "处理：工单 #TSK-20240509",
    tag: "工单",
    time: "2小时前",
    done: false,
  },
  {
    id: 5,
    title: "更新：用户角色权限配置",
    tag: "配置",
    time: "昨天 15:30",
    done: true,
  },
];

interface Activity {
  id: number;
  content: string;
  time: string;
}

const activities: Activity[] = [
  { id: 1, content: "管理员 admin 登录系统", time: "3分钟前" },
  { id: 2, content: "新增用户李四，角色为普通用户", time: "25分钟前" },
  { id: 3, content: "系统配置项「登录策略」已更新", time: "1小时前" },
  { id: 4, content: "数据库自动备份任务执行完成", time: "3小时前" },
  { id: 5, content: "角色权限批量修改：运营组新增导出权限", time: "昨天 16:42" },
  { id: 6, content: "SSL 证书已自动续期", time: "昨天 09:15" },
];

const todoOverviewItems = [
  { label: "审批", value: "2", percent: 40, tone: "primary" },
  { label: "审核", value: "1", percent: 20, tone: "primary" },
  { label: "通知", value: "1", percent: 20, tone: "primary" },
  { label: "工单", value: "1", percent: 20, tone: "primary" },
];

const todoSummaryItems = [
  { label: "今日新增", value: "3", tone: "primary" },
  { label: "即将超时", value: "1", tone: "primary" },
  { label: "今日完成", value: "1", tone: "success" },
];

const systemTrendTone = "success";

/**
 * 读取 CSS 变量，取不到时用兜底值
 */
function getCssVar(name: string, fallback: string) {
  if (typeof window === "undefined") return fallback;

  return getComputedStyle(document.documentElement).getPropertyValue(name).trim() || fallback;
}

/**
 * 给颜色加上透明度
 */
function colorWithAlpha(color: string, alpha: number) {
  const value = color.trim();

  if (value.startsWith("#")) {
    const hex =
      value.length === 4
        ? `#${value[1]}${value[1]}${value[2]}${value[2]}${value[3]}${value[3]}`
        : value;
    const rgb = Number.parseInt(hex.slice(1), 16);
    const r = (rgb >> 16) & 255;
    const g = (rgb >> 8) & 255;
    const b = rgb & 255;

    return `rgba(${r}, ${g}, ${b}, ${alpha})`;
  }

  const parts = value.match(/\d+(\.\d+)?/g);
  if (parts && parts.length >= 3) {
    return `rgba(${parts[0]}, ${parts[1]}, ${parts[2]}, ${alpha})`;
  }

  return value;
}

</script>

<style lang="scss" scoped>
// Tokens
$gap: 12px;
$pad: 10px;
%card {
  overflow: hidden;
  background: var(--el-bg-color-overlay);
  border: 1px solid var(--card-border);
  border-radius: var(--card-radius);
  box-shadow: var(--card-shadow);
}

// Page
.dash {
  display: flex;
  flex-direction: column;
  gap: $gap;
  padding: $pad;
  background: var(--page-bg);
}

// Header
.dash-header {
  &__card {
    display: flex;
    flex-wrap: wrap;
    gap: 18px;
    align-items: center;
    justify-content: space-between;
    min-height: 78px;
    padding: 16px 18px;
  }

  &__start {
    display: flex;
    flex: 1;
    gap: 12px;
    align-items: center;
    min-width: 260px;
  }

  &__text {
    display: flex;
    flex-direction: column;
    gap: 3px;
  }

  &__greeting {
    margin: 0;
    font-size: 18px;
    font-weight: 500;
    line-height: 1.3;
    color: var(--el-text-color-primary);
  }

  &__date {
    margin: 0;
    font-size: 12px;
    color: var(--el-text-color-secondary);
  }

  &__end {
    display: flex;
    flex-wrap: wrap;
    gap: 8px;
    align-items: center;
    justify-content: flex-end;
  }
}

.dash-avatar {
  display: flex;
  flex-shrink: 0;
  align-items: center;
  justify-content: center;
  width: 40px;
  height: 40px;
  overflow: hidden;
  color: var(--el-color-primary);
  background: color-mix(in srgb, var(--el-color-primary) 14%, var(--el-bg-color-overlay));
  border: 1px solid color-mix(in srgb, var(--el-color-primary) 18%, transparent);
  border-radius: 50%;

  img {
    width: 100%;
    height: 100%;
    object-fit: cover;
  }
}

// Quick links
.quick-link {
  display: inline-flex;
  gap: 6px;
  align-items: center;
  justify-content: center;
  height: 30px;
  padding: 0 10px;
  font-size: 12px;
  color: var(--el-text-color-secondary);
  text-decoration: none;
  background: var(--el-fill-color-extra-light);
  border: 1px solid var(--el-border-color-lighter);
  border-radius: 6px;
  transition:
    color 0.15s,
    background-color 0.15s,
    border-color 0.15s;

  .el-icon,
  [class^="i-svg:"] {
    width: 15px;
    height: 15px;
    font-size: 15px;
    color: currentcolor;
  }

  &:hover {
    color: var(--el-color-primary);
    background: color-mix(in srgb, var(--el-color-primary) 7%, var(--el-bg-color-overlay));
    border-color: color-mix(in srgb, var(--el-color-primary) 20%, var(--el-border-color-lighter));
  }
}

// Stat cards
.dash-quick {
  display: grid;
  grid-template-columns: 1fr;
  gap: $gap;
}

.quick-card {
  display: flex;
  gap: 14px;
  align-items: flex-start;
  padding: 18px;
  cursor: pointer;
  transition:
    border-color 0.16s,
    transform 0.16s;
  @extend %card;

  &:hover {
    transform: translateY(-1px);

    .quick-card__arrow {
      opacity: 1;
      transform: translateX(2px);
    }
  }

  &:focus-visible {
    outline: 2px solid var(--el-color-primary);
    outline-offset: 2px;
  }

  &__icon {
    display: flex;
    flex-shrink: 0;
    align-items: center;
    justify-content: center;
    width: 44px;
    height: 44px;
    border-radius: 10px;
  }

  &--danger &__icon {
    color: var(--el-color-danger);
    background: color-mix(in srgb, var(--el-color-danger) 10%, var(--el-bg-color-overlay));
  }

  &--primary &__icon {
    color: var(--el-color-primary);
    background: color-mix(in srgb, var(--el-color-primary) 10%, var(--el-bg-color-overlay));
  }

  &--warn &__icon {
    color: var(--el-color-warning);
    background: color-mix(in srgb, var(--el-color-warning) 12%, var(--el-bg-color-overlay));
  }

  &--ok &__icon {
    color: var(--el-color-success);
    background: color-mix(in srgb, var(--el-color-success) 10%, var(--el-bg-color-overlay));
  }

  &__ok {
    display: flex;
    gap: 6px;
    align-items: center;
    margin-top: 8px;
    font-size: 13px;
    color: var(--el-color-success);
  }

  &__body {
    flex: 1;
    min-width: 0;
  }

  &__title {
    display: flex;
    gap: 4px;
    align-items: center;
    font-size: 15px;
    font-weight: 600;
    color: var(--el-text-color-primary);
  }

  &__arrow {
    opacity: 0;
    transition:
      opacity 0.16s,
      transform 0.16s;
  }

  &__sub {
    margin-top: 4px;
    font-size: 12px;
    color: var(--el-text-color-secondary);
  }

  &__stat {
    display: flex;
    gap: 8px;
    align-items: baseline;
    margin-top: 10px;
  }

  &__stat-num {
    font-size: 26px;
    font-weight: 600;
    line-height: 1;
    font-variant-numeric: tabular-nums;

    &.is-danger {
      color: var(--el-color-danger);
    }

    &.is-warn {
      color: var(--el-color-warning);
    }

    &.is-ok {
      color: var(--el-color-success);
    }
  }

  &__stat-label {
    font-size: 12px;
    color: var(--el-text-color-placeholder);
  }

  // 副标（如「/ 161 个尺码」）：比 stat-label 更淡，不抢主数字
  &__stat-sub {
    font-size: 12px;
    color: var(--el-text-color-placeholder);
    opacity: 0.75;
  }
}

.quick-card__conflict {
  color: var(--el-color-warning-dark-2);
}

.hit-list {
  margin: 8px 0 0;
  padding: 0;
  list-style: none;

  &__item {
    display: flex;
    gap: 8px;
    align-items: baseline;
    padding: 5px 0;
    font-size: 12px;
    border-top: 1px dashed var(--el-border-color-lighter);

    &:first-child {
      border-top: 0;
    }
  }

  &__store {
    flex: 0 0 40px;
    font-weight: 600;
    color: var(--el-color-danger);
  }

  &__spu {
    flex: 0 0 auto;
    max-width: 220px;
    overflow: hidden;
    font-family: ui-monospace, Consolas, monospace;
    font-weight: 600;
    color: var(--el-text-color-primary);
    text-overflow: ellipsis;
    white-space: nowrap;
  }

  &__gap {
    flex: 1;
    min-width: 0;
    color: var(--el-text-color-secondary);
  }
}

// 低库存卡片里的「尺码」小标签
.low-tag {
  flex: 0 0 auto;
  padding: 0 6px;
  font-size: 11px;
  color: var(--el-text-color-secondary);
  background: var(--el-fill-color-light);
  border-radius: 4px;
}

.low-add {
  font-weight: 600;
  color: var(--el-color-success);
}

// 可开预售（仓里没货、7 天内有到货）
.low-presale {
  padding: 0 5px;
  font-weight: 600;
  color: var(--el-color-primary);
  background: var(--el-color-primary-light-9);
  border-radius: 3px;
}

.dash-stats {
  display: grid;
  grid-template-columns: repeat(4, 1fr);
  gap: $gap;
}

.stat-card {
  display: flex;
  gap: 14px;
  align-items: center;
  min-height: 84px;
  padding: 18px;
  @extend %card;

  &__icon {
    display: flex;
    flex-shrink: 0;
    align-items: center;
    justify-content: center;
    width: 44px;
    height: 44px;
    border-radius: 10px;

    &--online {
      color: var(--el-color-primary);
      background: color-mix(in srgb, var(--el-color-primary) 10%, var(--el-bg-color-overlay));
    }
    &--visitor {
      color: var(--el-color-primary);
      background: color-mix(in srgb, var(--el-color-primary) 8%, var(--el-bg-color-overlay));
    }
    &--view {
      color: var(--el-color-primary);
      background: color-mix(in srgb, var(--el-color-primary) 8%, var(--el-bg-color-overlay));
    }
    &--account {
      color: var(--el-color-primary);
      background: color-mix(in srgb, var(--el-color-primary) 8%, var(--el-bg-color-overlay));
    }
  }

  &__svg {
    width: 20px;
    height: 20px;
    font-size: 20px;
    color: currentcolor;
  }

  &__body {
    display: flex;
    flex: 1;
    flex-direction: column;
    min-width: 0;
  }

  &__num {
    font-size: 24px;
    font-weight: 600;
    line-height: 1.15;
    color: var(--el-text-color-primary);
  }

  &__label {
    margin-bottom: 3px;
    font-size: 13px;
    color: var(--el-text-color-secondary);
  }

  &__badge {
    flex-shrink: 0;
    font-size: 11px;
    font-weight: 500;

    &--on {
      color: var(--el-color-success);
    }
    &--off {
      color: var(--el-text-color-secondary);
    }
  }

  &__trend {
    display: inline-flex;
    flex-shrink: 0;
    gap: 3px;
    align-items: center;
    font-size: 12px;
    font-weight: 700;
    color: var(--el-text-color-secondary);

    &--success {
      color: var(--el-color-success);
    }

    &--danger {
      color: var(--el-color-danger);
    }
  }
}

// Generic card
.card {
  display: flex;
  flex-direction: column;
  @extend %card;

  &__head {
    display: flex;
    align-items: center;
    justify-content: space-between;
    min-height: 48px;
    padding: 13px 18px;
    border-bottom: 1px solid var(--card-border);
  }

  &__title {
    margin: 0;
    font-size: 14px;
    font-weight: 600;
    color: var(--el-text-color-primary);
  }

  &__body {
    padding: 16px 18px 18px;

    &--chart {
      padding: 14px 18px 16px;
    }

    &--scroll {
      flex: 1;
      padding: 0;
      overflow-y: auto;
    }
  }
}

.dash-header__card {
  flex-direction: row;
}

// Chart & bottom grids
// 访问趋势图已移除（它读的是 /api/v1/logs/analytics/*，静态托管下必然报错），
// 现在只剩「待办概览」一张卡，所以单列铺满、不再用 3fr/1fr 分栏（否则右侧留一条空档）。
.dash-chart {
  display: grid;
  grid-template-columns: minmax(0, 1fr);
  gap: $gap;
}

.dash-chart__overview {
  min-width: 0;
}

.overview-card {
  display: flex;
  flex: 1;
  flex-direction: column;
  gap: 20px;
  min-height: 0;
}

.overview-bars {
  display: flex;
  flex: 1;
  flex-direction: column;
  justify-content: space-between;
  min-height: 142px;
  padding: 0;

  &__item {
    display: flex;
    flex-direction: column;
    gap: 7px;
  }

  &__meta {
    display: flex;
    gap: 10px;
    align-items: center;
    justify-content: space-between;
    min-width: 0;
  }

  &__label,
  &__value {
    font-size: 12px;
    color: var(--el-text-color-secondary);
  }

  &__value {
    flex-shrink: 0;
    text-align: right;
  }

  &__label {
    display: inline-flex;
    gap: 6px;
    align-items: center;
    min-width: 0;
  }

  &__dot {
    width: 6px;
    height: 6px;
    background: var(--el-color-primary);
    border-radius: 50%;
  }

  &__track {
    height: 5px;
    overflow: hidden;
    background: color-mix(in srgb, var(--el-color-primary) 10%, var(--el-fill-color-light));
    border-radius: 999px;
  }

  &__bar {
    display: block;
    width: var(--overview-percent);
    height: 100%;
    background: linear-gradient(90deg, var(--el-color-primary), var(--el-color-primary-light-3));
    border-radius: inherit;
  }
}

.overview-summary {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 8px;

  &__item {
    position: relative;
    display: flex;
    flex-direction: column;
    justify-content: center;
    min-width: 0;
    min-height: 64px;
    padding: 12px;
    overflow: hidden;
    background: color-mix(in srgb, var(--el-color-primary) 4%, var(--el-bg-color-overlay));
    border: 1px solid
      color-mix(in srgb, var(--el-color-primary) 10%, var(--el-border-color-lighter));
    border-radius: 6px;

    &:nth-child(2) {
      background: color-mix(in srgb, var(--el-color-primary) 6%, var(--el-bg-color-overlay));
      border-color: color-mix(in srgb, var(--el-color-primary) 14%, var(--el-border-color-lighter));

      &::before {
        background: color-mix(in srgb, var(--el-color-primary) 48%, transparent);
      }

      .overview-summary__value {
        color: var(--el-color-warning);
      }
    }

    &:nth-child(3) {
      background: color-mix(in srgb, var(--el-color-primary) 3%, var(--el-bg-color-overlay));
      border-color: color-mix(in srgb, var(--el-color-primary) 8%, var(--el-border-color-lighter));

      &::before {
        background: color-mix(in srgb, var(--el-color-primary) 36%, transparent);
      }

      .overview-summary__value {
        color: var(--el-color-success);
      }
    }

    &::before {
      position: absolute;
      top: 0;
      left: 0;
      width: 100%;
      height: 2px;
      content: "";
      background: color-mix(in srgb, var(--el-color-primary) 62%, transparent);
    }
  }

  &__label {
    overflow: hidden;
    text-overflow: ellipsis;
    font-size: 12px;
    color: var(--el-text-color-secondary);
    white-space: nowrap;
  }

  &__value {
    flex-shrink: 0;
    margin-top: 5px;
    font-size: 18px;
    font-weight: 600;
    line-height: 1.1;
    color: var(--el-color-primary);
  }
}

.dash-bottom {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: $gap;
}

// Todo rows
.todo-row {
  display: flex;
  gap: 10px;
  align-items: center;
  padding: 11px 0;

  & + & {
    border-top: 1px solid var(--el-border-color-lighter);
  }

  &--done {
    .todo-row__title {
      color: var(--el-text-color-placeholder);
      text-decoration: line-through;
    }
  }

  &__icon--pending {
    flex-shrink: 0;
    color: var(--el-color-primary);
  }
  &__icon--done {
    flex-shrink: 0;
    color: var(--el-color-success);
  }

  &__title {
    flex: 1;
    min-width: 0;
    overflow: hidden;
    text-overflow: ellipsis;
    font-size: 13px;
    color: var(--el-text-color-regular);
    white-space: nowrap;
  }

  &__tag {
    flex-shrink: 0;
    color: var(--el-text-color-secondary);
    background: var(--el-fill-color-light);

    &.el-tag--warning {
      color: color-mix(in srgb, var(--el-color-warning) 78%, var(--el-text-color-primary));
      background: color-mix(in srgb, var(--el-color-warning) 9%, var(--el-bg-color-overlay));
    }

    &.el-tag--success {
      color: var(--el-color-success);
      background: color-mix(in srgb, var(--el-color-success) 8%, var(--el-bg-color-overlay));
    }
  }

  &__time {
    flex-shrink: 0;
    font-size: 12px;
    color: var(--el-text-color-secondary);
  }
}

// Activity feed
.feed {
  display: flex;
  flex-direction: column;
  padding: 10px 20px 16px;

  &__item {
    position: relative;
    display: flex;
    flex-wrap: wrap;
    gap: 8px;
    align-items: baseline;
    padding: 10px 0 10px 16px;

    &::before {
      position: absolute;
      top: 22px;
      bottom: -4px;
      left: 3px;
      width: 1px;
      content: "";
      background: var(--el-border-color-lighter);
    }

    &:last-child::before {
      display: none;
    }
  }

  &__dot {
    position: absolute;
    top: 12px;
    left: 0;
    width: 7px;
    height: 7px;
    background: var(--el-color-primary);
    border: 2px solid var(--el-color-primary-light-8);
    border-radius: 50%;
  }

  &__text {
    flex: 1;
    min-width: 0;
    font-size: 13px;
    line-height: 1.4;
    color: var(--el-text-color-regular);
  }

  &__time {
    flex-shrink: 0;
    font-size: 12px;
    color: var(--el-text-color-secondary);
  }
}

// Responsive
@media (max-width: 1200px) {
  .dash-stats {
    grid-template-columns: repeat(2, 1fr);
  }
  .dash-header__card {
    flex-direction: column;
    align-items: flex-start;
  }

  .dash-header__end {
    justify-content: flex-start;
  }
}

@media (max-width: 992px) {
  .dash-bottom {
    grid-template-columns: 1fr;
  }
}

@media (max-width: 768px) {
  .dash {
    gap: 10px;
    padding: 10px;
  }

  .dash-stats {
    grid-template-columns: 1fr;
  }
}
</style>
