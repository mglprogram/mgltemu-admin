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
