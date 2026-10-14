/**
 * 美国多时区时钟（数据大屏右上角）
 *
 * 用户 2026-10-14 要求：右上角时间从「北京时间」改成美国时间，按出单州分时区显示。
 *
 * 只保留 **4 个时区** —— 依据是真实的订单分布数据
 * （public/data/order-geo.json，2026-01-01 ~ 10-14 共 31,651 单）：
 *
 *   时区              覆盖的出单州           单量     占比
 *   美东 ET          FL NY PA GA NC NJ      9,298    29.4%
 *   美中 CT          TX IL                  3,523    11.1%
 *   美西 PT          CA                     3,010     9.5%
 *   大西洋 AST（PR） PR                     2,674     8.4%
 *   —— Top10 出单州全部落在这 4 个里（合计 58.5%），
 *      阿拉斯加仅 114 单、夏威夷更低，故不做，避免占满头部空间。
 *
 * ⚠ 为什么必须用 IANA 时区名 + Intl，而不是自己算偏移量：
 *   ① 美国实行夏令时（3 月第 2 个周日 ~ 11 月第 1 个周日），偏移量一年内会变；
 *   ② **波多黎各不实行夏令时**（全年 AST = UTC-4）——
 *      夏令时期间它与美东同为 UTC-4（时间相同），
 *      11/1 之后美东变 UTC-5，波多黎各仍是 UTC-4 ⇒ **PR 比美东快 1 小时**。
 *      写死偏移量必然算错这一个月；交给 Intl 自动处理才稳。
 *   ③ 中国不实行夏令时 ⇒「北京 ↔ 美东」时差在 12h/13h 之间切换，同理不能写死。
 */

import { ref } from "vue";

/** 一个时区档 */
export interface UsClockZone {
  /** 唯一键 */
  key: string;
  /** 界面上的短标签 */
  label: string;
  /** IANA 时区名（**不能**改成偏移量，见文件头注释） */
  tz: string;
  /** 该时区覆盖的主要出单州（悬浮提示用） */
  states: string;
}

/** 一个时区档 + 当前实时值 */
export interface UsClock extends UsClockZone {
  /** HH:mm:ss */
  time: string;
  /** 当地小时（0–23），用于判断活跃时段 */
  hour: number;
  /** 是否处于当地活跃时段 */
  active: boolean;
  /** 悬浮提示 */
  hint: string;
}

/**
 * 业务相关的 4 个美国时区。
 *
 * 顺序按单量从多到少（美东 → 美中 → 美西 → 波多黎各），
 * 与「订单分布」面板的州排名一致，便于横向对照。
 */
export const US_ZONES: UsClockZone[] = [
  { key: "et", label: "美东", tz: "America/New_York", states: "FL NY PA GA NC NJ" },
  { key: "ct", label: "美中", tz: "America/Chicago", states: "TX IL" },
  { key: "pt", label: "美西", tz: "America/Los_Angeles", states: "CA" },
  { key: "ast", label: "波多黎各", tz: "America/Puerto_Rico", states: "PR" },
];

/** 活跃时段（当地时间）：[8:00, 22:00) 记为活跃 */
const ACTIVE_FROM = 8;
const ACTIVE_TO = 22;

/** Intl.DateTimeFormat 构造较贵，按时区缓存复用 */
const fmtCache = new Map<string, Intl.DateTimeFormat>();
/** 北京时间（Asia/Shanghai）——显式指定，不依赖本机时区设置 */
const BJ = "Asia/Shanghai";
const WD_EN = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"];
const WD_ZH = ["日", "一", "二", "三", "四", "五", "六"];

function formatter(tz: string): Intl.DateTimeFormat {
  let f = fmtCache.get(tz);
  if (!f) {
    f = new Intl.DateTimeFormat("en-US", {
      timeZone: tz,
      hour: "2-digit",
      minute: "2-digit",
      second: "2-digit",
      // h23 ⇒ 午夜是 00 而不是 24
      hourCycle: "h23",
    });
    fmtCache.set(tz, f);
  }
  return f;
}

/** 取某时区在给定时刻的 时/分/秒 */
function zoneParts(tz: string, d: Date): { hour: number; minute: string; second: string } {
  const out: Record<string, string> = {};
  for (const p of formatter(tz).formatToParts(d)) {
    if (p.type !== "literal") out[p.type] = p.value;
  }
  return {
    hour: Number(out.hour ?? 0),
    minute: out.minute ?? "00",
    second: out.second ?? "00",
  };
}

/** 年月日+星期的格式化器（同样按时区缓存） */
const dateFmtCache = new Map<string, Intl.DateTimeFormat>();

function dateFormatter(tz: string): Intl.DateTimeFormat {
  let f = dateFmtCache.get(tz);
  if (!f) {
    f = new Intl.DateTimeFormat("en-US", {
      timeZone: tz,
      year: "numeric",
      month: "2-digit",
      day: "2-digit",
      weekday: "short",
    });
    dateFmtCache.set(tz, f);
  }
  return f;
}

const pad = (n: number) => String(n).padStart(2, "0");

/**
 * 美国四时区时钟 + 北京时间（对照）。
 *
 * 用法：在组件里 `const { clocks, beijing, tick } = useUsClocks()`，
 * 挂载时先 `tick()` 一次（useKeepTicking 只起定时器、不会立即回调），
 * 再用 `useKeepTicking(tick, 1000)` 每秒刷新。
 */
export function useUsClocks() {
  const mk = (z: UsClockZone): UsClock => ({
    ...z,
    time: "--:--:--",
    hour: 0,
    active: false,
    hint: "",
  });

  const clocks = ref<UsClock[]>(US_ZONES.map(mk));
  /** 北京时间的完整显示串（YYYY-MM-DD HH:mm:ss 周dd） */
  const beijing = ref("--");

  function tick() {
    const now = new Date();

    clocks.value = US_ZONES.map((z) => {
      const { hour, minute } = zoneParts(z.tz, now);
      // 只显示 HH:mm：4 个时钟都带秒会太吵，且头部横向空间紧张
      // （北京那行仍带秒，负责"在动"的观感）
      const time = `${pad(hour)}:${minute}`;
      const active = hour >= ACTIVE_FROM && hour < ACTIVE_TO;
      return {
        ...z,
        time,
        hour,
        active,
        hint:
          `${z.label}（${z.tz}）\n` +
          `覆盖出单州：${z.states}\n` +
          `当地 ${time} · ${active ? "活跃时段" : "休息时段"}（${ACTIVE_FROM}:00–${ACTIVE_TO}:00 记活跃）`,
      };
    });

    // 北京时间：同样显式带时区，保证在任何机器上都显示北京（而不是本机时区）
    const bj = zoneParts(BJ, now);
    const dp: Record<string, string> = {};
    for (const p of dateFormatter(BJ).formatToParts(now)) {
      if (p.type !== "literal") dp[p.type] = p.value;
    }
    const wd = WD_ZH[WD_EN.indexOf(dp.weekday ?? "")] ?? "?";
    beijing.value =
      `${dp.year}-${dp.month}-${dp.day} ` +
      `${pad(bj.hour)}:${bj.minute}:${bj.second} 周${wd}`;
  }

  return { clocks, beijing, tick };
}
