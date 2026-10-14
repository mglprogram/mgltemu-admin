<template>
  <div class="panel-box flex-1 flex flex-col min-h-0">
    <div class="panel-title-bar">
      <div class="title-left">
        <span class="indicator-bar orange" />
        <span class="title-text">自动化任务与定时巡检</span>
      </div>
      <div class="title-right">
        <span class="status-summary">
          正常: <b class="text-[#67c23a] font-din">{{ metrics.autoSummary.ok }}</b> /
          {{ metrics.autoSummary.total }}
        </span>
        <button
          type="button"
          class="more-link"
          @click="router.push('/automation/index')"
        >
          任务中心 →
        </button>
      </div>
    </div>

    <!-- 任务运行状态流 -->
    <div class="task-stream flex-1 overflow-y-auto">
      <div
        v-for="task in automationTasks.slice(0, 5)"
        :key="task.name"
        class="task-row"
      >
        <div class="status-indicator">
          <span
            class="dot"
            :class="getTaskStatusClass(task.status)"
          />
        </div>

        <div class="task-main">
          <div class="task-head">
            <span class="task-name">{{ task.label || task.name }}</span>
            <span class="source-tag">{{ task.source }}</span>
          </div>
          <div class="task-meta">
            <span class="rule-text">{{ task.rule || '定时自动触发' }}</span>
            <span v-if="task.lastRun" class="time-text font-din">
              前次: {{ formatTime(task.lastRun) }}
            </span>
          </div>
        </div>

        <div class="task-badge">
          <span class="status-pill" :class="getTaskStatusClass(task.status)">
            {{ getStatusText(task.status) }}
          </span>
        </div>
      </div>
    </div>
  </div>
</template>

<script lang="ts" setup>
import { useRouter } from "vue-router";
import { useDataScreenData } from "../composables/useDataScreenData";

defineOptions({ name: "AutomationTaskPanel" });

const router = useRouter();
const { automationTasks, metrics } = useDataScreenData();

function getTaskStatusClass(status: string) {
  if (status === "ok") return "is-ok";
  if (status === "alert") return "is-alert";
  if (status === "fail") return "is-fail";
  if (status === "warn") return "is-warn";
  return "is-waiting";
}

function getStatusText(status: string) {
  const map: Record<string, string> = {
    ok: "正常",
    alert: "告警",
    fail: "失败",
    warn: "注意",
    running: "运行中",
    disabled: "停用",
    waiting: "就绪",
  };
  return map[status] || status;
}

function formatTime(str: string | null) {
  if (!str) return "--";
  if (str.length >= 16) return str.slice(11, 16);
  return str;
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
  padding-bottom: 6px;
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
  background: #e6a23c;
  box-shadow: 0 0 8px #e6a23c;
  border-radius: 2px;
}

.title-text {
  font-size: 13px;
  font-weight: 600;
  color: #e6a23c;
  letter-spacing: 0.5px;
}

.title-right {
  display: flex;
  align-items: center;
  gap: 8px;
}

.status-summary {
  font-size: 10px;
  color: #8fb2dc;
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

.task-stream {
  display: flex;
  flex-direction: column;
  gap: 6px;
  max-height: 180px;

  &::-webkit-scrollbar {
    width: 3px;
  }
  &::-webkit-scrollbar-thumb {
    background: rgba(230, 162, 60, 0.25);
    border-radius: 2px;
  }
}

.task-row {
  display: flex;
  align-items: center;
  gap: 8px;
  background: rgba(14, 32, 68, 0.5);
  border: 1px solid rgba(46, 96, 153, 0.25);
  border-radius: 4px;
  padding: 5px 8px;
  font-size: 11px;
}

.status-indicator {
  display: flex;
  align-items: center;
}

.dot {
  width: 6px;
  height: 6px;
  border-radius: 50%;

  &.is-ok {
    background: #67c23a;
    box-shadow: 0 0 6px #67c23a;
  }
  &.is-alert {
    background: #e6a23c;
    box-shadow: 0 0 6px #e6a23c;
  }
  &.is-fail {
    background: #ff5252;
    box-shadow: 0 0 6px #ff5252;
  }
  &.is-waiting {
    background: #8fb2dc;
  }
}

.task-main {
  flex: 1;
  min-width: 0;
}

.task-head {
  display: flex;
  align-items: center;
  gap: 5px;
  margin-bottom: 2px;
}

.task-name {
  font-size: 11px;
  font-weight: 600;
  color: #ffffff;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.source-tag {
  font-size: 8px;
  padding: 0 3px;
  border-radius: 2px;
  background: rgba(0, 234, 255, 0.12);
  color: #00eaff;
}

.task-meta {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 9px;
  color: #6b8cb5;
}

.rule-text {
  color: #8fb2dc;
}

.status-pill {
  font-size: 9px;
  padding: 1px 5px;
  border-radius: 10px;

  &.is-ok {
    background: rgba(103, 194, 58, 0.15);
    color: #67c23a;
    border: 1px solid rgba(103, 194, 58, 0.3);
  }
  &.is-alert {
    background: rgba(230, 162, 60, 0.15);
    color: #e6a23c;
    border: 1px solid rgba(230, 162, 60, 0.3);
  }
  &.is-fail {
    background: rgba(255, 82, 82, 0.15);
    color: #ff5252;
    border: 1px solid rgba(255, 82, 82, 0.3);
  }
  &.is-waiting {
    background: rgba(143, 178, 220, 0.15);
    color: #8fb2dc;
    border: 1px solid rgba(143, 178, 220, 0.3);
  }
}
</style>
