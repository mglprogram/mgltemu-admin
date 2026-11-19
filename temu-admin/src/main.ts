import { createApp } from "vue";
import App from "./App.vue";

import "element-plus/theme-chalk/dark/css-vars.css";
import "@/styles/index.scss";
import "uno.css";
import "animate.css";

import { setupDirective } from "@/directives";
import { setupRouter } from "@/router";
import { setupStore } from "@/stores";
import { setupI18n } from "@/lang";
import { setupFormCreate } from "@/plugins/form-create";
import * as ElementPlusIcons from "@element-plus/icons-vue";
import { setupPermissionGuard } from "@/router/guards";
import { isStandalone } from "@/utils/standalone";
import { useSse } from "@/utils/sse";
import { useDictStoreHook } from "@/stores/dict";
import { useUserStoreHook } from "@/stores/user";
import { ensureStandaloneSession } from "@/utils/standalone";

/**
 * 懒加载分包拉取失败时自动整页刷新。
 *
 * 为什么需要（2026-10-15 实际事故）：
 *   本站一天会发布十几次，每次构建都会换掉所有 chunk 的哈希。
 *   如果浏览器里**开着一个旧标签页**（它用的是旧那份 index.html），
 *   再去点一个「按需加载」的菜单，就会去拉那份已经不存在的旧 chunk →
 *   动态 import 失败 → 路由组件渲染不出来 → **整页黑屏**（只有顶部一条进度条）。
 *   用户看到的就是"这个系统进不去了"，其实服务端完全正常。
 *
 * Vite 在动态 import 失败时会派发 `vite:preloadError`（自定义事件）。
 * 这里接住它并 reload 一次：刷新会重新取回最新的 index.html 与 chunk，
 * 页面即自愈。用 sessionStorage 打一个短时标记，避免万一 chunk 真的缺失时
 * 陷入"刷新 → 又失败 → 又刷新"的死循环。
 */
const RELOAD_KEY = "vite_preload_reloaded_at";

/**
 * 记录"应用真的挂载成功了"。
 *
 * ★ 2026-11-19 加（用户报：开代理后页面强制刷新就再也打不开，只能干等）。
 *   根因：index.html 里只放了门禁脚本，**入口 bundle 被代理抖动/Cloudflare
 *   掐断时页面就是一个静态 HTML** —— 门禁移除后 #app 是空的，没有 JS
 *   能派发 `vite:preloadError`（那时 vite 的运行时还没加载），于是既没有
 *   报错也没有任何提示，用户看到的就是"一片空白/一直加载"。
 *   publish_admin.py 注入的兜底脚本（index.html 里的 #boot）靠这个标记
 *   判断"app 到底起来了没"，起不来就自动重试取 JS，而不是死等。
 */
try {
  (window as unknown as Record<string, unknown>).__TEMU_BOOTED__ = true;
} catch {
  /* 忽略 */
}

/**
 * 记录「本次文档是不是按刷新打开的」以及「刷新时停在哪条路由上」。
 *
 * ★ 2026-11-19 加（用户报：从库存跟踪带 `?kw=` 跳到头程跟踪，**强制刷新后关键词不清**）。
 *   口径：刷新 = 重新开始，应回到不带关键词的全量视图。
 *
 * 必须在这里（真正的文档加载时）记，不能等到页面组件懒加载 chunk 时再读
 * `performance.getEntriesByType("navigation")` —— 那时用户可能已经在 reload 出来的
 * 文档里点了几次路由，读到的仍然是 "reload"，会把「刷新后从库存跟踪点进来」
 * 也误判成「刷新残留」而清掉关键词。
 */
try {
  const nav = performance.getEntriesByType("navigation")[0] as
    | PerformanceNavigationTiming
    | undefined;
  const w = window as unknown as Record<string, unknown>;
  w.__TEMU_NAV_TYPE__ = nav?.type || "navigate";
  w.__TEMU_BOOT_HASH__ = String(window.location.hash || "");
} catch {
  /* 老浏览器拿不到 Navigation Timing：按首次打开处理 */
}

window.addEventListener("vite:preloadError", () => {
  let last = 0;
  try {
    last = Number(sessionStorage.getItem(RELOAD_KEY) || 0);
  } catch {
    /* 隐私模式下 sessionStorage 可能不可用 */
  }
  // 60 秒内只自动刷新一次，避免死循环
  if (Date.now() - last < 60_000) {
    console.error("[preload] 分包加载失败，且刚刚已自动刷新过，放弃重试");
    return;
  }
  try {
    sessionStorage.setItem(RELOAD_KEY, String(Date.now()));
  } catch {
    /* 忽略 */
  }
  console.warn("[preload] 分包加载失败（多半是页面版本过期），自动刷新取回最新版本");
  window.location.reload();
});

const app = createApp(App);


setupDirective(app);
setupI18n(app);
setupRouter(app);
setupStore(app);
setupFormCreate(app);

Object.entries(ElementPlusIcons).forEach(([name, comp]) => app.component(name, comp));

// 单机模式：写入占位令牌并填充本地用户信息，让顶栏/头像有内容可渲染
if (isStandalone()) {
  ensureStandaloneSession();
  useUserStoreHook().getUserInfo();
}

setupPermissionGuard();

// 建立 SSE 连接并开启字典实时同步（单机模式无后端，跳过以免无谓重连与报错）
if (!isStandalone()) {
  useSse().connect();
  useDictStoreHook().setupDictSync();
}

app.mount("#app");

// 兜底脚本（index.html 里的 #boot）等到这里就收工，不再重试取 JS
try {
  document.dispatchEvent(new Event("temu:app-mounted"));
} catch {
  /* 忽略 */
}
