/** 自动化任务类型定义 */

/** 任务状态 */
export type TaskStatus =
  | "ok"
  | "fail"
  /** 业务告警：脚本正常，但业务上有待处理项（如重点款被限流） */
  | "alert"
  /** 部分失败：结果可用但不完整 */
  | "warn"
  | "never"
  | "running"
  | "disabled"
  | "waiting";

/** 单条定时任务 */
export interface AutomationTask {
  /** 任务名（系统原名，如 DailyReminder0900） */
  name: string;
  /** 中文名称 */
  label: string;
  /** 一句话说明这个任务干什么 */
  desc: string;
  /** 来源：Windows 计划任务 / WorkBuddy / DSH */
  source: "Windows" | "WorkBuddy" | "DSH";
  /** 触发规则的人话描述 */
  rule: string;
  /** 时区 */
  timezone: string;
  /** 是否启用 */
  enabled: boolean;
  /** 上次执行时间（ISO） */
  lastRun: string | null;
  /** 下次执行时间（ISO） */
  nextRun: string | null;
  /** 原始返回码 */
  rc: number | null;
  /** 归一化状态 */
  status: TaskStatus;
  /** 失败 / 未执行的原因（人话） */
  reason: string;
  /** 实际执行的命令 */
  action: string;
  /** DSH 任务的会话 id */
  session: string | null;
  /** 创建该任务的会话标题（来自历史日志） */
  origin: string;
  /** 补充说明 */
  note: string;
}

export interface AutomationSummary {
  total: number;
  ok: number;
  fail: number;
  /** 业务告警数（脚本正常但有待处理项） */
  alert: number;
  /** 部分失败数 */
  warn: number;
  never: number;
  running: number;
  disabled: number;
  waiting: number;
  /** 需要关注的总数（失败 + 未执行 + 告警 + 部分失败） */
  problem: number;
}

export interface AutomationSource {
  name: string;
  desc: string;
  count: number;
}

export interface AutomationData {
  generatedAt: string;
  summary: AutomationSummary;
  sources: AutomationSource[];
  tasks: AutomationTask[];
}
