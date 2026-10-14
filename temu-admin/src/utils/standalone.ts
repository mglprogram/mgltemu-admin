import { STORAGE_KEYS } from "@/constants";
import { Storage } from "@/utils/storage";

/**
 * 单机（离线）模式
 *
 * @description
 * 本项目（TEMU 运营后台）只吃构建期导出的静态 JSON，没有后端服务，
 * 因此不需要登录、也不需要后端下发菜单路由。
 *
 * 开启方式：在 .env.production / .env.development 里设 `VITE_STANDALONE=true`。
 * 开启后：
 *   1. 路由守卫直接放行，不再要求 access token；
 *   2. 权限 store 直接标记「路由已生成」，不请求 `/menus/routes`；
 *   3. 菜单只渲染 src/router/index.ts 里的静态路由。
 *
 * 日后接了真实后端，把开关关掉即可回到完整的登录 + 动态菜单流程。
 */

/** 令牌占位值：仅用于让守卫认为「已登录」，不会发起任何真实请求 */
const STUB_TOKEN = "standalone";

/**
 * 是否处于单机模式
 */
export function isStandalone(): boolean {
  return import.meta.env.VITE_STANDALONE === "true";
}

/**
 * 单机模式下写入占位令牌（幂等）
 */
export function ensureStandaloneSession(): void {
  if (!isStandalone()) return;
  if (!Storage.sessionGet(STORAGE_KEYS.ACCESS_TOKEN)) {
    Storage.sessionSet(STORAGE_KEYS.ACCESS_TOKEN, STUB_TOKEN);
  }
}