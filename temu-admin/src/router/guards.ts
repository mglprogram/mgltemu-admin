import type { RouteLocationNormalized, RouteRecordRaw } from "vue-router";
import NProgress from "@/plugins/nprogress";
import router from "@/router";
import { usePermissionStore, useUserStore } from "@/stores";
import { useTenantStoreHook } from "@/stores/tenant";
import { isStandalone } from "@/utils/standalone";
import { isTenantEnabled } from "@/utils/tenant";

/** 兜底标题（没匹配到任何 meta.title 时用），与 index.html 的 %VITE_APP_TITLE% 同源 */
const FALLBACK_TITLE = (import.meta.env.VITE_APP_TITLE as string) || "TEMU 运营后台";

/**
 * 把浏览器标签页标题改成当前页面的名字。
 *
 * 用户 2026-11-04 反馈：切到「限流监控」等多个页面，标签页一直写着「TEMU 运营后台」，
 * 开几个标签就分不清谁是谁。原来全项目**没有任何地方**设置 document.title，
 * 标题只来自 index.html 的静态 <title>。
 *
 * 取 `to.matched` 里最后一层有 title 的记录（= 当前页面本身，
 * 不是「TEMU 运营」这种分组壳），这样「TEMU 运营 / 限流监控」→「限流监控」。
 */
function setDocumentTitle(to: RouteLocationNormalized): void {
  const titles = to.matched
    .map((record) => (record.meta?.title as string) || "")
    .filter(Boolean);
  // 去掉相邻重复（壳层与子页同名时只留一个）
  const uniq = titles.filter((t, i) => i === 0 || t !== titles[i - 1]);
  const leaf = uniq[uniq.length - 1] || "";
  document.title = leaf || FALLBACK_TITLE;
}

/**
 * 路由守卫
 *
 * 处理登录验证、动态路由生成、404 检测、页面标题与进度条
 */
export function setupPermissionGuard() {
  // 白名单支持前缀匹配：/f 命中所有公开表单分享页 /f/:formKey
  const whiteList = ["/login", "/f"];

  router.beforeEach(async (to, _from) => {
    NProgress.start();

    try {
      // 单机模式：纯静态数据展示，跳过登录校验与后端菜单。
      // 注意：必须仍然把静态路由灌进 permission store —— 侧边栏/顶部菜单读的是
      // store.routes，而不是 router 的路由表；直接 return 会让菜单整片空白。
      if (isStandalone()) {
        const permissionStore = usePermissionStore();
        if (!permissionStore.isRouteGenerated) {
          await permissionStore.generateRoutes();
        }
        return;
      }

      const isLoggedIn = useUserStore().isLoggedIn();

      // 未登录处理
      if (!isLoggedIn) {
        const isWhiteListed = whiteList.some(
          (path) => to.path === path || to.path.startsWith(`${path}/`)
        );
        if (isWhiteListed) {
          return;
        }
        NProgress.done();
        return `/login?redirect=${encodeURIComponent(to.fullPath)}`;
      }

      // 已登录访问登录页，重定向到首页
      if (to.path === "/login") {
        return { path: "/" };
      }

      const permissionStore = usePermissionStore();
      const userStore = useUserStore();

      // 动态路由生成
      if (!permissionStore.isRouteGenerated) {
        if (!userStore.userInfo?.roles?.length) {
          await userStore.getUserInfo();
        }

        // 加载用户租户列表（VITE_TENANT_ENABLED=true 时生效）
        await initTenantContext();

        const dynamicRoutes = await permissionStore.generateRoutes();
        dynamicRoutes.forEach((route: RouteRecordRaw) => {
          router.addRoute(route);
        });

        return { ...to, replace: true };
      }

      // 路由 404 检查
      if (to.matched.length === 0) {
        // 从登录页跳转且目标路径无效，回退首页（避免不同用户权限不同导致的 404）
        if (_from.path === "/login") {
          return { path: "/", replace: true };
        }
        return "/404";
      }

      // 动态标题
      const title = (to.params.title as string) || (to.query.title as string);
      if (title) {
        to.meta.title = title;
      }
    } catch (error) {
      console.error("Route guard error:", error);
      await useUserStore().resetAllState();
      NProgress.done();
      return "/login";
    }
  });

  router.afterEach((to) => {
    NProgress.done();
    setDocumentTitle(to);
  });
}

/**
 * 初始化多租户上下文，未启用或失败时静默跳过
 */
async function initTenantContext(): Promise<void> {
  if (!isTenantEnabled()) return;

  try {
    await useTenantStoreHook().loadTenant();
  } catch {
    // 静默失败，不影响主流程
  }
}
