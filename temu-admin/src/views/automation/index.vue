<template>
  <div class="auto">
    <!-- 顶部汇总 -->
    <section class="auto-head">
      <div class="stat" :class="{ 'stat--bad': s.fail }">
        <div class="stat__num">{{ s.fail }}</div>
        <div class="stat__label">执行失败</div>
      </div>
      <div class="stat" :class="{ 'stat--alert': s.alert }">
        <div class="stat__num">{{ s.alert }}</div>
        <div class="stat__label">业务告警</div>
      </div>
      <div class="stat" :class="{ 'stat--warn': s.never }">
        <div class="stat__num">{{ s.never }}</div>
        <div class="stat__label">未执行</div>
      </div>
      <div class="stat" :class="{ 'stat--run': s.running }">
        <div class="stat__num">{{ s.running }}</div>
        <div class="stat__label">运行中</div>
      </div>
      <div class="stat stat--ok">
        <div class="stat__num">{{ s.ok }}</div>
        <div class="stat__label">成功</div>
      </div>
      <div class="stat">
        <div class="stat__num">{{ s.disabled }}</div>
        <div class="stat__label">已禁用</div>
      </div>
      <div class="stat">
        <div class="stat__num">{{ s.total }}</div>
        <div class="stat__label">全部任务</div>
      </div>
      <div class="auto-head__meta">
        <div class="auto-head__gen">数据生成于 {{ data?.generatedAt || "-" }}</div>
        <el-button size="small" :loading="loading" @click="load">刷新</el-button>
      </div>
    </section>

    <!-- 筛选 -->
    <section class="auto-filter">
      <el-radio-group v-model="filter" size="small">
        <el-radio-button value="all">全部 {{ s.total }}</el-radio-button>
        <el-radio-button value="problem">
          需关注 {{ s.fail + s.alert + s.warn + s.never }}
        </el-radio-button>
        <el-radio-button value="fail">失败 {{ s.fail }}</el-radio-button>
        <el-radio-button value="alert">告警 {{ s.alert }}</el-radio-button>
        <el-radio-button value="never">未执行 {{ s.never }}</el-radio-button>
        <el-radio-button value="disabled">已禁用 {{ s.disabled }}</el-radio-button>
        <el-radio-button value="ok">成功 {{ s.ok }}</el-radio-button>
      </el-radio-group>
      <el-input
        v-model="kw"
        size="small"
        placeholder="搜索任务名 / 说明 / 命令"
        clearable
        class="auto-filter__kw"
      />
    </section>

    <!-- 任务表 -->
    <section class="auto-body" v-loading="loading">
      <el-table :data="rows" size="small" border stripe row-key="name" style="width: 100%">
        <el-table-column label="状态" width="96">
          <template #default="{ row }">
            <el-tag :type="tagType(row.status)" size="small" effect="dark">
              {{ statusText(row.status) }}
            </el-tag>
          </template>
        </el-table-column>

        <el-table-column label="任务" min-width="230">
          <template #default="{ row }">
            <div class="cell-name">{{ row.label || row.name }}</div>
            <div v-if="row.desc" class="cell-desc">{{ row.desc }}</div>
            <div class="cell-sub">
              <el-tag size="small" effect="plain" type="info">{{ row.source }}</el-tag>
              <span v-if="row.label && row.label !== row.name" class="cell-raw">{{ row.name }}</span>
              <span v-if="row.origin" class="cell-origin">来自：{{ row.origin }}</span>
            </div>
          </template>
        </el-table-column>

        <el-table-column label="触发规则" width="150">
          <template #default="{ row }">
            <span class="mono">{{ row.rule }}</span>
          </template>
        </el-table-column>

        <el-table-column label="上次执行" width="150">
          <template #default="{ row }">
            <span class="mono">{{ fmt(row.lastRun) }}</span>
          </template>
        </el-table-column>

        <el-table-column label="下次执行" width="150">
          <template #default="{ row }">
            <span class="mono">{{ fmt(row.nextRun) }}</span>
          </template>
        </el-table-column>

        <el-table-column label="原因 / 说明" min-width="280">
          <template #default="{ row }">
            <span :class="{ 'reason--bad': row.status === 'fail', 'reason--warn': row.status === 'never' }">
              {{ row.reason }}
            </span>
            <div v-if="row.note" class="cell-note">{{ row.note }}</div>
          </template>
        </el-table-column>

        <el-table-column label="执行命令" min-width="220">
          <template #default="{ row }">
            <span class="mono cell-cmd" :title="row.action">{{ row.action || "-" }}</span>
          </template>
        </el-table-column>
      </el-table>

      <el-empty v-if="!loading && !rows.length" description="没有匹配的任务" />
    </section>

    <section class="auto-foot">
      <p>
        数据来源：<template v-for="(s, i) in activeSources" :key="s.name"
          ><span v-if="i > 0"> + </span><b>{{ s.name }}</b
          >（{{ s.desc }}）</template
        >
      </p>
      <p class="auto-foot__dim">
        返回码 267014 / 0xC000013A 表示「提醒窗口还开着被中止」，属于提醒类任务的正常现象，不是故障。
      </p>
    </section>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from "vue";
import AutomationAPI, {
  type AutomationData,
  type AutomationTask,
  type TaskStatus,
} from "@/api/automation";

defineOptions({ name: "Automation" });

const loading = ref(false);
const data = ref<AutomationData | null>(null);
const filter = ref<"all" | "problem" | "fail" | "alert" | "never" | "disabled" | "ok">("all");
const kw = ref("");

const s = computed(
  () =>
    data.value?.summary ?? {
      total: 0,
      ok: 0,
      fail: 0,
      alert: 0,
      warn: 0,
      never: 0,
      running: 0,
      disabled: 0,
      waiting: 0,
      problem: 0,
    }
);

/** 需关注的状态：故障 + 告警 + 部分失败 + 未执行 */
const PROBLEM: TaskStatus[] = ["fail", "alert", "warn", "never"];

const rows = computed<AutomationTask[]>(() => {
  const all = data.value?.tasks ?? [];
  const byStatus = all.filter((t) => {
    if (filter.value === "all") return true;
    if (filter.value === "problem") return PROBLEM.includes(t.status);
    return t.status === filter.value;
  });
  const k = kw.value.trim().toLowerCase();
  if (!k) return byStatus;
  return byStatus.filter(
    (t) =>
      t.name.toLowerCase().includes(k) ||
      (t.label || "").toLowerCase().includes(k) ||
      (t.desc || "").toLowerCase().includes(k) ||
      (t.action || "").toLowerCase().includes(k) ||
      (t.origin || "").toLowerCase().includes(k)
  );
});

/** 页脚只列出实际有任务的来源（DSH 可能为 0） */
const activeSources = computed(() => (data.value?.sources ?? []).filter((s) => s.count > 0));

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

/** ISO -> yyyy-MM-dd HH:mm；空值显示 — */
function fmt(v: string | null) {
  if (!v) return "—";
  const d = new Date(v);
  if (Number.isNaN(d.getTime())) return v.slice(0, 16).replace("T", " ");
  const p = (n: number) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}`;
}
</script>

<style scoped lang="scss">
.auto {
  padding: 16px;
}

/* ---------------- 汇总 ---------------- */
.auto-head {
  display: flex;
  flex-wrap: wrap;
  gap: 12px;
  align-items: stretch;
  margin-bottom: 14px;
}

.stat {
  flex: 0 0 108px;
  padding: 12px 14px;
  background: var(--el-bg-color);
  border: 1px solid var(--el-border-color-lighter);
  border-radius: 8px;

  &__num {
    font-size: 24px;
    font-weight: 700;
    line-height: 1.2;
    color: var(--el-text-color-primary);
  }

  &__label {
    margin-top: 2px;
    font-size: 12px;
    color: var(--el-text-color-secondary);
  }

  &--bad &__num {
    color: var(--el-color-danger);
  }
  &--alert &__num {
    color: var(--el-color-warning);
  }
  &--warn &__num {
    color: var(--el-color-warning);
  }
  &--run &__num {
    color: var(--el-color-primary);
  }
  &--ok &__num {
    color: var(--el-color-success);
  }

  &--bad {
    border-color: var(--el-color-danger-light-5);
  }
  &--alert {
    border-color: var(--el-color-warning-light-5);
  }
  &--warn {
    border-color: var(--el-color-warning-light-5);
  }
}

.auto-head__meta {
  display: flex;
  flex: 1;
  flex-direction: column;
  gap: 6px;
  align-items: flex-end;
  justify-content: center;
  min-width: 180px;
}

.auto-head__gen {
  font-size: 12px;
  color: var(--el-text-color-secondary);
}

/* ---------------- 筛选 ---------------- */
.auto-filter {
  display: flex;
  flex-wrap: wrap;
  gap: 10px;
  align-items: center;
  margin-bottom: 12px;
}

.auto-filter__kw {
  width: 220px;
  margin-left: auto;
}

/* ---------------- 表格 ---------------- */
.auto-body {
  min-height: 200px;
}

.cell-name {
  font-weight: 600;
  color: var(--el-text-color-primary);
}

.cell-desc {
  margin-top: 2px;
  font-size: 12px;
  color: var(--el-text-color-regular);
}

.cell-raw {
  font-family: ui-monospace, Consolas, monospace;
  font-size: 11px;
  color: var(--el-text-color-placeholder);
}

.cell-sub {
  display: flex;
  gap: 8px;
  align-items: center;
  margin-top: 3px;
  font-size: 12px;
  color: var(--el-text-color-secondary);
}

.cell-origin {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.cell-note,
.cell-sub {
  font-size: 12px;
  color: var(--el-text-color-secondary);
}

.cell-note {
  margin-top: 3px;
}

.mono {
  font-family: ui-monospace, "Cascadia Mono", Consolas, monospace;
  font-size: 12px;
}

.cell-cmd {
  display: block;
  overflow: hidden;
  color: var(--el-text-color-secondary);
  text-overflow: ellipsis;
  white-space: nowrap;
}

.reason--bad {
  color: var(--el-color-danger);
}

.reason--warn {
  color: var(--el-color-warning-dark-2);
}

/* ---------------- 底注 ---------------- */
.auto-foot {
  margin-top: 14px;
  font-size: 12px;
  line-height: 1.7;
  color: var(--el-text-color-secondary);

  p {
    margin: 0;
  }

  &__dim {
    color: var(--el-text-color-placeholder);
  }
}
</style>
