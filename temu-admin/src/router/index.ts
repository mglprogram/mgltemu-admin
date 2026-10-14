import type { App } from "vue";
import { createRouter, createWebHashHistory, type RouteRecordRaw } from "vue-router";

/**
 * 布局组件，供各路由复用（懒加载）
 */
export const Layout = () => import("@/layouts/index.vue");

/**
 * 布局外大屏路由：不套后台 Layout，整页铺满视口。
 *
 * ⚠ 2026-10-14 路径调整（用户要求）：**大屏就是首页**，占 `/dashboard`。
 *   原 `/dashboard` 的白底卡片页（views/dashboard）挪到 `/console`。
 *   大屏**必须**是顶层 `/dashboard`（而不是 Layout 下的子路由），
 *   否则会套上后台框架，看不到整屏效果。
 */
export const screenRoutes: RouteRecordRaw[] = [
  {
    path: "/dashboard",
    name: "Dashboard",
    component: () => import("@/views/data-screen/index.vue"),
    meta: { hidden: true, title: "数据大屏" },
  },
  {
    // 旧地址兜底：历史收藏/分享的 /data-screen 仍能打开大屏
    path: "/data-screen",
    name: "DataScreen",
    redirect: "/dashboard",
    meta: { hidden: true, title: "数据大屏" },
  },
];

// 静态路由
export const constantRoutes: RouteRecordRaw[] = [
  {
    path: "/redirect",
    component: Layout,
    meta: { hidden: true },
    children: [
      {
        path: "/redirect/:path(.*)",
        component: () => import("@/views/redirect.vue"),
      },
    ],
  },

  {
    path: "/login",
    component: () => import("@/views/login/index.vue"),
    meta: { hidden: true },
  },

  // 公开表单分享页（匿名访问，守卫白名单，不套管理端 Layout）
  {
    path: "/f/:formKey",
    name: "FormShare",
    component: () => import("@/views/dynamic-form/share.vue"),
    meta: { hidden: true, title: "表单填写" },
  },

  // 大屏演示页（独立成页，不经后台框架）
  ...screenRoutes,

  {
    path: "/",
    name: "/",
    component: Layout,
    // 登录后落地：大屏（/dashboard 是顶级路由，不套 Layout，见 screenRoutes）
    redirect: "/dashboard",
    children: [
      {
        // 白底运营控制台（原 /dashboard，2026-10-14 让位给大屏后挪到这里）
        path: "console",
        component: () => import("@/views/dashboard/index.vue"),
        // 用于 keep-alive 功能，需要与 SFC 中自动推导或显式声明的组件名称一致
        // 参考文档: https://cn.vuejs.org/guide/built-ins/keep-alive.html#include-exclude
        name: "Console",
        meta: {
          title: "运营总览",
          icon: "homepage",
          affix: true,
          keepAlive: true,
        },
      },
      {
        /**
         * 数据大屏（侧边栏入口）——**新标签页打开**，不是本框架内的页面。
         *
         * 为什么用外链形态：大屏是整屏铺满的独立页面（见上方 screenRoutes 的
         * 顶级 /dashboard），若作为 Layout 子路由会被套上侧边栏+标签页，
         * 整屏效果就没了。所以这里只放一个"入口"。
         *
         * ⚠ 必须把**绝对地址**写在 path 上，不能写成 meta.externalUrl：
         *   侧边栏（LayoutSidebarItem / LayoutSidebar）判外链只看 path
         *   —— `isExternal(routePath)` 命中才用 <a target="_blank">；
         *   meta.externalUrl 只在 permission store 处理**后端菜单**时才会被
         *   折成 path（buildChildRoutes → toExternalRoute），静态路由不经过那一步。
         */
        path: `${window.location.origin}${window.location.pathname}#/dashboard`,
        name: "ScreenEntry",
        // 纯侧边栏外链占位：自身不是真实路由（点击由 <a target="_blank"> 接管），
        // 但 vue-router 的类型要求无 component 的记录必须给 redirect
        redirect: "/dashboard",
        meta: {
          title: "数据大屏",
          icon: "el-icon-Monitor",
        },
      },
      {
        path: "401",
        component: () => import("@/views/error/401.vue"),
        meta: { hidden: true },
      },
      {
        path: "404",
        component: () => import("@/views/error/404.vue"),
        meta: { hidden: true },
      },
      {
        path: "profile",
        name: "Profile",
        component: () => import("@/views/profile/index.vue"),
        meta: { title: "个人中心", icon: "user", hidden: true },
      },
      {
        path: "profile/notice",
        name: "MyNotice",
        component: () => import("@/views/profile/notice/index.vue"),
        meta: { title: "我的通知", icon: "user", hidden: true },
      },
      {
        path: "/detail/:id(\\d+)",
        name: "DemoDetail",
        component: () => import("@/views/demo/route/detail.vue"),
        meta: { title: "详情页缓存", icon: "user", hidden: true, keepAlive: true },
      },
      {
        path: "/route-example/edit/:id(\\d+)",
        name: "RouteExampleEdit",
        component: () => import("@/views/demo/route/navigate/edit.vue"),
        meta: { title: "跳转编辑页", icon: "user", hidden: true },
      },
    ],
  },

  // TEMU 运营模块：顶层套 Layout，与后端动态菜单的路由形态保持一致，
  // 这样日后接了后端菜单也能平滑迁移
  {
    path: "/temu",
    name: "/temu",
    component: Layout,
    redirect: "/temu/limited",
    meta: { title: "TEMU 运营", icon: "el-icon-Shop" },
    children: [
      {
        path: "limited",
        component: () => import("@/views/temu/limited/index.vue"),
        name: "Limited",
        meta: { title: "限流监控", icon: "el-icon-Warning", keepAlive: true },
      },
      {
        path: "flux-boost",
        component: () => import("@/views/temu/flux-boost/index.vue"),
        name: "FluxBoost",
        meta: { title: "流量加速", icon: "el-icon-Promotion", keepAlive: true },
      },
      {
        path: "presale",
        component: () => import("@/views/temu/presale/index.vue"),
        name: "Presale",
        meta: { title: "销量-库存", icon: "el-icon-SoldOut", keepAlive: true },
      },
      {
        path: "on-sale",
        component: () => import("@/views/temu/goods/index.vue"),
        name: "TemuGoodsOnSale",
        meta: { title: "在售款明细", icon: "el-icon-Goods", keepAlive: true },
      },
    ],
  },

  // 自动化任务：全部定时任务的统一视图（Windows 计划任务 + WorkBuddy + DSH）
  {
    path: "/automation",
    name: "Automation",
    component: Layout,
    redirect: "/automation/timeline",
    meta: { title: "自动化任务", icon: "el-icon-AlarmClock" },
    children: [
      {
        path: "timeline",
        component: () => import("@/views/automation/timeline.vue"),
        name: "AutomationTimeline",
        meta: { title: "时间轴", icon: "el-icon-Time", keepAlive: true },
      },
      {
        path: "index",
        component: () => import("@/views/automation/index.vue"),
        name: "AutomationIndex",
        meta: { title: "任务列表", icon: "el-icon-List", keepAlive: true },
      },
    ],
  },
];

/**
 * 创建路由
 */
const router = createRouter({
  history: createWebHashHistory(),
  routes: constantRoutes,
  // 刷新时，滚动条位置还原
  scrollBehavior: () => ({ left: 0, top: 0 }),
});

/**
 * 全局注册 router
 */
export function setupRouter(app: App<Element>) {
  app.use(router);
}

export default router;
