/**
 * 自动化任务接口
 *
 * 与 limited / flux-boost 同源：构建期由 Python 导出静态 JSON
 * （public/data/automation.json），前端直接 fetch，不走后端。
 */
import type { AutomationData } from "./types";

const DATA_BASE = `${import.meta.env.BASE_URL}data`;

async function fetchJson<T>(name: string): Promise<T> {
  const res = await fetch(`${DATA_BASE}/${name}`, { cache: "no-cache" });
  if (!res.ok) {
    throw new Error(`加载 ${name} 失败：HTTP ${res.status}`);
  }
  return (await res.json()) as T;
}

const AutomationAPI = {
  /** 全部定时任务（Windows 计划任务 + DSH 会话提醒） */
  getAutomationData() {
    return fetchJson<AutomationData>("automation.json");
  },
};

export default AutomationAPI;

// 重导出类型
export * from "./types";
