<template>
  <div class="tl" v-loading="loading">
    <!-- 顶部：日期筛选 + 汇总 -->
    <section class="tl-head">
      <div class="tl-head__left">
        <h3 class="tl-title">今天的时间轴</h3>
        <p class="tl-sub">
          {{ data?.generatedAt ? `数据生成于 ${data.generatedAt}` : "加载中…" }}
          ·
          {{ todayTasks.length }} 个任务今天会跑
        </p>
      </div>
      <div class="tl-head__right">
        <span class="tl-hint">密集时段标签会自动省略，指向圆点看详情</span>
        <el-radio-group v-model="dayMode" size="small">
          <el-radio-button value="today">今天</el-radio-button>
          <el-radio-button value="all">全部（不限星期）</el-radio-button>
        </el-radio-group>
        <el-radio-group v-model="showMode" size="small">
          <el-radio-button value="enabled">仅启用</el-radio-button>
          <el-radio-button value="browser">仅用窗口</el-radio-button>
          <el-radio-button value="all">含已停用</el-radio-button>
        </el-radio-group>
        <el-button size="small" :loading="loading" @click="load">刷新</el-button>
      </div>
    </section>

    <!-- 真冲突：同一时刻 + 同一紫鸟店铺窗口 -->
    <el-alert
      v-if="conflicts.length"
      type="error"
      :closable="false"
      show-icon
      class="tl-alert"
    >
      <template #title>
        ⛔ 有 {{ conflicts.length }} 个时刻，两个任务会抢同一个紫鸟店铺窗口
      </template>
      <div class="tl-alert__body">
        <span v-for="c in conflicts" :key="c.time" class="tl-chip tl-chip--bad">
          {{ c.time }}
          <span
            v-for="p in c.pairs"
            :key="p.a + p.b"
            class="tl-chip__dim"
          >{{ shortName(p.a) }} ⚔ {{ shortName(p.b) }}（{{ p.stores.join("+") }}）</span>
        </span>
      </div>
    </el-alert>
    <el-alert v-else type="success" :closable="false" show-icon class="tl-alert">
      <template #title>
        ✓ 没有同刻抢窗口的任务（{{ browserTasks.length }} 个任务占用紫鸟窗口，时刻都错开了）
      </template>
    </el-alert>

    <!-- 时段重叠风险：前一个卡住会压到后一个（分典型/最坏两个口径） -->
    <el-alert
      v-if="typicalTight.length"
      type="warning"
      :closable="false"
      show-icon
      class="tl-alert"
    >
      <template #title>
        ⚠ {{ typicalTight.length }} 对任务平时就会时段重叠，建议错开
      </template>
    </el-alert>

    <section v-if="tightGroups.length" class="card tl-tight">
      <h4 class="tl-tight__title">
        时段重叠风险
        <span class="tl-tight__sub">
          任务卡住时（跑到超时上限）会压到后面的任务 —— 平时不会，因为实测都只要
          {{ maxTypical }} 分钟内
        </span>
      </h4>
      <div v-for="g in tightGroups" :key="g.a + g.aTime" class="tl-tight__row">
        <div class="tl-tight__head">
          <el-tag :type="g.severity === 'typical' ? 'danger' : 'warning'" size="small" effect="dark">
            {{ g.severity === "typical" ? "平时就重叠" : "仅卡住时" }}
          </el-tag>
          <b class="mono">{{ g.aTime }}</b>
          <b>{{ labelOf(g.a) }}</b>
          <span class="tl-tight__meta">
            超时 {{ g.aTimeout }} 分 / 典型 {{ g.aTypical }} 分
            · 周{{ g.weekdays.map(wdText).join("") }}
          </span>
        </div>
        <ul class="tl-tight__affects">
          <li v-for="f in g.affects" :key="f.b + f.bTime">
            会压到 <b class="mono">{{ f.bTime }}</b> {{ labelOf(f.b) }}
            <span class="tl-tight__dim">
              （相隔 {{ f.gapMinutes }} 分，最坏重叠 {{ f.worstOverlap }} 分，
              店 {{ f.stores.join("+") }}）
            </span>
          </li>
        </ul>
      </div>
    </section>

    <!-- 24 小时时间轴 -->
    <section class="tl-axis card">
      <div class="tl-axis__track">
        <div
          v-for="h in 24"
          :key="h"
          class="tl-hour"
          :class="{ 'tl-hour--busy': hourBusy(h - 1) }"
        >
          <span class="tl-hour__label">{{ String(h - 1).padStart(2, "0") }}</span>
        </div>
      </div>

      <!-- 任务点：横向定位 + 分车道错开 -->
      <div class="tl-marks" :style="{ height: marksHeight + 'px' }">
        <div
          v-for="m in marks"
          :key="m.key"
          class="tl-mark"
          :class="[`tl-mark--${m.status}`, { 'tl-mark--conflict': m.conflict }]"
          :style="{ left: m.pct + '%', top: m.lane * 24 + 'px' }"
          :title="m.title"
          @click="openDetail(m)"
        >
          <span class="tl-mark__dot" />
          <span v-if="m.showLabel" class="tl-mark__label">{{ m.time }}</span>
        </div>
      </div>
    </section>

    <!-- 按时刻分组的清单（比时间轴更易读） -->
    <section class="tl-list card">
      <div class="tl-list__head">
        <span class="tl-col tl-col--time">时刻</span>
        <span class="tl-col tl-col--task">任务</span>
        <span class="tl-col tl-col--status">状态</span>
        <span class="tl-col tl-col--last">上次执行</span>
        <span class="tl-col tl-col--rule">触发规则</span>
      </div>
      <div v-for="g in grouped" :key="g.time" class="tl-row" :class="{ 'tl-row--conflict': g.items.length > 1 }">
        <span class="tl-col tl-col--time">
          <b class="tl-time">{{ g.time }}</b>
          <el-tag v-if="g.items.length > 1" type="warning" size="small" effect="dark">
            ×{{ g.items.length }}
          </el-tag>
        </span>
        <span class="tl-col tl-col--task">
          <div v-for="t in g.items" :key="t.name" class="tl-task">
            <span class="tl-task__name">{{ t.label || t.name }}</span>
            <el-tag
              v-if="t.browserWindow"
              size="small"
              type="warning"
              effect="plain"
              class="tl-task__win"
            >🖥 {{ t.browserWindow }}</el-tag>
            <span class="tl-task__sub">{{ t.name }}</span>
          </div>
        </span>
        <span class="tl-col tl-col--status">
          <div v-for="t in g.items" :key="t.name" class="tl-task">
            <el-tag :type="tagType(t.status)" size="small">{{ statusText(t.status) }}</el-tag>
          </div>
        </span>
        <span class="tl-col tl-col--last">
          <div v-for="t in g.items" :key="t.name" class="tl-task mono">{{ fmt(t.lastRun) }}</div>
        </span>
        <span class="tl-col tl-col--rule">
          <div v-for="t in g.items" :key="t.name" class="tl-task mono">{{ t.rule }}</div>
        </span>
      </div>
      <el-empty v-if="!grouped.length" description="今天没有要跑的任务" />
    </section>

    <!-- 空闲时段建议 -->
    <section class="tl-free card">
      <h4 class="tl-free__title">可用的空闲时刻（想加新任务可挑这些）</h4>
      <div class="tl-free__body">
        <span v-for="f in freeSlots" :key="f" class="tl-chip">{{ f }}</span>
        <span v-if="!freeSlots.length" class="tl-free__none">（上午/下午几乎排满）</span>
      </div>
      <p class="tl-free__hint">
        已占用 {{ busySlots.length }} 个时刻；冲突时刻建议错开 ≥5 分钟。
      </p>
    </section>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from "vue";
import { ElMessageBox } from "element-plus";
import AutomationAPI, {
  type AutomationData,
  type AutomationTask,
  type TaskStatus,
} from "@/api/automation";

defineOptions({ name: "AutomationTimeline" });

const loading = ref(false);
const data = ref<AutomationData | null>(null);
const dayMode = ref<"today" | "all">("today");
const showMode = ref<"enabled" | "browser" | "all">("enabled");

async function load() {
  loading.value = true;
  try {
    data.value = await AutomationAPI.getAutomationData();
  } catch {
    data.value = null;
  } finally {
    loading.value = false;
  }
}
onMounted(load);

/** 今天是周几（1=周一 … 7=周日） */
const todayWd = new Date().getDay() === 0 ? 7 : new Date().getDay();

/** 任务是否在"今天"会跑（weekdays 为空 = 每天都算） */
function runsToday(t: AutomationTask) {
  if (!t.times?.length) return false;
  if (dayMode.value === "all") return true;
  return !t.weekdays?.length || t.weekdays.includes(todayWd);
}

const baseTasks = computed(() =>
  (data.value?.tasks ?? []).filter((t) => {
    if (showMode.value === "all") return true;
    if (showMode.value === "browser") return !!t.browserWindow;
    return t.enabled;
  })
);

const todayTasks = computed(() => baseTasks.value.filter(runsToday));

const conflicts = computed(() => data.value?.conflicts ?? []);
const busySlots = computed(() => data.value?.busySlots ?? []);
const tight = computed(() => data.value?.tight ?? []);

/** 占用紫鸟窗口的任务（这些才会互相抢） */
const browserTasks = computed(() =>
  (data.value?.tasks ?? []).filter((t) => t.browserWindow)
);

/** 平时就会重叠的（典型口径） */
const typicalTight = computed(() => tight.value.filter((x) => x.severity === "typical"));

/** 典型耗时里的最大值，用于文案「平时都只要 N 分钟内」 */
const maxTypical = computed(() =>
  Math.max(1, ...browserTasks.value.map((t) => t.typicalMinutes ?? 0))
);

/** 按「哪个任务卡住」归组，避免同一个任务刷出一堆行 */
const tightGroups = computed(() => {
  const map = new Map<string, {
    a: string; aTime: string; aTimeout: number; aTypical: number;
    weekdays: number[]; severity: string;
    affects: typeof tight.value;
  }>();
  for (const x of tight.value) {
    const k = `${x.a}@${x.aTime}`;
    if (!map.has(k)) {
      map.set(k, {
        a: x.a, aTime: x.aTime, aTimeout: x.aTimeout, aTypical: x.aTypical,
        weekdays: [...x.weekdays], severity: x.severity, affects: [],
      });
    }
    const g = map.get(k)!;
    g.affects.push(x);
    for (const w of x.weekdays) if (!g.weekdays.includes(w)) g.weekdays.push(w);
    if (x.severity === "typical") g.severity = "typical";
  }
  const out = [...map.values()];
  out.forEach((g) => g.weekdays.sort());
  // 典型优先，其次看最坏重叠多大
  out.sort((p, q) => {
    if (p.severity !== q.severity) return p.severity === "typical" ? -1 : 1;
    const mp = Math.max(...p.affects.map((z) => z.worstOverlap));
    const mq = Math.max(...q.affects.map((z) => z.worstOverlap));
    return mq - mp;
  });
  return out;
});

/** 任务名 → 中文名 */
function labelOf(name: string) {
  const t = (data.value?.tasks ?? []).find((x) => x.name === name);
  return t?.label || name;
}

const WD = ["一", "二", "三", "四", "五", "六", "日"];
function wdText(d: number) {
  return WD[d - 1] ?? String(d);
}

/** 时间轴上的点：每个 (任务 × 时刻) 一个。
 *
 * 密集时段（如早高峰 07:30~08:50 有 7 个任务）会挤在一起，所以：
 *   ① 分配「车道」错开圆点（同车道内间隔 ≥ LANE_MIN 分钟）
 *   ② 标签按间距**贪心**显示 —— 挤不下就不显示文字（靠 hover 提示 + 下方清单看细节）
 */
const LANE_MIN = 12; // 同车道最小间隔（分钟）
const LABEL_MIN = 28; // 标签之间最小间隔（分钟）

const marks = computed(() => {
  type M = {
    key: string; time: string; minutes: number; pct: number;
    status: TaskStatus; conflict: boolean; title: string; task: AutomationTask;
    lane: number; showLabel: boolean;
  };
  const conflictTimes = new Set(conflicts.value.map((c) => c.time));
  const raw: M[] = [];
  for (const t of todayTasks.value) {
    for (const hhmm of t.times) {
      const [h, m] = hhmm.split(":").map(Number);
      if (Number.isNaN(h) || Number.isNaN(m)) continue;
      const minutes = h * 60 + m;
      raw.push({
        key: `${t.name}-${hhmm}`,
        time: hhmm,
        minutes,
        pct: (minutes / 1440) * 100,
        status: t.status,
        conflict: conflictTimes.has(hhmm),
        title: `${hhmm}  ${t.label || t.name}  [${statusText(t.status)}]  ${t.rule}`,
        task: t,
        lane: 0,
        showLabel: true,
      });
    }
  }
  raw.sort((a, b) => a.minutes - b.minutes);

  // ① 车道分配
  const laneLast: number[] = [];
  for (const m of raw) {
    let lane = laneLast.findIndex((last) => m.minutes - last >= LANE_MIN);
    if (lane === -1) {
      lane = laneLast.length;
      laneLast.push(-9999);
    }
    laneLast[lane] = m.minutes;
    m.lane = lane;
  }

  // ② 同车道内标签贪心显示
  const byLane = new Map<number, M[]>();
  for (const m of raw) {
    if (!byLane.has(m.lane)) byLane.set(m.lane, []);
    byLane.get(m.lane)!.push(m);
  }
  for (const arr of byLane.values()) {
    let lastShown = -9999;
    for (const m of arr) {
      if (m.minutes - lastShown >= LABEL_MIN) {
        m.showLabel = true;
        lastShown = m.minutes;
      } else {
        m.showLabel = false;
      }
    }
  }
  return raw;
});

/** 车道数 → 时间轴高度（自适应） */
const laneCount = computed(() => Math.max(1, ...marks.value.map((m) => m.lane + 1)));
const marksHeight = computed(() => 26 + laneCount.value * 24);

/** 按时刻分组（同一时刻多任务合并成一行） */
const grouped = computed(() => {
  const map = new Map<string, AutomationTask[]>();
  for (const t of todayTasks.value) {
    for (const hhmm of t.times) {
      if (!map.has(hhmm)) map.set(hhmm, []);
      map.get(hhmm)!.push(t);
    }
  }
  return [...map.entries()]
    .map(([time, items]) => ({ time, items }))
    .sort((a, b) => a.time.localeCompare(b.time));
});

function hourBusy(h: number) {
  const pad = String(h).padStart(2, "0");
  return busySlots.value.some((s) => s.startsWith(pad + ":"));
}

/** 空闲时刻：06:00~23:00 内没被占用的整点/半点 */
const freeSlots = computed(() => {
  const busy = new Set(busySlots.value);
  const out: string[] = [];
  for (let h = 6; h <= 23; h++) {
    for (const m of [0, 30]) {
      const s = `${String(h).padStart(2, "0")}:${String(m).padStart(2, "0")}`;
      if (!busy.has(s)) out.push(s);
    }
  }
  // 只提示相对稀少的时段：避开繁忙的整点 ±10 分钟
  return out.filter((s) => {
    const [hh, mm] = s.split(":").map(Number);
    const mins = hh * 60 + mm;
    return !busySlots.value.some((b) => {
      const [bh, bm] = b.split(":").map(Number);
      return Math.abs(bh * 60 + bm - mins) <= 10;
    });
  });
});

function shortName(n: string) {
  // 面板里已有中文名，这里只做超长截断
  return n.length > 14 ? n.slice(0, 13) + "…" : n;
}

function tagType(st: TaskStatus) {
  return (
    {
      ok: "success",
      fail: "danger",
      alert: "warning",
      warn: "warning",
      never: "info",
      running: "primary",
      disabled: "info",
      waiting: "info",
    } as const
  )[st];
}

function statusText(st: TaskStatus) {
  return (
    {
      ok: "成功",
      fail: "失败",
      alert: "告警",
      warn: "部分失败",
      never: "未执行",
      running: "运行中",
      disabled: "已禁用",
      waiting: "等待中",
    } as const
  )[st];
}

function fmt(v: string | null) {
  if (!v) return "—";
  const d = new Date(v);
  if (Number.isNaN(d.getTime())) return String(v).slice(0, 16).replace("T", " ");
  const p = (n: number) => String(n).padStart(2, "0");
  return `${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}`;
}

/** 点任务点 → 展示详情（含日志路径提示） */
async function openDetail(m: { task: AutomationTask }) {
  const t = m.task;
  await ElMessageBox.alert(
    `<div style="line-height:1.9;font-size:13px">
      <b>${t.label || t.name}</b><br/>
      <span style="color:#888">${t.name}</span><br/><br/>
      <b>触发规则：</b>${t.rule}<br/>
      <b>状态：</b>${statusText(t.status)}${t.rc !== null ? `（rc=${t.rc}）` : ""}<br/>
      <b>上次执行：</b>${fmt(t.lastRun)}<br/>
      <b>下次执行：</b>${t.nextRun ? fmt(t.nextRun) : "—"}<br/>
      <b>原因：</b>${t.reason || "—"}<br/>
      <b>来源：</b>${t.source}
    </div>`,
    "任务详情",
    { dangerouslyUseHTMLString: true, confirmButtonText: "知道了" }
  ).catch(() => {});
}
</script>

<style scoped lang="scss">
.tl {
  padding: 16px;
}

.card {
  padding: 16px;
  margin-bottom: 14px;
  background: var(--el-bg-color);
  border: 1px solid var(--el-border-color-lighter);
  border-radius: 8px;
}

.tl-head {
  display: flex;
  flex-wrap: wrap;
  gap: 12px;
  align-items: flex-start;
  justify-content: space-between;
  margin-bottom: 12px;
}

.tl-title {
  margin: 0;
  font-size: 17px;
  font-weight: 600;
}

.tl-sub {
  margin: 4px 0 0;
  font-size: 12px;
  color: var(--el-text-color-secondary);
}

.tl-head__right {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  align-items: center;
}

.tl-hint {
  font-size: 12px;
  color: var(--el-text-color-placeholder);
}

.tl-alert {
  margin-bottom: 14px;

  &__body {
    display: flex;
    flex-wrap: wrap;
    gap: 6px;
    margin-top: 6px;
  }
}

.tl-chip {
  display: inline-flex;
  gap: 5px;
  align-items: center;
  padding: 2px 8px;
  font-size: 12px;
  background: var(--el-fill-color-light);
  border-radius: 4px;

  &__dim {
    color: var(--el-text-color-secondary);
  }

  &--warn {
    background: var(--el-color-warning-light-9);
    color: var(--el-color-warning-dark-2);
  }

  &--bad {
    background: var(--el-color-danger-light-9);
    color: var(--el-color-danger-dark-2);
  }
}

/* ---------------- 时间轴 ---------------- */
.tl-axis {
  position: relative;
}

.tl-axis__track {
  display: flex;
  padding-bottom: 4px;
  border-bottom: 1px solid var(--el-border-color-lighter);
}

.tl-hour {
  position: relative;
  flex: 1;
  height: 22px;
  text-align: center;

  &__label {
    font-size: 10px;
    color: var(--el-text-color-placeholder);
  }

  &--busy {
    background: var(--el-color-primary-light-9);
  }
}

.tl-marks {
  position: relative;
  margin-top: 6px;
}

.tl-mark {
  position: absolute;
  display: flex;
  flex-direction: column;
  align-items: center;
  height: 22px;
  cursor: pointer;
  transform: translateX(-50%);

  &__dot {
    flex: 0 0 auto;
    width: 10px;
    height: 10px;
    border-radius: 50%;
    background: var(--el-color-success);
    transition: transform 0.12s;
  }

  &__label {
    margin-top: 2px;
    font-size: 10px;
    line-height: 1.1;
    color: var(--el-text-color-secondary);
    white-space: nowrap;
  }

  &--fail &__dot {
    background: var(--el-color-danger);
  }
  &--alert &__dot {
    background: var(--el-color-warning);
  }
  &--warn &__dot {
    background: var(--el-color-warning);
  }
  &--disabled &__dot {
    background: var(--el-color-info-light-5);
  }
  &--waiting &__dot,
  &--never &__dot {
    background: var(--el-color-info);
  }
  &--running &__dot {
    background: var(--el-color-primary);
  }

  &--conflict &__dot {
    box-shadow: 0 0 0 3px var(--el-color-warning-light-5);
  }

  &:hover &__dot {
    transform: scale(1.35);
  }
}

/* ---------------- 清单 ---------------- */
.tl-list__head,
.tl-row {
  display: flex;
  gap: 10px;
  align-items: flex-start;
  padding: 8px 0;
}

.tl-list__head {
  font-size: 12px;
  font-weight: 600;
  color: var(--el-text-color-secondary);
  border-bottom: 1px solid var(--el-border-color-lighter);
}

.tl-row {
  border-bottom: 1px dashed var(--el-border-color-lighter);

  &--conflict {
    background: var(--el-color-warning-light-9);
  }
}

.tl-col {
  flex: 0 0 auto;

  &--time {
    width: 82px;
  }
  &--task {
    flex: 1;
    min-width: 190px;
  }
  &--status {
    width: 84px;
  }
  &--last {
    width: 92px;
  }
  &--rule {
    width: 190px;
  }
}

.tl-time {
  font-family: ui-monospace, Consolas, monospace;
  font-size: 14px;
}

.tl-task {
  &__name {
    font-size: 13px;
    font-weight: 600;
  }

  &__win {
    margin-left: 6px;
  }

  &__sub {
    display: block;
    font-family: ui-monospace, Consolas, monospace;
    font-size: 11px;
    color: var(--el-text-color-placeholder);
  }
}

/* ---------------- 时段重叠风险 ---------------- */
.tl-tight {
  &__title {
    margin: 0 0 10px;
    font-size: 14px;
  }

  &__sub {
    margin-left: 8px;
    font-size: 12px;
    font-weight: 400;
    color: var(--el-text-color-secondary);
  }

  &__row {
    padding: 8px 0;
    border-bottom: 1px dashed var(--el-border-color-lighter);

    &:last-child {
      border-bottom: none;
    }
  }

  &__head {
    display: flex;
    flex-wrap: wrap;
    gap: 8px;
    align-items: center;
  }

  &__meta {
    font-size: 12px;
    color: var(--el-text-color-secondary);
  }

  &__affects {
    margin: 6px 0 0 18px;
    font-size: 12px;
    line-height: 1.9;
    list-style: disc;
  }

  &__dim {
    color: var(--el-text-color-secondary);
  }
}

.mono {
  font-family: ui-monospace, Consolas, monospace;
  font-size: 12px;
  color: var(--el-text-color-secondary);
}

/* ---------------- 空闲时刻 ---------------- */
.tl-free {
  &__title {
    margin: 0 0 8px;
    font-size: 14px;
  }

  &__body {
    display: flex;
    flex-wrap: wrap;
    gap: 6px;
  }

  &__none {
    font-size: 12px;
    color: var(--el-text-color-secondary);
  }

  &__hint {
    margin: 10px 0 0;
    font-size: 12px;
    color: var(--el-text-color-secondary);
  }
}
</style>
