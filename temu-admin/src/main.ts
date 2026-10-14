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
