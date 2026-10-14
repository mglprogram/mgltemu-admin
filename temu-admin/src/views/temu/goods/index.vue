<!--
  TEMU 在售款明细（商品列表 · 在售中）

  数据来自 public/data/goods.json（由 scripts/goods/run_goods_on_sale.py 抓取 +
  build_goods_data.py 导出），静态托管，无后端依赖。

  页面结构（用户 2026-10-12 指定）：
    ① 顶部**置顶**「在售中」这一档的统计栏（SPU / 款数 / SKU / 库存 / 均价），
       向下滚动时固定在视口顶部；
    ② SPU / 货号 搜索框 + 店铺 / 类目过滤；
    ③ 在售款明细表（按 SKC 一行，展开看该款每个 SKU 的尺码/颜色/库存/申报价）。
-->
<template>
  <div class="goods-page app-container">
    <!-- 页头 -->
    <div class="goods-header">
      <div>
        <h2 class="goods-header__title">在售款明细 · 商品列表</h2>
        <p class="goods-header__desc">
          TEMU 商家中心「商品列表 · 在售中」全量导出，可按 SPU / 货号检索
        </p>
      </div>
      <div class="goods-header__meta">
        <el-tag v-if="data.generatedAt" type="info" effect="plain" size="small">
          数据时间 {{ data.generatedAt }}
        </el-tag>
        <el-button :loading="loading" @click="load">
          <el-icon><Refresh /></el-icon><span>刷新</span>
        </el-button>
      </div>
    </div>

    <el-alert
      v-if="loadError"
      type="error"
      :closable="false"
      show-icon
      class="goods-alert"
      :title="loadError"
      description="请先跑 python scripts/goods/run_goods_on_sale.py --store all 再跑 build_goods_data.py 重新生成 goods.json"
    />

    <!-- ① 置顶统计栏：在售中这一档 -->
    <div class="goods-pinbar">
      <div class="goods-pinbar__status">
        <span class="goods-pinbar__dot" />
        <span class="goods-pinbar__label">在售中</span>
        <el-tag size="small" effect="dark" type="success">当前口径</el-tag>
      </div>

      <div class="goods-pinbar__body">
        <div v-for="s in pinStats" :key="s.label" class="goods-pinbar__stat">
          <div class="goods-pinbar__v" :style="{ color: s.color || undefined }">{{ s.value }}</div>
          <div class="goods-pinbar__l">{{ s.label }}</div>
        </div>
      </div>

      <div class="goods-pinbar__aside">
        <span class="goods-pinbar__scope">{{ scopeText }}</span>
        <span v-if="statusCompare" class="goods-pinbar__cmp">页面状态对账：{{ statusCompare }}</span>
      </div>
    </div>

    <!-- ② 店铺切换 -->
    <div class="goods-switch">
      <el-radio-group v-model="store" size="default">
        <el-radio-button value="">
          全部
          <span class="goods-switch__badge">{{ data.totals.onSaleSkc }}</span>
        </el-radio-button>
        <el-radio-button v-for="k in storeKeys" :key="k" :value="k">
          {{ data.stores[k].label }}
          <span class="goods-switch__badge">{{ data.stores[k].counts.onSaleSkc }}</span>
        </el-radio-button>
      </el-radio-group>

      <el-radio-group v-model="view" size="default" class="goods-switch__view">
        <el-radio-button value="todo">
          今日待办
          <span class="goods-switch__badge goods-switch__badge--ok">
            {{ todoBuckets.find((b) => b.label === '可加库存')?.value ?? 0 }}
          </span>
        </el-radio-button>
        <el-radio-button value="all">在售款明细</el-radio-button>
        <el-radio-button value="low">
          低库存款·尺码
          <span class="goods-switch__badge goods-switch__badge--warn">{{ lowSummary.sku }}</span>
        </el-radio-button>
      </el-radio-group>
    </div>

    <!-- ②.5 今日待办：把当天的补货清单摆成一页（只读，方便照着操作） -->
    <el-card v-if="view === 'todo'" shadow="never" class="goods-card" id="daily-todo">
      <template #header>
        <div class="goods-card-head">
          <span class="goods-card-title">
            今日待办 · 补货清单
            <span class="goods-dim">
              （数据 {{ restockMeta?.generatedAt || "—" }} · 看板快照
              {{ restockMeta?.boardBuiltAt || "—" }}）
            </span>
          </span>
          <span class="goods-low-stats__hint">
            「可加库存」= 加州+kansas 有现货且算得出量；「可开预售」= 仓内无货但 7 天内有到货。
            本页只读，**不会动库存**；明细/悬浮逐仓拆分在「低库存款·尺码」页
          </span>
        </div>
      </template>

      <div class="goods-low-stats">
        <div v-for="b in todoBuckets" :key="b.label" class="goods-low-stats__item">
          <div class="goods-low-stats__num" :style="{ color: b.color || undefined }">
            {{ b.value }}
          </div>
          <div class="goods-low-stats__label">{{ b.label }}</div>
        </div>
      </div>

      <el-table
        v-loading="loading"
        :data="todoRows"
        size="default"
        stripe
        height="calc(100vh - 380px)"
      >
        <el-table-column label="优先级" width="86">
          <template #default="{ row }">
            <el-tag :type="row.tone" size="small" effect="light">{{ row.kind }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column label="店铺" width="76">
          <template #default="{ row }">
            <span class="goods-store">{{ row.store }}</span>
          </template>
        </el-table-column>
        <el-table-column label="货号" width="215">
          <template #default="{ row }">
            <span class="goods-code">{{ row.code }}</span>
          </template>
        </el-table-column>
        <el-table-column prop="size" label="尺码" width="78" />
        <el-table-column prop="stock" label="TEMU可售" width="96" sortable />
        <el-table-column prop="used" label="加州+kansas" width="118" sortable />
        <el-table-column label="建议加" width="96" sortable prop="add">
          <template #default="{ row }">
            <b v-if="row.add" class="goods-low-add">{{ row.add }}</b>
            <span v-else class="goods-dim">—</span>
          </template>
        </el-table-column>
        <el-table-column label="到货" width="126">
          <template #default="{ row }">
            <span v-if="row.arrival" class="goods-arrival">{{ row.arrival }}</span>
            <span v-else class="goods-dim">—</span>
          </template>
        </el-table-column>
        <el-table-column prop="title" label="商品" min-width="200" show-overflow-tooltip />
      </el-table>
    </el-card>

    <!-- ③ 低库存（款 × 尺码 < 阈值）：一个款一行，展开看该款全部尺码 -->
    <el-card v-if="view === 'low'" shadow="never" class="goods-card" id="low-stock">
      <template #header>
        <div class="goods-card-head">
          <span class="goods-card-title">
            低库存款·尺码（库存 < {{ data.lowStockThreshold }}）·
            {{ lowGroupsFiltered.length }} 个款 / {{ lowSummary.sku }} 个尺码
            {{ store ? "· " + data.stores[store]?.label : "· 两店合计" }}
          </span>
          <div class="goods-card-head__filters">
            <el-input
              v-model="lowQuery"
              placeholder="款号 / SPU / SKU货号"
              clearable
              class="goods-filter-input"
            >
              <template #prefix><el-icon><Search /></el-icon></template>
            </el-input>
            <el-select v-model="lowBucket" class="goods-filter-select">
              <el-option label="全部低库存" value="" />
              <el-option label="仅 0 库存（断货）" value="zero" />
              <el-option label="库存 1-4" value="1-4" />
              <el-option label="库存 5-9" value="5-9" />
            </el-select>
          </div>
        </div>
      </template>

      <div class="goods-low-stats">
        <div v-for="b in lowBuckets" :key="b.label" class="goods-low-stat">
          <span class="goods-low-stat__v" :style="{ color: b.color }">{{ b.value }}</span>
          <span class="goods-low-stat__l">{{ b.label }}</span>
        </div>
        <span class="goods-low-stats__hint">
          按「该款最低库存」升序；展开可看该款全部尺码。
          「仓内现货」列 = 全部海外仓合计（= 看板页那一列），悬浮可看逐仓拆分；
          「建议加库存」只按**加州仓**现货算：≥150 加 30，否则 ×20%（不足 3 按 3），
          且不超过加州仓现货 —— 与「加库存」脚本同一算法
        </span>      </div>

      <div class="goods-low-verdicts">
        <el-radio-group v-model="verdict" size="small">
          <el-radio-button value="">全部结论</el-radio-button>
          <el-radio-button v-for="v in verdictOptions" :key="v.name" :value="v.name">
            {{ v.name }}
            <span class="goods-switch__badge">{{ v.value }}</span>
          </el-radio-button>
        </el-radio-group>
        <span v-if="restockMeta" class="goods-low-verdicts__src">
          海外仓
          <a :href="restockMeta.boardUrl || '#'" target="_blank" rel="noreferrer">看板</a>
          {{ restockMeta.boardAt || "—" }} · 领星到货表 {{ restockMeta.feishuAt || "—" }}
          <template v-if="restockMeta.canRestockTotal">
            · 建议加量合计 <b>{{ restockMeta.canRestockTotal.toLocaleString() }}</b> 件
          </template>
          · 口径：{{ restockMeta.rule }}
        </span>
      </div>

      <el-table
        v-loading="loading"
        :data="lowPaged"
        :row-key="lowGroupKey"
        size="default"
        stripe
        height="calc(100vh - 330px)"
      >
        <el-table-column type="expand">
          <template #default="{ row }">
            <div class="goods-sku-box">
              <div class="goods-sku-box__title">
                {{ row.code }} · SKU 明细（该款在售 {{ row.skuCount }} 个尺码，其中
                {{ row.lowCount }} 个库存 < {{ data.lowStockThreshold }}，已标出）
              </div>
              <el-table :data="row.skus" size="small" border max-height="320"
                        :row-class-name="({ row: s }: any) => (s.low ? 'goods-low-row' : '')">
                <el-table-column prop="code" label="SKU 货号" min-width="190" show-overflow-tooltip />
                <el-table-column prop="color" label="颜色" min-width="100" show-overflow-tooltip />
                <el-table-column prop="size" label="尺码" width="90" />
                <el-table-column label="申报价" width="100" align="right">
                  <template #default="{ row: s }">{{ money(s.price) }}</template>
                </el-table-column>
                <el-table-column label="TEMU库存" width="110" align="right" sortable prop="stock">
                  <template #default="{ row: s }">
                    <span :class="stockClass(s.stock)">{{ s.stock }}</span>
                  </template>
                </el-table-column>
                <el-table-column label="仓内现货" width="110" align="right">
                  <template #default="{ row: s }">
                    <el-popover
                      v-if="whBreakdown(row.storeKey, row.code, s.size).length"
                      placement="left"
                      :width="320"
                      trigger="hover"
                    >
                      <template #reference>
                        <span class="goods-wh" :class="(s.whStockRaw ?? 0) > 0 ? 'is-ok' : ''">
                          {{ s.whStockRaw ?? "—" }}
                        </span>
                      </template>
                      <div class="wh-pop">
                        <div class="wh-pop__title">
                          仓内现货拆分 · {{ row.code }}-{{ s.size }}
                          <span v-if="s.stockSource" class="wh-pop__src">{{ s.stockSource }}</span>
                        </div>
                        <div
                          v-for="w in whBreakdown(row.storeKey, row.code, s.size)"
                          :key="(w.仓库 || w.来源 || '') + (w.看板键 || '')"
                          class="wh-pop__row"
                        >
                          <template v-if="w.仓库">
                            <span class="wh-pop__wh">{{ w.仓库 }}</span>
                            <span class="wh-pop__tag">{{ w.货源 || "—" }}</span>
                            <b class="wh-pop__num">{{ w.代发库存 ?? 0 }}</b>
                            <span v-if="w.代发途中" class="wh-pop__transit">
                              途中 {{ w.代发途中 }}
                            </span>
                          </template>
                          <template v-else>
                            <span class="wh-pop__foot">
                              来源：{{ w.来源 }} · 看板键 {{ w.看板键 }}
                            </span>
                          </template>
                        </div>
                        <div class="wh-pop__facts">
                          <span>
                            日均 <b>{{ fmt(s.dailyUse) }}</b>
                            <span class="wh-pop__src">（{{ s.dailySource || "—" }}）</span>
                          </span>
                          <span v-if="s.boardDaily !== null && s.boardDaily !== undefined">
                            看板日均 {{ fmt(s.boardDaily) }}
                          </span>
                          <span v-if="s.lingxingDaily !== null && s.lingxingDaily !== undefined">
                            领星日耗 {{ fmt(s.lingxingDaily) }}
                          </span>
                          <span v-if="s.suggestAdd">
                            30 天需求 {{ fmt(s.needUnits) }} − TEMU {{ s.stock }} ⇒
                            <b>建议加 {{ s.suggestAdd }}</b>
                            （上限 = 仓内 {{ s.whStockRaw }}）
                          </span>
                          <span v-if="s.coverDays !== null && s.coverDays !== undefined">
                            全加进去能卖 {{ fmt(s.coverDays) }} 天（目标 30 天<template
                              v-if="s.shortDays"
                            >，还差 {{ fmt(s.shortDays) }} 天</template>）
                          </span>
                        </div>
                        <div class="wh-pop__note">
                          看板快照 {{ restockMeta?.boardAt || "—" }}
                          <template v-if="restockMeta?.boardFetchedAt">
                            · 抓取 {{ restockMeta.boardFetchedAt }}
                          </template>
                          <template v-if="restockMeta?.boardUrl">
                            ·
                            <a :href="restockMeta.boardUrl" target="_blank" rel="noreferrer">
                              打开看板核对
                            </a>
                          </template>
                        </div>
                        <div v-if="s.variantNote" class="wh-pop__note">{{ s.variantNote }}</div>
                      </div>
                    </el-popover>
                    <span v-else class="goods-dim">—</span>
                  </template>
                </el-table-column>
                <el-table-column label="建议加库存" width="120" align="right">
                  <template #default="{ row: s }">
                    <span v-if="s.suggestAdd" class="goods-add">{{ s.suggestAdd }}</span>
                    <span v-else class="goods-dim">—</span>
                  </template>
                </el-table-column>
                <el-table-column label="结论" width="140">
                  <template #default="{ row: s }">
                    <el-tooltip
                      v-if="s.verdict"
                      :content="s.reason || lowReason(row.storeKey, row.code, s.size)"
                      placement="top"
                      :show-after="200"
                    >
                      <el-tag :type="verdictTone(s.verdict)" size="small" effect="light">
                        {{ s.verdict }}
                      </el-tag>
                    </el-tooltip>
                    <span v-else class="goods-dim">—</span>
                  </template>
                </el-table-column>
                <el-table-column prop="skuId" label="SKU ID" width="140" />
              </el-table>
            </div>
          </template>
        </el-table-column>

        <el-table-column v-if="!store" prop="storeLabel" label="店铺" width="90" fixed="left" />
        <el-table-column label="货号(款号)" width="215" fixed="left" show-overflow-tooltip>
          <template #default="{ row }">
            <span class="goods-code">{{ row.code }}</span>
            <el-tooltip
              v-if="variantNoteOf(row.storeKey, row.code)"
              :content="variantNoteOf(row.storeKey, row.code)"
              placement="top"
            >
              <el-tag size="small" type="warning" effect="plain" class="goods-code__flag">
                末尾数字
              </el-tag>
            </el-tooltip>
          </template>
        </el-table-column>
        <el-table-column prop="spuId" label="SPU ID" width="112" />
        <el-table-column prop="category" label="叶子类目" width="104" show-overflow-tooltip />
        <el-table-column label="SKU 数" width="78" align="right">
          <template #default="{ row }">{{ row.skuCount }}</template>
        </el-table-column>
        <el-table-column label="低库存尺码" width="105" align="right" prop="lowCount" sortable>
          <template #default="{ row }">
            <span :class="row.lowCount >= row.skuCount ? 'goods-stock--zero' : 'goods-stock--low'">
              {{ row.lowCount }} / {{ row.skuCount }}
            </span>
          </template>
        </el-table-column>
        <el-table-column label="这些尺码的库存" width="128" align="right">
          <template #default="{ row }">
            <span :class="stockClass(row.minLow)">
              {{ row.lowSizes.length ? row.lowSizes.join(" / ") : "—" }}
            </span>
          </template>
        </el-table-column>
        <el-table-column label="仓内现货" width="100" align="right" prop="whStockRaw" sortable>
          <template #default="{ row }">
            <el-popover
              v-if="whBreakdown(row.storeKey, row.code).length"
              placement="left"
              :width="340"
              trigger="hover"
            >
              <template #reference>
                <span class="goods-wh" :class="row.whStockRaw > 0 ? 'is-ok' : ''">
                  {{ row.whStockRaw ?? "—" }}
                </span>
              </template>
              <div class="wh-pop">
                <div class="wh-pop__title">
                  该款按仓拆分的现货（各尺码合计）
                  <span v-if="row.stockSource" class="wh-pop__src">{{ row.stockSource }}</span>
                </div>
                <div
                  v-for="w in whBreakdown(row.storeKey, row.code)"
                  :key="(w.仓库 || w.来源 || '') + (w.看板键 || '')"
                  class="wh-pop__row"
                >
                  <template v-if="w.仓库">
                    <span class="wh-pop__wh">{{ w.仓库 }}</span>
                    <span class="wh-pop__tag">{{ w.货源 || "—" }}</span>
                    <b class="wh-pop__num">{{ w.代发库存 ?? 0 }}</b>
                    <span v-if="w.代发途中" class="wh-pop__transit">途中 {{ w.代发途中 }}</span>
                  </template>
                  <template v-else>
                    <span class="wh-pop__foot">来源：{{ w.来源 }}</span>
                  </template>
                </div>
              </div>
            </el-popover>
            <span v-else class="goods-dim">—</span>
          </template>
        </el-table-column>
        <el-table-column label="库存合计" width="100" align="right" prop="totalStock" sortable />
        <el-table-column label="可加尺码" width="100" align="right" prop="restockCount" sortable>
          <template #default="{ row }">
            <span :class="row.restockCount ? 'goods-stock--ok' : 'goods-dim'">
              {{ row.restockCount || "—" }}
            </span>
          </template>
        </el-table-column>
        <el-table-column label="建议加量" width="100" align="right" prop="suggestAddTotal" sortable>
          <template #default="{ row }">
            <span v-if="row.suggestAddTotal" class="goods-add">{{ row.suggestAddTotal }}</span>
            <span v-else class="goods-dim">—</span>
          </template>
        </el-table-column>
        <el-table-column label="最近到货" width="105">
          <template #default="{ row }">
            <el-popover
              v-if="row.arrivalDate"
              placement="left"
              :width="330"
              trigger="hover"
            >
              <template #reference>
                <span class="goods-arrival">{{ row.arrivalDate }}</span>
              </template>
              <div class="wh-pop">
                <div class="wh-pop__title">
                  最近到货 · {{ row.code }}
                  <span class="wh-pop__src">领星到货计划甘特</span>
                </div>
                <div
                  v-for="(b, i) in (row.arrivalBatches || []).slice(0, 5)"
                  :key="i"
                  class="wh-pop__row"
                >
                  <span class="wh-pop__wh">
                    {{ b.size }} · 批次 {{ b.批次 || "—" }}
                  </span>
                  <span class="wh-pop__tag">{{ b.渠道 || "—" }}</span>
                  <b class="wh-pop__num">{{ b.件数 ?? 0 }} 件</b>
                  <span class="wh-pop__transit">{{ b.到货日 }}</span>
                </div>
                <div v-if="!(row.arrivalBatches || []).length" class="wh-pop__foot">
                  到货计划里没有未入仓批次（日期来自库存覆盖甘特的推算）
                </div>
                <div class="wh-pop__note">
                  飞书到货表 {{ restockMeta?.feishuAt || "—" }}
                  <template v-if="restockMeta?.feishuUrl">
                    ·
                    <a :href="restockMeta.feishuUrl" target="_blank" rel="noreferrer">
                      打开飞书到货表
                    </a>
                  </template>
                </div>
              </div>
            </el-popover>
            <span v-else class="goods-dim">—</span>
          </template>
        </el-table-column>
        <el-table-column label="结论" width="160">
          <template #default="{ row }">
            <el-tag
              v-for="(n, v) in row.verdicts"
              :key="v"
              :type="verdictTone(String(v))"
              size="small"
              effect="light"
              class="goods-verdict-tag"
            >
              {{ v }} {{ n }}
            </el-tag>
          </template>
        </el-table-column>
        <!-- 标题放最后一列（min-width 自适应）：它最长，放前面会把「最近到货」等
             关键列挤出视口，用户就得横向拖才能看到日期（2026-10-12 反馈） -->
        <el-table-column prop="title" label="商品标题" min-width="180" show-overflow-tooltip />
      </el-table>

      <Pagination
        v-model:page="lowPageNum"
        v-model:limit="lowPageSize"
        :total="lowGroups.length"
        :page-sizes="[20, 50, 100, 200]"
      />
    </el-card>

    <!-- ④ 明细表 -->
    <el-card v-show="view === 'all'" shadow="never" class="goods-card">
      <template #header>
        <div class="goods-card-head">
          <span class="goods-card-title">
            在售款明细（{{ filtered.length }} / {{ allItems.length }}）
            {{ store ? "· " + data.stores[store]?.label : "· 两店合计" }}
          </span>
          <div class="goods-card-head__filters">
            <el-input
              v-model="spuQuery"
              placeholder="SPU ID"
              clearable
              class="goods-filter-input goods-filter-input--spu"
            >
              <template #prefix><el-icon><Search /></el-icon></template>
            </el-input>
            <el-input
              v-model="codeQuery"
              placeholder="货号 / 款号（如 BEL-104504）"
              clearable
              class="goods-filter-input"
            >
              <template #prefix><el-icon><Search /></el-icon></template>
            </el-input>
            <el-select v-model="category" placeholder="叶子类目" clearable class="goods-filter-select">
              <el-option v-for="c in categoryOptions" :key="c" :label="c" :value="c" />
            </el-select>
          </div>
        </div>
      </template>

      <el-table
        v-show="view === 'all'"
        v-loading="loading"
        :data="pagedItems"
        row-key="skcId"
        stripe
        height="calc(100vh - 300px)"
      >
        <el-table-column type="expand">
          <template #default="{ row }">
            <div class="goods-sku-box">
              <div class="goods-sku-box__title">
                {{ row.code }} · SKU 明细（{{ row.skus.length }} 个）
              </div>
              <el-table :data="row.skus" size="small" border max-height="320">
                <el-table-column prop="code" label="SKU 货号" min-width="190" show-overflow-tooltip />
                <el-table-column prop="color" label="颜色" min-width="100" show-overflow-tooltip />
                <el-table-column prop="size" label="尺码" width="90" />
                <el-table-column label="申报价" width="100" align="right">
                  <template #default="{ row: s }">{{ money(s.price) }}</template>
                </el-table-column>
                <el-table-column label="库存" width="90" align="right">
                  <template #default="{ row: s }">
                    <span :class="{ 'goods-stock--low': (s.stock ?? 0) <= 5 }">{{ s.stock }}</span>
                  </template>
                </el-table-column>
                <el-table-column prop="skuId" label="SKU ID" width="140" />
              </el-table>
            </div>
          </template>
        </el-table-column>
        <el-table-column v-if="!store" prop="storeLabel" label="店铺" width="90" fixed="left" />
        <el-table-column prop="code" label="货号(款号)" width="200" fixed="left" show-overflow-tooltip />
        <el-table-column prop="spuId" label="SPU ID" width="125" />
        <el-table-column prop="title" label="商品标题" min-width="220" show-overflow-tooltip />
        <el-table-column prop="category" label="叶子类目" width="120" show-overflow-tooltip />
        <el-table-column label="SKU 数" width="90" align="right" prop="skuCount" sortable />
        <el-table-column label="库存" width="100" align="right" prop="stock" sortable>
          <template #default="{ row }">
            <span :class="{ 'goods-stock--zero': row.stock === 0 }">{{ row.stock }}</span>
          </template>
        </el-table-column>
        <el-table-column label="申报价" width="130" align="right" prop="priceMin" sortable>
          <template #default="{ row }">{{ priceRange(row) }}</template>
        </el-table-column>
        <el-table-column label="近7日销量" width="110" align="right" prop="sales7" sortable>
          <template #default="{ row }">{{ row.sales7 ?? "—" }}</template>
        </el-table-column>
      </el-table>

      <Pagination
        v-model:page="pageNum"
        v-model:limit="pageSize"
        :total="filtered.length"
        :page-sizes="[20, 50, 100, 200]"
      />
    </el-card>
  </div>
</template>

<script setup lang="ts">
import GoodsAPI from "@/api/temu/goods";
import type {
  GoodsData,
  GoodsItem,
  LowStockGroup,
  LowStockRow,
  LowStockSummary,
} from "@/api/temu/goods";
import { useRoute } from "vue-router";

defineOptions({ name: "TemuGoodsOnSale" });

const route = useRoute();

const loading = ref(false);
const loadError = ref("");
const EMPTY_LOW: LowStockSummary = {
  sku: 0,
  style: 0,
  zero: 0,
  buckets: [],
  stores: [],
  minStock: null,
  top: [],
};
const data = ref<GoodsData>({
  generatedAt: null,
  source: "",
  statusLabel: "在售中",
  statusCode: "100",
  lowStockThreshold: 10,
  lowStock: { threshold: 10, rows: [], summary: EMPTY_LOW },
  stores: {},
  totals: { onSaleSkc: 0, spu: 0, sku: 0, stock: 0 },
});

async function load() {
  loading.value = true;
  loadError.value = "";
  try {
    const d = await GoodsAPI.getGoodsData();
    data.value = d;
    // 首页卡片可以用 ?view=low 直接落到低库存视图
    if (String(route.query.view || "") === "low") view.value = "low";
    // ?view=todo 直接落到「今日待办」页签（首页卡片/飞书消息里的深链）
    if (String(route.query.view || "") === "todo") view.value = "todo";
    if (route.query.store) store.value = String(route.query.store);
  } catch (error) {
    loadError.value = error instanceof Error ? error.message : "加载在售款数据失败";
  } finally {
    loading.value = false;
  }
}

onMounted(load);

/** 视图：在售款明细 / 低库存款·尺码 */
const view = ref<"all" | "low" | "todo">("all");

const money = (v: number | null | undefined) =>
  v === null || v === undefined ? "—" : `$${Number(v).toFixed(2)}`;

const num = (v: number | null | undefined) =>
  v === null || v === undefined ? "—" : Number(v).toLocaleString();

// ---------------------------------------------------------------- 店铺维度
// "" = 两店合计；否则只看该店。整页（含置顶统计栏）都跟着它走。
const store = ref("");
const storeKeys = computed(() => Object.keys(data.value.stores));

/** 当前生效的店铺明细（跨店合并，并标注来源店） */
const allItems = computed<(GoodsItem & { storeKey: string; storeLabel: string })[]>(() => {
  const keys = store.value ? [store.value] : storeKeys.value;
  return keys.flatMap((k) => {
    const s = data.value.stores[k];
    if (!s) return [];
    return s.items.map((it) => ({ ...it, storeKey: k, storeLabel: s.label }));
  });
});

const scopeText = computed(() =>
  store.value
    ? `当前只看 ${data.value.stores[store.value]?.label ?? store.value}`
    : "当前为两店合计；点上方店铺按钮可单独查看"
);

// ---------------------------------------------------------------- 置顶统计
/** 置顶栏指标：在售中 SPU / 款数(SKC) / SKU / 库存 / 均价 / 断货尺码 */
const pinStats = computed(() => {
  const items = allItems.value;
  const spu = new Set(items.map((i) => i.spuId)).size;
  const skc = items.length;
  const sku = items.reduce((a, i) => a + i.skuCount, 0);
  const stock = items.reduce((a, i) => a + i.stock, 0);
  const prices = items.flatMap((i) => i.skus.map((s) => s.price).filter((p): p is number => p !== null));
  const avg = prices.length ? prices.reduce((a, b) => a + b, 0) / prices.length : null;
  // 与「低库存款·尺码」板块同口径：是**款·尺码**（SKU）粒度，不是款粒度
  const zeroSku = items.reduce(
    (a, i) => a + i.skus.filter((s) => (s.stock ?? 0) <= 0).length,
    0
  );
  return [
    { label: "在售中 SPU", value: num(spu), color: "" },
    { label: "在售中款数(SKC)", value: num(skc), color: "" },
    { label: "在售中 SKU", value: num(sku), color: "" },
    { label: "库存合计", value: num(stock), color: "#599ce7" },
    { label: "均价", value: avg === null ? "—" : `$${avg.toFixed(2)}`, color: "" },
    { label: "断货尺码（库存0）", value: num(zeroSku), color: zeroSku ? "#e05561" : "#2fa37c" },
  ];
});

/** 抓取端各状态数量对账（让「在售中 116」这类口径可见） */
const statusCompare = computed(() => {
  const keys = store.value ? [store.value] : storeKeys.value;
  const parts = keys
    .map((k) => {
      const sc = data.value.stores[k]?.statusCounts || {};
      const onSale = sc["在售中"];
      const total = Object.values(sc).reduce((a, b) => a + b, 0);
      return onSale === undefined ? "" : `${k} ${onSale}/${total}`;
    })
    .filter(Boolean);
  return parts.join(" · ");
});

// ---------------------------------------------------------------- 过滤
const spuQuery = ref("");
const codeQuery = ref("");
const category = ref("");
const pageNum = ref(1);
const pageSize = ref(20);

const categoryOptions = computed(() =>
  Array.from(new Set(allItems.value.map((i) => i.category).filter(Boolean))).sort()
);

const filtered = computed(() => {
  let list = allItems.value;
  const spu = spuQuery.value.trim();
  const code = codeQuery.value.trim().toLowerCase();
  if (spu) {
    list = list.filter(
      (i) => i.spuId.includes(spu) || i.skus.some((s) => (s.skuId || "").includes(spu))
    );
  }
  if (code) {
    list = list.filter(
      (i) =>
        i.code.toLowerCase().includes(code) ||
        i.skus.some((s) => (s.code || "").toLowerCase().includes(code)) ||
        (i.title || "").toLowerCase().includes(code)
    );
  }
  if (category.value) {
    list = list.filter((i) => i.category === category.value);
  }
  return list;
});

const pagedItems = computed(() => {
  const start = (pageNum.value - 1) * pageSize.value;
  return filtered.value.slice(start, start + pageSize.value);
});

/** 申报价区间显示：单一价就显示一个 */
function priceRange(row: { priceMin?: number | null; priceMax?: number | null }) {
  const lo = row.priceMin;
  const hi = row.priceMax;
  if (lo === null || lo === undefined) return "—";
  if (hi === null || hi === undefined || hi === lo) return money(lo);
  return `${money(lo)} ~ ${money(hi)}`;
}

// 切店/改筛选条件后回第一页，避免停在越界空页
watch([store, spuQuery, codeQuery, category], () => {
  pageNum.value = 1;
});

// ---------------------------------------------------------------- 低库存（款 × 尺码）
// 展示口径（用户 2026-10-12 要求）：**一个款一行**，展开看该款全部尺码
// —— 与上面的「在售款明细」表结构一致，不再是一行一个尺码。
const lowQuery = ref("");
const lowBucket = ref("");
const verdict = ref("");
const lowPageNum = ref(1);
const lowPageSize = ref(50);

/**
 * 行的唯一键。
 *
 * ⚠ 不能只用 `店铺|款号`：同一个款号可能对应**两个 SKC**（在售明细里查得到，
 * 如 `BEL-102503-5SetB` / `BHY-103910-1B` 各有 2 个 SKC）。行键撞了之后，
 * Element Plus 的展开状态是按行键匹配的（`getKeysMap(expandRows, rowKey)`），
 * 于是一行展开会连带展开所有同键行。
 * ⇒ 键里再带上该款合并的 SKC 集合，保证一行一键。
 *
 * ⚠⚠ 展开状态**交给 Element Plus 自己管**（不再用 `:expand-row-keys` 受控）：
 * 它的 `expand-change` 事件第二个参数是**当前已展开行的数组**（不是布尔），
 * 我原先按布尔用，于是「展开 A → 收起 A → 展开 B」时 A 会被重新塞回展开列表
 * —— 2026-10-12 用户报的正是这个（实测：展开3 后变成 [0,3]）。
 * 只要 `row-key` 稳定，组件内部的状态就够用，受控反而引入这类反馈回路。
 */
const lowGroupKey = (g: { storeKey: string; code: string; skcIds?: string[] }) =>
  `${g.storeKey}|${g.code}|${(g.skcIds ?? []).join(",")}`;

/** 原始低库存行（含判定字段），键 = 店铺|款号|尺码，供展开行的 tooltip 用 */
const lowRowIndex = computed(() => {
  const m = new Map<string, LowStockRow>();
  for (const s of Object.values(data.value.stores)) {
    for (const r of s.lowStock?.rows ?? []) {
      m.set(`${r.store}|${r.code}|${r.size}`, r);
    }
  }
  return m;
});

function lowReason(storeKey: string, code: string, size: string) {
  return lowRowIndex.value.get(`${storeKey}|${code}|${size}`)?.reason ?? "";
}

/** 该款的变体兜底说明（货号末尾数字被忽略时非空），给货号列加个小标记 */
function variantNoteOf(storeKey: string, code: string) {
  return lowRowsOf(storeKey, code).find((r) => r.variantNote)?.variantNote ?? "";
}

/** 按「款」聚合：一个款一行，skus 放该店该款**全部**尺码 */
const lowGroups = computed(() => {
  const out: (LowStockGroup & { lowSizes: number[]; minLow: number })[] = [];
  const keys = store.value ? [store.value] : storeKeys.value;
  for (const k of keys) {
    const s = data.value.stores[k];
    if (!s) continue;
    const byCode = new Map<string, LowStockRow[]>();
    for (const r of s.lowStock?.rows ?? []) {
      const list = byCode.get(r.code) ?? [];
      list.push(r);
      byCode.set(r.code, list);
    }
    const itemsByCode = new Map(s.items.map((i) => [i.code, i]));
    for (const [code, lowRowsOfCode] of byCode) {
      const item = itemsByCode.get(code);
      const lowSizeSet = new Map(lowRowsOfCode.map((r) => [r.size, r]));
      const skus = (item?.skus ?? []).map((sk) => {
        const low = lowSizeSet.get(sk.size);
        return {
          ...sk,
          low: !!low,
          // 判定字段按尺码带上（展开行的悬浮/结论列都要用）
          verdict: low?.verdict,
          reason: low?.reason,
          suggestAdd: low?.suggestAdd ?? null,
          whStockRaw: low?.whStockRaw ?? null,
          whStock: low?.whStock ?? null,
          dailyUse: low?.dailyUse ?? null,
          dailySource: low?.dailySource ?? "",
          boardDaily: low?.boardDaily ?? null,
          lingxingDaily: low?.lingxingDaily ?? null,
          needUnits: low?.needUnits ?? null,
          afterStock: low?.afterStock ?? null,
          coverDays: low?.coverDays ?? null,
          shortDays: low?.shortDays ?? null,
          transitRaw: low?.transitRaw ?? null,
          transitSource: low?.transitSource ?? "",
          arrivalDate: low?.arrivalDate ?? "",
          arrivalSource: low?.arrivalSource ?? "",
          variantNote: low?.variantNote ?? "",
          whByWarehouse: low?.whByWarehouse ?? [],
          boardKey: low?.boardKey ?? "",
          stockSource: low?.stockSource ?? "",
          factor: low?.factor ?? 1,
          nearKeys: low?.nearKeys ?? [],
        };
      });
      const verdicts: Record<string, number> = {};
      for (const r of lowRowsOfCode) {
        const v = r.verdict || "未判定";
        verdicts[v] = (verdicts[v] ?? 0) + 1;
      }
      const restockRows = lowRowsOfCode.filter((r) => r.verdict === "可加库存");
      out.push({
        storeKey: k,
        storeLabel: s.label,
        code,
        skcIds: [...new Set(lowRowsOfCode.map((r) => r.skcId).filter(Boolean))],
        spuId: lowRowsOfCode[0].spuId,
        skcId: lowRowsOfCode[0].skcId,
        title: lowRowsOfCode[0].title,
        category: lowRowsOfCode[0].category,
        priceMin: item?.priceMin ?? lowRowsOfCode[0].price ?? null,
        priceMax: item?.priceMax ?? lowRowsOfCode[0].price ?? null,
        skuCount: item?.skuCount ?? lowRowsOfCode.length,
        lowCount: lowRowsOfCode.length,
        totalStock: item?.stock ?? lowRowsOfCode.reduce((a, r) => a + r.stock, 0),
        lowStockSum: lowRowsOfCode.reduce((a, r) => a + r.stock, 0),
        skus,
        restockCount: restockRows.length,
        suggestAddTotal: restockRows.reduce((a, r) => a + (r.suggestAdd ?? 0), 0),
        arrivalDate:
          lowRowsOfCode.map((r) => r.arrivalDate).filter(Boolean).sort()[0] ?? "",
        arrivalBatches: lowRowsOfCode.flatMap((r) =>
          (r.arrivalBatches ?? []).map((b) => ({ ...b, size: r.size }))
        ),
        verdicts,
        lowSizes: lowRowsOfCode.map((r) => r.stock).sort((a, b) => a - b),
        minLow: Math.min(...lowRowsOfCode.map((r) => r.stock)),
      });
    }
  }
  return out.sort((a, b) => a.minLow - b.minLow || a.code.localeCompare(b.code));
});

/** 单行是否通过「搜索 + 库存档 + 结论」三道筛选 */
function rowPasses(r: LowStockRow) {
  const kw = lowQuery.value.trim().toLowerCase();
  if (
    kw &&
    !(
      r.code.toLowerCase().includes(kw) ||
      (r.skuCode || "").toLowerCase().includes(kw) ||
      r.spuId.includes(kw) ||
      (r.title || "").toLowerCase().includes(kw)
    )
  ) {
    return false;
  }
  if (lowBucket.value === "zero" && r.stock > 0) return false;
  if (lowBucket.value === "1-4" && !(r.stock >= 1 && r.stock <= 4)) return false;
  if (lowBucket.value === "5-9" && !(r.stock >= 5 && r.stock <= 9)) return false;
  if (verdict.value && (r.verdict || "未判定") !== verdict.value) return false;
  return true;
}

/** 某个「款」的低库存行（该店该款号） */
function lowRowsOf(storeKey: string, code: string): LowStockRow[] {
  return (data.value.stores[storeKey]?.lowStock?.rows ?? []).filter(
    (r) => r.store === storeKey && r.code === code
  );
}

/** 过滤后的款列表（表体）：
 *  ⚠ 判据是「该款**有没有任何一个低库存尺码**通过筛选」，不是拿整款的 minLow 去判 ——
 *  前者才符合直觉（筛 0 库存时，只要这个款有断码尺码就该出现）。 */
const lowGroupsFiltered = computed(() =>
  lowGroups.value.filter((g) => lowRowsOf(g.storeKey, g.code).some((r) => rowPasses(r)))
);

/** 过滤后仍然满足条件的**尺码级**行（统计口径用，与表体的款数互补） */
const lowRowsFiltered = computed(() =>
  lowGroupsFiltered.value.flatMap((g) =>
    lowRowsOf(g.storeKey, g.code).filter((r) => rowPasses(r))
  )
);

const lowPaged = computed(() => {
  const start = (lowPageNum.value - 1) * lowPageSize.value;
  return lowGroupsFiltered.value.slice(start, start + lowPageSize.value);
});

/**
 * 今日待办（只读清单）：
 *   可加库存  —— 该加、且算得出量（按加量降序）
 *   可开预售  —— 仓内无货但 7 天内有到货
 *   需补货    —— 仓内与在途都没有
 *   待到货确认 —— 在途但没有到货日
 * 口径与「低库存款·尺码」页完全同源（都来自 goods.json 的判定字段）。
 */
const todoRows = computed(() => {
  const rows = data.value.lowStock?.rows ?? [];
  const order: Record<string, number> = {
    可加库存: 0, 可开预售: 1, 待到货确认: 2, 需补货: 3,
  };
  const tone: Record<string, string> = {
    可加库存: "success", 可开预售: "primary", 待到货确认: "warning", 需补货: "danger",
  };
  return rows
    .filter((r) => order[r.verdict ?? ""] !== undefined)
    .filter((r) => !store.value || r.store === store.value)
    .map((r) => {
      const by = (r.whByWarehouse ?? []).filter((w) => w.仓库);
      const used = by
        .filter((w) => (w.仓库 ?? "").includes("加州") || (w.仓库 ?? "").includes("kansas"))
        .reduce((a, w) => a + (w.代发库存 ?? 0), 0);
      return {
        kind: r.verdict ?? "",
        tone: tone[r.verdict ?? ""] ?? "info",
        store: r.store,
        code: r.code,
        size: r.size,
        stock: r.stock ?? 0,
        used: Math.round(used),
        add: r.suggestAdd ?? 0,
        arrival: r.arrivalDate ?? "",
        title: r.title ?? "",
      };
    })
    .sort((a, b) => {
      const d = (order[a.kind] ?? 9) - (order[b.kind] ?? 9);
      if (d !== 0) return d;
      return b.add - a.add;
    });
});

const todoBuckets = computed(() => {
  const rows = todoRows.value.filter((r) => !store.value || r.store === store.value);
  const add = rows.filter((r) => r.kind === "可加库存");
  return [
    { label: "可加库存", value: add.length, color: "#2fa37c" },
    { label: "可加合计（件）", value: add.reduce((a, r) => a + r.add, 0).toLocaleString(), color: "#2fa37c" },
    { label: "可开预售", value: rows.filter((r) => r.kind === "可开预售").length, color: "#599ce7" },
    { label: "需补货", value: rows.filter((r) => r.kind === "需补货").length, color: "#e05561" },
    { label: "待到货确认", value: rows.filter((r) => r.kind === "待到货确认").length, color: "#e0a33e" },
  ];
});

function stockClass(v: number | null | undefined) {
  const n = Number(v ?? 0);
  if (n <= 0) return "goods-stock--zero";
  if (n < (data.value.lowStockThreshold ?? 10)) return "goods-stock--low";
  return "";
}

type WhRow = NonNullable<LowStockRow["whByWarehouse"]>[number];

/** 悬浮「仓内现货」时给出的按仓拆分。
 *
 *  传了 size → 该尺码那一行的拆分；不传 → 把该款所有低库存尺码的拆分**按仓合并**。
 *  数据来自看板每行的 `货源`（帕沃尔/恒信…）与 `仓库名称`（帕沃尔·kansas仓 等）。
 */
function whBreakdown(storeKey: string, code: string, size?: string): WhRow[] {
  const rows = lowRowsOf(storeKey, code).filter((r) => !size || r.size === size);
  const merged = new Map<string, WhRow>();
  let foot: WhRow | null = null;
  for (const r of rows) {
    for (const w of r.whByWarehouse ?? []) {
      if (!w.仓库) {
        foot = foot ?? w;
        continue;
      }
      const k = w.仓库;
      const cur = merged.get(k) ?? { ...w, 代发库存: 0, 代发途中: 0 };
      cur.代发库存 = (cur.代发库存 ?? 0) + (w.代发库存 ?? 0);
      cur.代发途中 = (cur.代发途中 ?? 0) + (w.代发途中 ?? 0);
      merged.set(k, cur);
    }
  }
  const out = [...merged.values()].sort((a, b) => (b.代发库存 ?? 0) - (a.代发库存 ?? 0));
  return foot ? [...out, foot] : out;
}

/** 低库存概览：尺码级按筛选后的行现算，款数直接用表体行数 */
const lowSummary = computed<LowStockSummary>(() => {
  const rows = lowRowsFiltered.value;
  const zero = rows.filter((r) => r.stock <= 0).length;
  const b1 = rows.filter((r) => r.stock >= 1 && r.stock <= 4).length;
  const b2 = rows.filter((r) => r.stock >= 5 && r.stock <= 9).length;
  return {
    sku: rows.length,
    style: lowGroupsFiltered.value.length,
    zero,
    buckets: [
      { name: "0", value: zero },
      { name: "1-4", value: b1 },
      { name: "5-9", value: b2 },
    ],
    stores: [...new Set(rows.map((r) => r.store))],
    minStock: rows.length ? Math.min(...rows.map((r) => r.stock)) : null,
    top: [],
  };
});

const lowBuckets = computed(() => {
  const rc = data.value.lowStock?.restock;
  const addable = lowRowsFiltered.value.filter((r) => r.verdict === "可加库存");
  return [
    { label: "涉及款数", value: lowGroupsFiltered.value.length, color: "" },
    { label: "款·尺码（<阈值）", value: lowSummary.value.sku, color: "" },
    { label: "0 库存（断货）", value: lowSummary.value.zero, color: "#e05561" },
    {
      label: "可加库存",
      value: rc?.verdictCounts?.["可加库存"] ?? 0,
      color: (rc?.verdictCounts?.["可加库存"] ?? 0) ? "#2fa37c" : "",
    },
    {
      label: "现在能加（件）",
      value: Number(addable.reduce((a, r) => a + (r.suggestAdd ?? 0), 0)).toLocaleString(),
      color: addable.length ? "#2fa37c" : "",
    },
    {
      label: "可开预售",
      value: rc?.verdictCounts?.["可开预售"] ?? 0,
      color: (rc?.verdictCounts?.["可开预售"] ?? 0) ? "#599ce7" : "",
    },
    { label: "待到货", value: rc?.verdictCounts?.["待到货"] ?? 0, color: "#e0a33e" },
    {
      label: "待到货确认",
      value: rc?.verdictCounts?.["待到货确认"] ?? 0,
      color: (rc?.verdictCounts?.["待到货确认"] ?? 0) ? "#e0a33e" : "",
    },
  ];
});

/** 结论筛选项（只列实际出现过的结论，避免一排空按钮） */
const verdictOptions = computed(() => {
  const keys = store.value ? [store.value] : storeKeys.value;
  const cnt: Record<string, number> = {};
  for (const k of keys) {
    const rows = data.value.stores[k]?.lowStock?.rows ?? [];
    for (const r of rows) {
      const v = r.verdict || "未判定";
      cnt[v] = (cnt[v] ?? 0) + 1;
    }
  }
  const order = [
    "可加库存",
    "可开预售",
    "库存够卖(不用加)",
    "仓里有货(未定量)",
    "待到货",
    "待到货确认",
    "需补货",
    "未匹配",
    "未判定",
  ];
  return order
    .filter((k) => cnt[k])
    .map((k) => ({ name: k, value: cnt[k] }));
});

/** 海外仓看板 / 领星表的取数时间，让人知道这份判定的新鲜度 */
const restockMeta = computed(() => {
  const rc = data.value.lowStock?.restock;
  if (!rc?.sources) return null;
  return {
    generatedAt: data.value.generatedAt ?? "",
    restockAt: data.value.restockGeneratedAt ?? "",
    boardAt: rc.sources.看板?.builtAt ?? "",
    boardBuiltAt: rc.sources.看板?.builtAt ?? "",
    boardFetchedAt: rc.sources.看板?.fetchedAt ?? "",
    boardUrl: rc.sources.看板?.url ?? "",
    feishuAt: rc.sources.飞书?.fetchedAt ?? "",
    feishuUrl: rc.sources.飞书?.url ?? "",
    canRestockTotal: rc.suggestAddTotal ?? 0,
    rule: rc.rule ?? "",
  };
});

/** 数值展示（保留 1 位，整数不带小数点） */
function fmt(v: number | null | undefined) {
  if (v === null || v === undefined) return "—";
  const n = Number(v);
  return Number.isInteger(n) ? String(n) : n.toFixed(1);
}

/** 结论 → el-tag 类型 */
function verdictTone(v?: string) {
  if (v === "可加库存") return "success";
  if (v === "可开预售") return "primary";
  if (v === "仓里有货(未定量)") return "primary";
  if (v === "待到货") return "warning";
  if (v === "待到货确认") return "warning";
  if (v === "库存够卖(不用加)") return "info";
  if (v === "作废链接") return "info";
  if (v === "需补货" || v === "未匹配") return "danger";
  return "info";
}

watch([store, lowQuery, lowBucket, verdict], () => {
  lowPageNum.value = 1;
});
</script>

<style lang="scss" scoped>
.goods-header {
  display: flex;
  flex-wrap: wrap;
  gap: 12px;
  align-items: flex-start;
  justify-content: space-between;
  margin-bottom: 12px;

  &__title {
    margin: 0 0 4px;
    font-size: 18px;
    font-weight: 600;
  }

  &__desc {
    margin: 0;
    font-size: 13px;
    color: var(--el-text-color-secondary);
  }

  &__meta {
    display: flex;
    gap: 8px;
    align-items: center;
  }
}

.goods-alert {
  margin-bottom: 12px;
}

// ① 置顶统计栏：吸顶显示「在售中」这一档
.goods-pinbar {
  position: sticky;
  top: 0;
  z-index: 10;
  display: flex;
  flex-wrap: wrap;
  gap: 12px 18px;
  align-items: center;
  padding: 12px 16px;
  margin-bottom: 12px;
  background: var(--el-bg-color-overlay);
  border: 1px solid var(--el-color-success-light-5);
  border-left: 3px solid var(--el-color-success);
  border-radius: var(--card-radius, 6px);
  box-shadow: var(--card-shadow, 0 2px 8px rgb(0 0 0 / 6%));

  &__status {
    display: flex;
    gap: 8px;
    align-items: center;
    padding-right: 16px;
    border-right: 1px dashed var(--el-border-color);
  }

  &__dot {
    width: 8px;
    height: 8px;
    background: var(--el-color-success);
    border-radius: 50%;
    box-shadow: 0 0 0 3px var(--el-color-success-light-8);
  }

  &__label {
    font-size: 15px;
    font-weight: 600;
    color: var(--el-text-color-primary);
  }

  &__body {
    display: flex;
    flex: 1;
    flex-wrap: wrap;
    gap: 10px 26px;
    min-width: 260px;
  }

  &__stat {
    min-width: 74px;
  }

  &__v {
    font-size: 20px;
    font-weight: 600;
    font-variant-numeric: tabular-nums;
    line-height: 1.2;
    color: var(--el-text-color-primary);
  }

  &__l {
    font-size: 12px;
    color: var(--el-text-color-secondary);
  }

  &__aside {
    display: flex;
    flex-direction: column;
    gap: 2px;
    align-items: flex-end;
    font-size: 12px;
    color: var(--el-text-color-placeholder);
  }

  &__cmp {
    font-variant-numeric: tabular-nums;
  }
}

.goods-switch {
  display: flex;
  flex-wrap: wrap;
  gap: 12px;
  align-items: center;
  margin-bottom: 12px;

  &__badge {
    margin-left: 4px;
    font-size: 11px;
    opacity: 0.75;
  }
}

.goods-card {
  margin-bottom: 12px;

  &-title {
    font-size: 14px;
    font-weight: 600;
  }

  &-head {
    display: flex;
    flex-wrap: wrap;
    gap: 10px;
    align-items: center;
    justify-content: space-between;

    &__filters {
      display: flex;
      flex-wrap: wrap;
      gap: 8px;
    }
  }
}

.goods-filter-input {
  width: 240px;

  &--spu {
    width: 150px;
  }
}

.goods-filter-select {
  width: 170px;
}

.goods-switch__view {
  margin-left: auto;
}

.goods-low-add {
  color: var(--el-color-success);
  font-weight: 600;
}

.goods-switch__badge--ok {
  background: var(--el-color-success-light-8);
  color: var(--el-color-success);
}

.goods-switch__badge--warn {
  color: var(--el-color-warning);
  opacity: 1;
}

.goods-low-stats {
  display: flex;
  flex-wrap: wrap;
  gap: 10px 24px;
  align-items: baseline;
  padding: 0 2px 12px;
  border-bottom: 1px dashed var(--el-border-color-lighter);
  margin-bottom: 10px;

  .goods-low-stat {
    display: flex;
    gap: 6px;
    align-items: baseline;

    &__v {
      font-size: 18px;
      font-weight: 600;
      font-variant-numeric: tabular-nums;
      color: var(--el-text-color-primary);
    }

    &__l {
      font-size: 12px;
      color: var(--el-text-color-secondary);
    }
  }

  &__hint {
    margin-left: auto;
    font-size: 12px;
    color: var(--el-text-color-placeholder);
  }
}

.goods-sku-box {
  padding: 4px 12px 10px 48px;
  &__title {
    margin-bottom: 8px;
    font-size: 13px;
    font-weight: 600;
  }
}

.goods-low-verdicts {
  display: flex;
  flex-wrap: wrap;
  gap: 10px;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 10px;

  &__src {
    font-size: 12px;
    color: var(--el-text-color-placeholder);
  }
}

.goods-add {
  font-weight: 600;
  color: var(--el-color-success);
  font-variant-numeric: tabular-nums;
}

.goods-dim {
  color: var(--el-text-color-placeholder);
}

.goods-arrival {
  font-size: 12px;
  color: var(--el-color-warning);
  font-variant-numeric: tabular-nums;
}

.goods-verdict-tag {
  margin: 1px 4px 1px 0;
}

.goods-code {
  &__flag {
    margin-left: 4px;
    cursor: help;
  }
}

// 「仓内现货」：可悬浮看按仓拆分
.goods-wh {
  cursor: help;
  border-bottom: 1px dashed var(--el-border-color);

  &.is-ok {
    font-weight: 600;
    color: var(--el-color-success);
  }
}

.wh-pop {
  font-size: 12px;
  line-height: 1.7;

  &__title {
    display: flex;
    gap: 6px;
    align-items: baseline;
    justify-content: space-between;
    padding-bottom: 4px;
    margin-bottom: 4px;
    font-weight: 600;
    color: var(--el-text-color-primary);
    border-bottom: 1px solid var(--el-border-color-lighter);
  }

  &__src {
    font-weight: 400;
    color: var(--el-text-color-placeholder);
  }

  &__row {
    display: flex;
    gap: 6px;
    align-items: baseline;
  }

  &__wh {
    flex: 1;
    min-width: 0;
    overflow: hidden;
    color: var(--el-text-color-regular);
    text-overflow: ellipsis;
    white-space: nowrap;
  }

  &__tag {
    flex: none;
    padding: 0 5px;
    font-size: 11px;
    color: var(--el-text-color-secondary);
    background: var(--el-fill-color-light);
    border-radius: 3px;
  }

  &__num {
    flex: none;
    min-width: 46px;
    text-align: right;
    font-variant-numeric: tabular-nums;
    color: var(--el-color-success);
  }

  &__transit {
    flex: none;
    font-size: 11px;
    color: var(--el-color-warning);
  }

  &__foot,
  &__note {
    display: block;
    margin-top: 6px;
    font-size: 11px;
    color: var(--el-text-color-placeholder);
  }

  &__facts {
    display: flex;
    flex-direction: column;
    gap: 2px;
    padding-top: 4px;
    margin-top: 4px;
    font-size: 11px;
    color: var(--el-text-color-secondary);
    border-top: 1px solid var(--el-border-color-lighter);

    b {
      color: var(--el-color-primary);
    }
  }
}

// 展开的 SKU 明细里，把库存 < 阈值的尺码整行标出来
:deep(.goods-low-row) {
  background: color-mix(in srgb, var(--el-color-warning) 12%, transparent);

  td {
    background: transparent;
  }
}

.goods-stock {
  &--zero {
    font-weight: 600;
    color: var(--el-color-danger);
  }

  &--low {
    font-weight: 600;
    color: var(--el-color-warning);
  }

  &--ok {
    font-weight: 600;
    color: var(--el-color-success);
  }
}

@media (max-width: 992px) {
  .goods-pinbar {
    position: static;

    &__aside {
      align-items: flex-start;
    }
  }
}
</style>
