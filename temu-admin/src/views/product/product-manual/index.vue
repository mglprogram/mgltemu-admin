<!--
  产品手册（产品-库存 → 产品手册）

  用户需求（2026-11-06）：在面板「产品-库存」下加一个「产品手册」栏目，
  内容是 D:\together\产品手册.xlsx 里的产品资料（款号 / 中文名称 / 报关编码 /
  做货工厂 / 实物图 / 尺码表 / 重量表 / 洗水唛），加上款号命名规则。

  数据：public/data/product-manual.json
       + public/manual-img/<ID>.webp（从 xlsx 的 WPS 内嵌图抽出来转的）
  生成：scripts/goods/build_product_manual.py（xlsx 改了就重跑一次再发布）
        scripts/goods/encrypt_product_costs.mjs（成本明文改了重跑）

  ⚠ 成本（出厂价）是 2026-11-07 用户明确要求「只有我能看到」的敏感数据：
     线上只放 public/data/product-costs.json 这个 AES-256-GCM 密文，
     明文与口令只存在本机（scripts/goods/_local/，gitignore），绝不入库。
     页面上要输「成本口令」才会用浏览器 WebCrypto 本地解密并渲染两列成本，
     没解锁时金额一个字都不在页面 / 网络里。product-manual.json 本身不含任何价格。
-->
<template>
  <div class="pm-page app-container">
    <div class="pm-header">
      <div>
        <h2 class="pm-header__title">产品手册</h2>
        <p class="pm-header__desc">
          {{ data.products.length }} 个款号 · 尺码表 / 重量表 / 洗水唛 / 实物图，点任意一行看大图。
        </p>
      </div>
      <div class="pm-header__meta">
        <el-tag v-if="data.sourceMtime" type="info" effect="plain" size="small">
          源表更新 {{ data.sourceMtime }}
        </el-tag>
        <el-radio-group v-model="view" size="small">
          <el-radio-button value="table">列表</el-radio-button>
          <el-radio-button value="grid">图卡</el-radio-button>
        </el-radio-group>
        <el-button :loading="loading" @click="load">
          <el-icon><Refresh /></el-icon><span>刷新</span>
        </el-button>
      </div>
    </div>

    <!-- 成本解锁：金额只存在密文里，输对口令才在浏览器本地解密 -->
    <el-alert v-if="!unlocked" type="warning" :closable="false" class="pm-cost-gate" show-icon>
      <template #title>
        <div class="pm-cost-gate__row">
          <span>成本列已加密（黑白 / 彩色两价）。输入成本口令解锁查看：</span>
          <el-input
            v-model="costPwd"
            type="password"
            placeholder="成本口令"
            size="small"
            style="width: 160px"
            show-password
            @keyup.enter="unlockCost"
          />
          <el-button size="small" type="primary" :loading="unlocking" @click="unlockCost">解锁成本</el-button>
          <span v-if="costError" class="pm-cost-gate__err">{{ costError }}</span>
        </div>
      </template>
    </el-alert>
    <el-alert v-else type="success" :closable="false" class="pm-cost-gate" show-icon>
      <template #title>
        <div class="pm-cost-gate__row">
          <span>成本已解锁 · {{ Object.keys(costs).length }} 个款号有成本</span>
          <el-button size="small" text @click="lockCost">隐藏 / 锁定成本</el-button>
        </div>
      </template>
    </el-alert>

    <!-- 筛选 -->
    <el-card shadow="never" class="pm-card">
      <div class="pm-tools">
        <el-input v-model="keyword" placeholder="搜款号 / 中文名称 / 工厂" clearable size="small" style="width: 240px">
          <template #prefix><el-icon><Search /></el-icon></template>
        </el-input>
        <el-select v-model="factory" placeholder="做货工厂" clearable size="small" style="width: 130px">
          <el-option v-for="f in factories" :key="f" :label="f" :value="f" />
        </el-select>
        <el-checkbox v-model="withImages" size="small">只看有尺码表的</el-checkbox>
        <span class="pm-dim">共 {{ rows.length }} 条</span>
      </div>
    </el-card>

    <!-- 列表视图 -->
    <el-card v-if="view === 'table'" shadow="never" class="pm-card">
      <el-table :data="rows" size="default" stripe @row-click="open">
        <el-table-column label="图片" width="72">
          <template #default="{ row }">
            <el-image
              v-if="row.img"
              :src="row.img"
              fit="cover"
              class="pm-thumb"
              :preview-src-list="[...row.sizeImgs, ...row.compImgs, ...row.weightImgs, row.img].filter(Boolean)"
              preview-teleported
              @click.stop
            />
            <div v-else class="pm-thumb pm-thumb--empty"><el-icon><Picture /></el-icon></div>
          </template>
        </el-table-column>
        <el-table-column prop="style" label="款号" width="110" fixed />
        <el-table-column prop="name" label="中文名称" min-width="240" show-overflow-tooltip />
        <el-table-column prop="factory" label="做货工厂" width="100" align="center" />
        <el-table-column prop="hs" label="报关编码" width="120" align="center" />
        <el-table-column label="成本(黑白)" width="105" align="right" sortable :sort-method="costSort('bw')">
          <template #default="{ row }">
            <span v-if="!unlocked" class="pm-cost-locked">🔒</span>
            <span v-else-if="costOf(row, 'bw') !== null" class="pm-cost">{{ costOf(row, "bw") }}</span>
            <span v-else class="pm-dim">—</span>
          </template>
        </el-table-column>
        <el-table-column label="成本(彩色)" width="105" align="right" sortable :sort-method="costSort('color')">
          <template #default="{ row }">
            <span v-if="!unlocked" class="pm-cost-locked">🔒</span>
            <span v-else-if="costOf(row, 'color') !== null" class="pm-cost">{{ costOf(row, "color") }}</span>
            <span v-else class="pm-dim">—</span>
          </template>
        </el-table-column>
        <el-table-column prop="weight" label="产品克重" min-width="150" show-overflow-tooltip>
          <template #default="{ row }">
            <span v-if="row.weight">{{ row.weight }}</span>
            <el-tag v-else-if="row.weightImgs.length" size="small" effect="plain">重量表</el-tag>
            <span v-else class="pm-dim">—</span>
          </template>
        </el-table-column>
        <el-table-column prop="careLabel" label="洗水唛成分（后台显示）" min-width="200" show-overflow-tooltip>
          <template #default="{ row }">
            <span v-if="row.careLabel">{{ row.careLabel }}</span>
            <el-tag v-else-if="row.compImgs.length" size="small" effect="plain">见图片</el-tag>
            <span v-else class="pm-dim">—</span>
          </template>
        </el-table-column>
        <el-table-column label="尺码表" width="90" align="center">
          <template #default="{ row }">
            <el-tag v-if="row.sizeImgs.length" size="small" effect="plain">查看</el-tag>
            <span v-else class="pm-dim">无</span>
          </template>
        </el-table-column>
      </el-table>
      <el-empty v-if="!rows.length && !loading" description="没有匹配的款号" :image-size="70" />
    </el-card>

    <!-- 图卡视图 -->
    <div v-else class="pm-grid">
      <el-card v-for="row in rows" :key="row.row" shadow="hover" :body-style="{ padding: '0' }" class="pm-tile" @click="open(row)">
        <el-image v-if="row.img" :src="row.img" fit="cover" class="pm-tile__pic" lazy />
        <div v-else class="pm-tile__pic pm-tile__pic--empty"><el-icon><Picture /></el-icon></div>
        <div class="pm-tile__body">
          <div class="pm-tile__style">{{ row.style }}</div>
          <div class="pm-tile__name" :title="row.name">{{ row.name }}</div>
          <div class="pm-tile__foot">
            <el-tag size="small" type="info" effect="plain">{{ row.factory || "—" }}</el-tag>
            <span class="pm-dim">{{ row.hs }}</span>
          </div>
        </div>
      </el-card>
    </div>

    <!-- 款号命名规则 -->
    <el-card shadow="never" class="pm-card">
      <template #header><span class="pm-card__title">款号命名规则</span></template>
      <el-table :data="ruleRows" size="small" border class="pm-rules">
        <el-table-column
          v-for="(col, i) in ruleCols"
          :key="i"
          :prop="String(i)"
          :label="col"
          align="center"
          min-width="90"
        />
      </el-table>
    </el-card>

    <!-- 详情抽屉 -->
    <el-drawer v-model="detail" :title="current?.style ? `${current.style} · ${current.name}` : '产品详情'" size="620px">
      <template v-if="current">
        <div class="pm-detail__meta">
          <span><b>款号</b> {{ current.style || "—" }}</span>
          <span><b>做货工厂</b> {{ current.factory || "—" }}</span>
          <span><b>报关编码</b> {{ current.hs || "—" }}</span>
        </div>
        <div v-if="unlocked" class="pm-detail__meta">
          <span><b>成本(黑白)</b> {{ costOf(current, "bw") ?? "—" }}</span>
          <span><b>成本(彩色)</b> {{ costOf(current, "color") ?? "—" }}</span>
        </div>
        <div v-if="current.weight" class="pm-detail__meta"><span><b>产品克重</b> {{ current.weight }}</span></div>
        <div v-if="current.careLabel" class="pm-detail__meta"><span><b>洗水唛成分</b> {{ current.careLabel }}</span></div>

        <template v-for="sec in sections" :key="sec.title">
          <h4 class="pm-detail__h">{{ sec.title }}</h4>
          <div v-for="(src, i) in sec.srcs" :key="src + i" class="pm-detail__img">
            <el-image :src="src" fit="contain" :preview-src-list="allImages" preview-teleported :initial-index="allImages.indexOf(src)" />
            <el-link :href="src" target="_blank" type="primary" :underline="false" class="pm-detail__open">原图</el-link>
          </div>
        </template>
        <el-empty v-if="!allImages.length" description="这个款号在源表里没有图片" :image-size="60" />
      </template>
    </el-drawer>
  </div>
</template>

<script setup lang="ts">
  import ProductManualAPI from "@/api/temu/product-manual";
  import type { ManualProduct, ProductManualData } from "@/api/temu/product-manual";
  import {
    fetchCostPayload,
    decryptCosts,
    type ProductCostPayload,
    type ProductCosts,
  } from "@/api/temu/product-manual/cost";

  defineOptions({ name: "TemuProductManual" });

  const loading = ref(false);
  const view = ref<"table" | "grid">("table");
  const keyword = ref("");
  const factory = ref("");
  const withImages = ref(false);
  const detail = ref(false);
  const current = ref<ManualProduct | null>(null);

  // ---- 成本（加密，仅口令正确时解密）----
  const costPayload = ref<ProductCostPayload | null>(null);
  const costPwd = ref("");
  const costError = ref("");
  const unlocking = ref(false);
  const costs = ref<ProductCosts>({});
  const unlocked = ref(false);

  const EMPTY: ProductManualData = {
    generatedAt: "",
    source: "",
    sourceMtime: "",
    withPrice: false,
    fields: [],
    products: [],
    rules: [],
  };
  const data = ref<ProductManualData>(EMPTY);

  const factories = computed(() =>
    [...new Set(data.value.products.map((p) => p.factory).filter(Boolean))].sort(),
  );

  const rows = computed(() => {
    const kw = keyword.value.trim().toLowerCase();
    return data.value.products.filter((p) => {
      if (factory.value && p.factory !== factory.value) return false;
      if (withImages.value && !p.sizeImgs.length) return false;
      if (!kw) return true;
      return (
        p.style.toLowerCase().includes(kw) ||
        p.name.toLowerCase().includes(kw) ||
        p.factory.toLowerCase().includes(kw)
      );
    });
  });

  /** 款号规则：第 2 行是分栏标题，第 1 行是表名，剩下是取值 */
  const ruleHeader = computed(() => data.value.rules[1] || []);
  const ruleCols = computed(() => {
    // 标题行形如 [第一个数字, '', 第二、三个数字, '', ...]：空位是上一栏的展开列
    const h = ruleHeader.value;
    const out: string[] = [];
    let last = "";
    h.forEach((v) => {
      if (v) last = v;
      out.push(last);
    });
    return out;
  });
  const ruleRows = computed(() => {
    const recs = data.value.rules.slice(2).map((line, idx) => {
      const rec: Record<string, string> = { _i: String(idx) };
      ruleCols.value.forEach((_, i) => (rec[String(i)] = line[i] || ""));
      return rec;
    });
    return recs.filter((r) => Object.keys(r).some((k) => k !== "_i" && r[k]));
  });

  const allImages = computed(() => {
    const c = current.value;
    if (!c) return [] as string[];
    return [...c.sizeImgs, ...c.compImgs, ...c.weightImgs, ...(c.img ? [c.img] : [])];
  });

  const sections = computed(() => {
    const c = current.value;
    if (!c) return [] as { title: string; srcs: string[] }[];
    return [
      { title: "尺码表 / 定做重量表", srcs: c.sizeImgs },
      { title: "洗水唛", srcs: c.compImgs },
      { title: "产品克重", srcs: c.weightImgs },
      { title: "实物图", srcs: c.img ? [c.img] : [] },
    ].filter((s) => s.srcs.length);
  });

  function open(row: ManualProduct) {
    current.value = row;
    detail.value = true;
  }

  /** 取某款号某颜色的成本；未解锁 / 没有该款号 → null */
  function costOf(row: ManualProduct, kind: "bw" | "color"): number | null {
    if (!unlocked.value) return null;
    const c = costs.value[String(row.style)];
    const v = c?.[kind];
    return typeof v === "number" ? v : null;
  }

  /** el-table 的排序比较函数：空值排最后 */
  function costSort(kind: "bw" | "color") {
    return (a: ManualProduct, b: ManualProduct) => {
      const va = costOf(a, kind) ?? Number.POSITIVE_INFINITY;
      const vb = costOf(b, kind) ?? Number.POSITIVE_INFINITY;
      return va - vb;
    };
  }

  async function unlockCost() {
    costError.value = "";
    if (!costPwd.value.trim()) {
      costError.value = "请输入成本口令";
      return;
    }
    unlocking.value = true;
    try {
      if (!costPayload.value) costPayload.value = await fetchCostPayload();
      costs.value = await decryptCosts(costPayload.value, costPwd.value);
      unlocked.value = true;
      costPwd.value = "";
    } catch {
      costError.value = "口令不对，或成本数据已损坏";
      costs.value = {};
      unlocked.value = false;
    } finally {
      unlocking.value = false;
    }
  }

  function lockCost() {
    unlocked.value = false;
    costs.value = {};
    costPwd.value = "";
    costError.value = "";
  }

  async function load() {
    loading.value = true;
    try {
      data.value = await ProductManualAPI.getProductManual();
      // 顺手把成本密文拉下来（只有密文，没有金额明文）
      costPayload.value = await fetchCostPayload().catch(() => null);
    } catch (e: any) {
      ElMessage.error(e?.message || "加载产品手册失败");
    } finally {
      loading.value = false;
    }
  }

  onMounted(load);
</script>

<style scoped lang="scss">
  .pm-page { padding-bottom: 24px; }

  .pm-header {
    display: flex;
    align-items: flex-start;
    justify-content: space-between;
    gap: 16px;
    margin-bottom: 14px;

    &__title { margin: 0 0 6px; font-size: 19px; font-weight: 600; }
    &__desc { margin: 0; font-size: 13px; color: var(--el-text-color-secondary); line-height: 1.6; }
    &__meta { display: flex; align-items: center; gap: 8px; flex-shrink: 0; }
  }

  .pm-card {
    margin-bottom: 14px;

    &__title { font-size: 14px; font-weight: 600; }
  }

  .pm-tools {
    display: flex;
    align-items: center;
    gap: 10px;
    flex-wrap: wrap;
  }

  .pm-cost-gate {
    margin-bottom: 14px;

    &__row {
      display: flex;
      align-items: center;
      gap: 10px;
      flex-wrap: wrap;
      font-size: 13px;
    }

    &__err { color: var(--el-color-danger); }
  }

  .pm-cost {
    font-weight: 600;
    color: var(--el-color-danger);
    font-variant-numeric: tabular-nums;
  }

  .pm-cost-locked {
    color: var(--el-text-color-placeholder);
  }

  .pm-thumb {
    width: 44px;
    height: 44px;
    border-radius: 4px;
    background: var(--el-fill-color-light);

    &--empty {
      display: flex;
      align-items: center;
      justify-content: center;
      color: var(--el-text-color-placeholder);
    }
  }

  .pm-dim { color: var(--el-text-color-placeholder); font-size: 12px; }

  .pm-grid {
    display: grid;
    grid-template-columns: repeat(auto-fill, minmax(190px, 1fr));
    gap: 12px;
    margin-bottom: 14px;
  }

  .pm-tile {
    cursor: pointer;
    overflow: hidden;

    &__pic {
      display: block;
      width: 100%;
      height: 220px;
      background: var(--el-fill-color-light);

      &--empty {
        display: flex;
        align-items: center;
        justify-content: center;
        font-size: 28px;
        color: var(--el-text-color-placeholder);
      }
    }

    &__body { padding: 8px 10px 10px; }
    &__style { font-size: 13px; font-weight: 600; color: var(--el-color-primary); }
    &__name {
      height: 38px;
      margin: 4px 0 8px;
      overflow: hidden;
      font-size: 12px;
      line-height: 1.55;
      color: var(--el-text-color-regular);
      display: -webkit-box;
      -webkit-line-clamp: 2;
      -webkit-box-orient: vertical;
    }
    &__foot { display: flex; align-items: center; justify-content: space-between; gap: 6px; }
  }

  .pm-rules :deep(.el-table__header th) { font-weight: 600; }

  .pm-detail {
    &__meta {
      display: flex;
      flex-wrap: wrap;
      gap: 16px;
      margin-bottom: 8px;
      font-size: 13px;
      color: var(--el-text-color-regular);

      b { margin-right: 6px; color: var(--el-text-color-primary); }
    }

    &__h {
      margin: 18px 0 8px;
      padding-left: 8px;
      border-left: 3px solid var(--el-color-primary);
      font-size: 13px;
      font-weight: 600;
    }

    &__img {
      position: relative;
      margin-bottom: 12px;

      :deep(.el-image) {
        width: 100%;
        max-height: 520px;
        border: 1px solid var(--el-border-color-lighter);
        border-radius: 6px;
      }
    }

    &__open {
      position: absolute;
      right: 8px;
      bottom: 8px;
      padding: 0 8px;
      border-radius: 4px;
      background: rgb(255 255 255 / 85%);
      font-size: 12px;
    }
  }
</style>
