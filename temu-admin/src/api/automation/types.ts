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

/** 任务分类：面板按它分组，「归谁管」的口径 */
export type TaskCategory =
  | "temu-store"
  | "panel"
  | "ops"
  | "pw"
  | "reminder"
  | "other";

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
  /** 分类 id（temu-store / panel / ops / pw / reminder / other） */
  category?: TaskCategory;
  /** 分类中文名 */
  categoryLabel?: string;
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
  /** 触发时刻列表，如 ["08:00","12:30"]（时间轴/冲突检测用） */
  times: string[];
  /** 适用星期（1=周一 … 7=周日）；空数组 = 每天都算 */
  weekdays: number[];
  /** 占用的紫鸟店铺窗口："BHY+BEL" / "BEL" / ""（空=不占窗口） */
  browserWindow?: string;
  /** 「不占窗口」是否已人工核实过（注册表里显式写了空串） */
  windowVerified?: boolean;
  /** 超时上限（分钟）—— 算「卡住时」的最坏占用 */
  timeoutMinutes?: number | null;
  /** 实测典型耗时（分钟）—— 算「平时会不会撞」 */
  typicalMinutes?: number | null;
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
  /** 有冲突的时刻数（同一时刻 ≥2 个**占窗口**任务，且店铺有交集） */
  conflictSlots?: number;
  /** 有「时段重叠风险」的配对数 */
  tightSlots?: number;
  /** 真正的抢窗口任务数（注册表声明占紫鸟窗口的） */
  browserTasks?: number;
}

/** 同一时刻多个**占窗口**任务 → 真会抢同一个紫鸟店铺窗口 */
export interface AutomationConflict {
  /** 时刻 HH:MM */
  time: string;
  /** 该时刻的任务数 */
  count: number;
  /** 涉及的任务名 */
  tasks: string[];
  /** 星期都重叠的部分（1=周一 … 7=周日） */
  weekdays: number[];
  /** 风险提示 */
  risk: string;
  /** 具体是哪两两相撞、撞在哪个店 */
  pairs?: { a: string; b: string; stores: string[] }[];
}

/**
 * 时段重叠风险：前一个任务如果卡住（跑到超时上限），会压到后一个。
 *
 * 分两个口径，别混淆：
 *   * typicalOverlap > 0 → 平时就会撞，必须错开
 *   * 只有 worstOverlap > 0 → 仅当上一个卡住才会发生（典型耗时很短）
 */
export interface AutomationTight {
  a: string;
  b: string;
  aTime: string;
  bTime: string;
  /** 两次触发相隔多少分钟 */
  gapMinutes: number;
  /** 按超时上限算的重叠分钟 */
  worstOverlap: number;
  /** 按实测典型耗时算的重叠分钟（0 = 平时不撞） */
  typicalOverlap: number;
  aTimeout: number;
  aTypical: number;
  weekdays: number[];
  stores: string[];
  severity: "typical" | "timeout-only";
  risk: string;
}

export interface AutomationSource {
  name: string;
  desc: string;
  count: number;
}

/** 一个分类的汇总（面板分组标题上显示的数字） */
export interface AutomationCategory {
  id: TaskCategory;
  label: string;
  /** 这个分类是干什么的 */
  desc: string;
  count: number;
  ok: number;
  fail: number;
  alert: number;
  warn: number;
  never: number;
  running: number;
  waiting: number;
  disabled: number;
  /** 需关注 = fail + alert + warn + never */
  problem: number;
}

export interface AutomationData {
  generatedAt: string;
  summary: AutomationSummary;
  sources: AutomationSource[];
  /** 分类汇总（按展示顺序，空分类不出现） */
  categories?: AutomationCategory[];
  tasks: AutomationTask[];
  /** 冲突时刻（同一时刻 ≥2 个占窗口任务） */
  conflicts?: AutomationConflict[];
  /** 时段重叠风险（前一个卡住会压到后一个） */
  tight?: AutomationTight[];
  /** 被占用的时刻（含单任务），用于算空闲时段 */
  busySlots?: string[];
}
