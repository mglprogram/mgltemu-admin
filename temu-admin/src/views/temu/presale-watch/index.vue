<!--
  修改库存 · 预售 / 清零（用户 2026-11-09 要求：TEMU 运营下的子节点）

  TEMU 两个页签（SHEIN 没有预售，只有清零）：
    预售中   —— 正在预售的「款号-尺码」（SPU / 补货日 / 在途量 / 开预售时间 / 剩余天数）
    已过预售 —— 开满 7 天自动进来的记录；同一 SKU 满 7 天可重新开预售 ⇒ 该行标「可清除」
  一级筛选：TEMU / SHEIN（平台）
     TEMU  → 店铺 BEL / BHY / BK
     SHEIN → 店铺 BS / BD / BAN / BE，状态页签隐藏（用户 2026-11-12：SHEIN 不存在预售，只有清零）

  ⚠ 2026-11-18 用户要求：表格改成「一行一个款号」，点左侧箭头展开看该款每个尺码
    （与「销量分析 · 加库存提醒」「在售款明细」同款设计）—— 原来是一行一个尺码，款多时看不清到底几款要处理。
    展开行里还是逐尺码的明细，字段与原来一致；款级行是合计 / 最紧张值。

  数据：public/data/presale-watch.json
    （scripts/goods/build_presale_watch.py 由开预售台账 data/presale-ledger.json
      + SHEIN 清零台账 data/shein-clear-ledger.json 生成）
-->
<template>
  <div class="pw-page app-container">
    <div class="pw-header">
      <div>
        <h2 class="pw-header__title">{{ isShein ? "修改库存 · 清零" : "修改库存 · 预售" }}</h2>
        <p class="pw-header__desc">
          <template v-if="isShein">
            SHEIN 半托管没有预售，只有清零：可售天数（仓内 ÷ 7 天日均）≤ 1 天 → 线上库存
            OVERWRITE 0。本页只列**现在线上仍为 0** 的「款号-变体-尺码」；已经补回去的
            （线上现值 > 0）由每轮 pipeline 对账后移出本页。「仓内可用」是清零当时的快照。
          </template>
          <template v-else>
            开预售满 {{ data.presaleDays }} 天自动进入「已过预售」；平台每 SKU
            {{ data.presaleDays }} 天内只允许开启一次，满期后该记录即「可清除」。
          </template>
        </p>
      </div>
      <div class="pw-header__meta">
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
      class="pw-alert"
      :title="loadError"
      description="先跑 python scripts/goods/build_presale_watch.py 生成 presale-watch.json"
    />

    <!--
      筛选分两行（用户 2026-11-10 要求：店铺按钮不要和 TEMU/SHEIN 并排）
        第一行：平台 TEMU / SHEIN
        第二行：店铺 全部 / (TEMU) BEL BHY BK (SHEIN) BS BD BAN BE  +  状态（仅 TEMU）  +  搜索
    -->
    <div class="pw-switch pw-switch--platform">
      <el-radio-group v-model="platform" size="default">
        <el-radio-button v-for="p in platformOptions" :key="p" :value="p">
          {{ p }}
          <span class="pw-switch__badge">{{ platformCount(p) }}</span>
        </el-radio-button>
      </el-radio-group>
    </div>

    <div class="pw-switch pw-switch--sub">
      <el-radio-group v-model="store" size="default">
        <el-radio-button value="">
          全部
          <span class="pw-switch__badge">{{ storeCount("") }}</span>
        </el-radio-button>
        <el-radio-button v-for="s in storeOptions" :key="s" :value="s">
          {{ isShein ? s : storeLabel(s) }}
          <span class="pw-switch__badge">{{ storeCount(s) }}</span>
        </el-radio-button>
      </el-radio-group>

      <el-radio-group v-if="!isShein" v-model="status" size="default">
        <!-- 用户 2026-11-13：按「在售款明细 + 领星总库存」算出的今日待办 -->
        <el-radio-button value="待处理">
          待处理 <span class="pw-switch__badge">{{ countPending() }}</span>
        </el-radio-button>
        <el-radio-button value="预售中">
          预售中 <span class="pw-switch__badge">{{ countOf("预售中") }}</span>
        </el-radio-button>
        <!-- 用户 2026-11-13：TEMU 也有「清零」记录，单独一个页签，别混进预售中 -->
        <el-radio-button value="已清零">
          已清零 <span class="pw-switch__badge">{{ countOf("已清零") }}</span>
        </el-radio-button>
        <el-radio-button value="已过预售">
          已过预售 <span class="pw-switch__badge">{{ countOf("已过预售") }}</span>
        </el-radio-button>
      </el-radio-group>

      <el-input
        v-model="keyword"
        :placeholder="isShein ? '款号 / 变体 / 商家SKU' : 'SPU / 货号'"
        clearable
        size="default"
        class="pw-switch__search"
      >
        <template #prefix><el-icon><Search /></el-icon></template>
      </el-input>
    </div>

    <el-row :gutter="12" class="pw-stats">
      <el-col v-for="s in stats" :key="s.label" :xs="12" :md="6">
        <el-card shadow="never" :body-style="{ padding: '14px 16px' }">
          <div class="pw-stat__label">{{ s.label }}</div>
          <div class="pw-stat__value" :style="{ color: s.color }">{{ s.value }}</div>
        </el-card>
      </el-col>
    </el-row>

    <!-- 汇总条：几款 / 几个尺码 + 一键展开收起 -->
    <div class="pw-summary">
      <span class="pw-summary__title">
        {{ isShein ? "清零明细" : "预售明细" }}：<b>{{ groups.length }}</b>
        {{ isShein ? " 个款-变体" : " 个款号" }} · <b>{{ rows.length }}</b> 个尺码
      </span>
      <span class="pw-hint">点每行左侧箭头展开看该款全部尺码</span>
      <el-button size="small" text type="primary" :disabled="!groups.length" @click="toggleExpandAll">
        {{ expandedAll ? "全部收起" : "全部展开" }}
      </el-button>
    </div>

    <!-- SHEIN：清零明细（一行一个款-变体，展开看每个尺码） -->
    <el-table
      v-if="isShein"
      v-loading="loading"
      :data="groups"
      border
      stripe
      size="small"
      row-key="key"
      :expand-row-keys="expandKeys"
      :row-class-name="groupRowClass"
      empty-text="没有符合条件的清零记录"
      class="pw-table"
      @expand-change="onExpandChange"
    >
      <el-table-column type="expand">
        <template #default="{ row }">
          <div class="pw-sizes">
            <div class="pw-sizes__title">
              <b>{{ row.code }}</b><template v-if="row.variant"> · {{ row.variant }}</template>
              · {{ row.sizes.length }} 个尺码
              <span class="pw-hint pw-hint--gap">点尺码行可复制货号</span>
            </div>
            <el-table :data="row.sizes" size="small" border class="pw-sizes__table">
              <el-table-column label="尺码" prop="size" width="66" align="center" />
              <el-table-column label="商家SKU" prop="seller" min-width="190" show-overflow-tooltip />
              <el-table-column label="平台SKU" prop="sku" width="130" />
              <el-table-column label="清零前线上" width="100" align="right">
                <template #default="{ row: s }">{{ s.before ?? "—" }}</template>
              </el-table-column>
              <el-table-column label="线上现值" width="100" align="right">
                <template #default="{ row: s }">
                  <span :style="{ color: (s.onlineNow ?? 0) > 0 ? '#67c23a' : undefined }">
                    {{ s.onlineNow ?? "—" }}
                  </span>
                </template>
              </el-table-column>
              <el-table-column label="仓内可用" width="90" align="right">
                <template #default="{ row: s }">{{ s.total ?? "—" }}</template>
              </el-table-column>
              <el-table-column label="7 天日均" width="90" align="right">
                <template #default="{ row: s }">{{ s.daily ? s.daily.toFixed(2) : "—" }}</template>
              </el-table-column>
              <el-table-column label="可售天数" width="90" align="right">
                <template #default="{ row: s }">{{ daysText(s.days) }}</template>
              </el-table-column>
              <el-table-column label="首次清零" prop="firstClearedAt" width="155" />
              <el-table-column label="最近清零" prop="clearedAt" width="155" />
              <el-table-column label="次数" width="66" align="right">
                <template #default="{ row: s }">{{ s.times ?? 1 }}</template>
              </el-table-column>
              <el-table-column label="状态" width="96">
                <template #default="{ row: s }">
                  <el-tag :type="statusTagType(s)" size="small">{{ s.status }}</el-tag>
                </template>
              </el-table-column>
              <el-table-column label="操作" width="96" align="center">
                <template #default="{ row: s }">
                  <el-button link type="primary" size="small" @click="copySize(s)">复制货号</el-button>
                </template>
              </el-table-column>
            </el-table>
          </div>
        </template>
      </el-table-column>

      <el-table-column label="店铺" prop="store" width="70" />
      <el-table-column label="货号（款号）" prop="code" min-width="120" />
      <el-table-column label="变体" prop="variant" width="90" />
      <el-table-column label="尺码" width="90" align="center">
        <template #default="{ row }">
          <span class="pw-sizecnt">{{ row.sizes.length }}<span class="pw-hint"> 个</span></span>
        </template>
      </el-table-column>
      <el-table-column label="线上现值" width="100" align="right">
        <template #default="{ row }">
          <span :style="{ color: (row.onlineNow ?? 0) > 0 ? '#67c23a' : undefined }">
            {{ row.onlineNow ?? "—" }}
          </span>
        </template>
      </el-table-column>
      <el-table-column label="仓内可用" width="100" align="right">
        <template #default="{ row }">{{ row.total ?? "—" }}</template>
      </el-table-column>
      <el-table-column label="7 天日均" width="100" align="right">
        <template #default="{ row }">{{ row.daily ? row.daily.toFixed(2) : "—" }}</template>
      </el-table-column>
      <el-table-column label="可售天数" width="100" align="right">
        <template #header>
          <span>可售天数</span>
          <el-tooltip placement="top" content="该款-变体里最紧张的那个尺码：仓内可用 ÷ 7 天日均。">
            <el-icon class="pw-th-hint"><QuestionFilled /></el-icon>
          </el-tooltip>
        </template>
        <template #default="{ row }">
          <span :class="{ 'pw-warn': (row.days ?? 99) <= 1 }">{{ daysText(row.days) }}</span>
        </template>
      </el-table-column>
      <el-table-column label="最近清零" prop="clearedAt" width="155" />
      <el-table-column label="次数" width="76" align="right">
        <template #header>
          <span>次数</span>
          <el-tooltip placement="top" content="该款-变体里被清零次数最多的那个尺码。">
            <el-icon class="pw-th-hint"><QuestionFilled /></el-icon>
          </el-tooltip>
        </template>
        <template #default="{ row }">{{ row.times || 1 }}</template>
      </el-table-column>
      <el-table-column label="状态" width="110">
        <template #default="{ row }">
          <el-tag :type="statusTagType(row)" size="small">{{ row.status }}</el-tag>
        </template>
      </el-table-column>
    </el-table>

    <!-- TEMU：预售明细（一行一个款号，展开看每个尺码） -->
    <el-table
      v-else
      v-loading="loading"
      :data="groups"
      border
      stripe
      size="small"
      row-key="key"
      :expand-row-keys="expandKeys"
      :row-class-name="groupRowClass"
      empty-text="没有符合条件的记录"
      class="pw-table"
      @expand-change="onExpandChange"
    >
      <el-table-column type="expand">
        <template #default="{ row }">
          <div class="pw-sizes">
            <div class="pw-sizes__title">
              <b>{{ row.code }}</b> · {{ row.sizes.length }} 个尺码<template v-if="row.spu">
                （SPU {{ row.spu }}）</template>
              <span class="pw-hint pw-hint--gap">点尺码行可复制货号</span>
            </div>
            <el-table :data="row.sizes" size="small" border class="pw-sizes__table">
              <el-table-column label="尺码" prop="size" width="64" align="center" />
              <el-table-column label="状态" width="96">
                <template #default="{ row: s }">
                  <el-tag :type="statusTagType(s)" size="small">{{ s.status }}</el-tag>
                </template>
              </el-table-column>
              <el-table-column label="在途量" width="90" align="right">
                <template #default="{ row: s }">
                  <el-tooltip placement="top" content="领星 LM739 的真实在途件数（不是预售填进去的量）">
                    <span>{{ s.transit ?? "—" }}</span>
                  </el-tooltip>
                </template>
              </el-table-column>
              <el-table-column label="在途预售库存" width="116" align="right">
                <template #default="{ row: s }">
                  <el-tooltip placement="top" content="开预售时填进弹窗的「在途预售库存」">
                    <span :class="{ 'pw-strong': (s.qty ?? 0) > 0 }">{{ s.qty ?? "—" }}</span>
                  </el-tooltip>
                </template>
              </el-table-column>
              <el-table-column label="TEMU 现货" width="104" align="right">
                <template #default="{ row: s }">
                  <el-tooltip
                    placement="top"
                    :content="`商品列表显示 ${s.temuStockRaw ?? '—'}（含在途预售 ${s.qty ?? 0}）→ 剔除预售后的现货 ${s.temuStockExPresale ?? '—'}`"
                  >
                    <span>{{ s.temuStockExPresale ?? "—" }}</span>
                  </el-tooltip>
                </template>
              </el-table-column>
              <el-table-column label="7 天日均" width="90" align="right">
                <template #default="{ row: s }">{{ s.daily ? s.daily.toFixed(2) : "—" }}</template>
              </el-table-column>
              <el-table-column label="补货日期" width="110">
                <template #default="{ row: s }">
                  <span v-if="s.date">{{ s.date }}</span>
                  <span v-else class="pw-hint">—</span>
                </template>
              </el-table-column>
              <el-table-column label="开预售时间" prop="openedAt" width="160" />
              <el-table-column label="已开天数" width="86" align="right">
                <template #default="{ row: s }">{{ s.ageDays ?? "—" }}</template>
              </el-table-column>
              <el-table-column label="说明" min-width="300" show-overflow-tooltip>
                <template #default="{ row: s }">
                  <span class="pw-hint">{{ s.planReason || "—" }}</span>
                </template>
              </el-table-column>
              <el-table-column label="操作" width="96" align="center">
                <template #default="{ row: s }">
                  <el-button link type="primary" size="small" @click="copySize(s)">复制货号</el-button>
                </template>
              </el-table-column>
            </el-table>
          </div>
        </template>
      </el-table-column>

      <el-table-column label="款号" width="250" fixed show-overflow-tooltip>
        <template #header>
          <span>款号</span>
          <el-tooltip placement="top" content="一行一个款号；点左侧箭头展开看该款每个尺码。">
            <el-icon class="pw-th-hint"><QuestionFilled /></el-icon>
          </el-tooltip>
        </template>
        <template #default="{ row }">
          <span class="pw-sku">
            <el-tag
              size="small"
              effect="plain"
              class="pw-sku__store"
              :type="row.store === 'BHY' ? 'primary' : 'warning'"
            >{{ row.store }}</el-tag>
            {{ row.code }}
          </span>
          <span v-if="row.spu" class="pw-sub">SPU {{ row.spu }}</span>
        </template>
      </el-table-column>
      <el-table-column label="尺码" width="86" align="center">
        <template #default="{ row }">
          <span class="pw-sizecnt">{{ row.sizes.length }}<span class="pw-hint"> 个</span></span>
        </template>
      </el-table-column>
      <el-table-column label="状态" width="190">
        <template #header>
          <span>状态</span>
          <el-tooltip placement="top" content="该款各尺码的状态分布（待开预售 / 待清零 / 预售中 / 已清零 / 已过预售）。">
            <el-icon class="pw-th-hint"><QuestionFilled /></el-icon>
          </el-tooltip>
        </template>
        <template #default="{ row }">
          <span class="pw-tags">
            <el-tag
              v-for="c in row.statusCounts"
              :key="c.label"
              :type="c.type"
              size="small"
              effect="light"
            >{{ c.label }} {{ c.count }}</el-tag>
          </span>
        </template>
      </el-table-column>
      <el-table-column label="在途量" width="90" align="right" sortable :sort-by="(r) => r.transit">
        <template #header>
          <span>在途量</span>
          <el-tooltip placement="top" content="该款各尺码在途件数合计（领星 LM739 真实在途，不是预售填进去的量）。">
            <el-icon class="pw-th-hint"><QuestionFilled /></el-icon>
          </el-tooltip>
        </template>
        <template #default="{ row }">{{ row.transit || "—" }}</template>
      </el-table-column>
      <el-table-column label="在途预售库存" width="112" align="right" sortable :sort-by="(r) => r.qty">
        <template #header>
          <span>在途预售库存</span>
          <el-tooltip placement="top" content="该款各尺码「在途预售库存」合计（开预售时填进弹窗的那个数）。">
            <el-icon class="pw-th-hint"><QuestionFilled /></el-icon>
          </el-tooltip>
        </template>
        <template #default="{ row }">
          <span :class="{ 'pw-strong': (row.qty || 0) > 0 }">{{ row.qty || "—" }}</span>
        </template>
      </el-table-column>
      <el-table-column label="补货日期" width="120" sortable :sort-by="(r) => r.date || '9999'">
        <template #header>
          <span>补货日期</span>
          <el-tooltip placement="top" content="该款最近的一批补货日（展开看每个尺码自己的日期）。">
            <el-icon class="pw-th-hint"><QuestionFilled /></el-icon>
          </el-tooltip>
        </template>
        <template #default="{ row }">
          <span v-if="row.date">{{ row.date }}</span>
          <span v-else class="pw-hint">—</span>
          <span v-if="row.dateDays != null" class="pw-sub">
            {{ row.dateDays < 0 ? `已过 ${-row.dateDays} 天` : `${row.dateDays} 天后` }}
          </span>
        </template>
      </el-table-column>
      <el-table-column label="TEMU 现货" width="110" align="right" sortable :sort-by="(r) => r.stock">
        <template #header>
          <span>TEMU 现货</span>
          <el-tooltip placement="top" content="该款各尺码剔除在途预售占用后的现货合计 = Σ max(0, 商品列表显示库存 − 在途预售库存)。">
            <el-icon class="pw-th-hint"><QuestionFilled /></el-icon>
          </el-tooltip>
        </template>
        <template #default="{ row }">
          <span :class="{ 'pw-zero': (row.stock || 0) <= 0 }">{{ row.stock }}</span>
        </template>
      </el-table-column>
      <el-table-column label="7 天日均" width="100" align="right" sortable :sort-by="(r) => r.daily">
        <template #header>
          <span>7 天日均</span>
          <el-tooltip placement="top" content="该款各尺码 7 天日均合计（展开看单个尺码）。">
            <el-icon class="pw-th-hint"><QuestionFilled /></el-icon>
          </el-tooltip>
        </template>
        <template #default="{ row }">{{ row.daily ? row.daily.toFixed(2) : "—" }}</template>
      </el-table-column>
      <el-table-column label="已开天数" width="96" align="right">
        <template #header>
          <span>已开天数</span>
          <el-tooltip placement="top" content="该款里开得最久的那个尺码（展开看每个尺码的开预售时间）。">
            <el-icon class="pw-th-hint"><QuestionFilled /></el-icon>
          </el-tooltip>
        </template>
        <template #default="{ row }">{{ row.ageDays ?? "—" }}</template>
      </el-table-column>
      <el-table-column label="可清除" width="90" align="center">
        <template #header>
          <span>可清除</span>
          <el-tooltip placement="top" content="该款里已开满 7 天、可以从台账清掉再重开的尺码数。">
            <el-icon class="pw-th-hint"><QuestionFilled /></el-icon>
          </el-tooltip>
        </template>
        <template #default="{ row }">
          <el-tag v-if="row.clearable" type="warning" size="small">可清除 {{ row.clearable }}</el-tag>
          <span v-else>—</span>
        </template>
      </el-table-column>
      <el-table-column label="商品标题" prop="title" min-width="260" show-overflow-tooltip />
    </el-table>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, ref, watch } from "vue";
import { Refresh, Search } from "@element-plus/icons-vue";
import PresaleWatchAPI, { type PresaleWatchData, type PresaleWatchItem } from "@/api/temu/presale-watch";

defineOptions({ name: "TemuPresaleWatch" });

const data = ref<PresaleWatchData>({
  generatedAt: "",
  presaleDays: 7,
  rule: { note: "", openedFrom: "" },
  counts: {},
  platforms: ["TEMU", "SHEIN"],
  stores: [],
  items: [],
});
const loading = ref(false);
const loadError = ref("");
const platform = ref("TEMU");
const store = ref("");
const status = ref<"预售中" | "已清零" | "已过预售" | "待处理">("待处理");
const keyword = ref("");

const isShein = computed(() => platform.value === "SHEIN");
const platformOptions = computed(() => data.value.platforms?.length ? data.value.platforms : ["TEMU", "SHEIN"]);

/**
 * 店铺筛选（用户 2026-11-10：TEMU 加 全部 / BEL / BHY / BK）
 * SHEIN 固定 BS / BD / BAN / BE（用户 2026-11-12 指定），不随数据有无而消失。
 */
// ⚠ 2026-11-18 改正：TEMU 真实三店是 BEL / BHY / KinglamanSport（KLS），
//   原来这里的 "BK" 是遗留标签，数据里根本没有这个店（按钮永远 0）。
const BASE_STORES = ["BEL", "BHY", "KinglamanSport"];

/** 店铺按钮上的显示名：KinglamanSport 太长，按钮里用 KLS（与其它脚本口径一致） */
const STORE_LABEL: Record<string, string> = {
  BEL: "BEL 店",
  BHY: "BHY 店",
  KinglamanSport: "KLS 店",
};
function storeLabel(s: string) {
  return STORE_LABEL[s] || s;
}
const SHEIN_STORES = ["BS", "BD", "BAN", "BE"];
const storeOptions = computed(() => {
  if (isShein.value) {
    const fromData = data.value.sheinStores ?? [];
    const extra = (data.value.stores ?? []).filter(
      (s) => !SHEIN_STORES.includes(s) && (data.value.items ?? []).some((x) => x.platform === "SHEIN" && x.store === s),
    );
    return [...(fromData.length ? fromData : SHEIN_STORES), ...extra];
  }
  // ⚠ 2026-11-12 用户反馈：TEMU 页签下出现了 SHEIN 的 BAN店/BD店 按钮（都是 0）。
  //   原因：这里用了全局 data.stores，而它混了 SHEIN 主体。
  //   ⇒ 只从**当前平台**的 items 里取店铺，TEMU 就是 BEL/BHY/BK + 数据里真出现的 TEMU 店。
  const temuInData = new Set(
    (data.value.items ?? []).filter((x) => x.platform === "TEMU").map((x) => x.store),
  );
  const extra = [...temuInData].filter((s) => s && !BASE_STORES.includes(s));
  return [...BASE_STORES, ...extra];
});

// 切平台时，如果当前选中的店铺不属于新平台（比如 TEMU 选了 BAN），自动回到「全部」
watch([platform, storeOptions], () => {
  if (store.value && !storeOptions.value.includes(store.value)) store.value = "";
});

/**
 * 已开天数 / 状态在前端**按当前时间实时算**。
 * ⚠ 2026-11-12 用户反馈「已开天数没有随天数累加」：原来直接显示 JSON 里的 ageDays，
 *   而 JSON 是每天定时重建的，两次重建之间（或数据还没重跑时）天数就停在旧值。
 *   这里改成用 openedAt 现算：
 *     · 已开天数 = 自然日差（今天日期 − 开预售日期），11-10 开 → 11-12 显示 2
 *     · 已过预售 = 开满 presaleDays × 24 小时（平台按满 7 天锁，不能提前清）
 *   SHEIN 的清零记录不参与这套推算：状态固定「已清零」，只按 clearedAt 显示时间。
 */
function parseOpened(s?: string): number | null {
  if (!s) return null;
  const t = Date.parse(String(s).replace(" ", "T"));
  return Number.isFinite(t) ? t : null;
}

/** 自然日差：按本地零点算，避免「差 1 小时少 1 天」 */
function ageDaysOf(openedAt?: string): number | null {
  const t = parseOpened(openedAt);
  if (t == null) return null;
  const a = new Date(t);
  const n = new Date();
  const d0 = new Date(a.getFullYear(), a.getMonth(), a.getDate()).getTime();
  const d1 = new Date(n.getFullYear(), n.getMonth(), n.getDate()).getTime();
  return Math.max(0, Math.round((d1 - d0) / 86400000));
}

/** 已开小时数（决定是否满 7 天可重开） */
function ageHoursOf(openedAt?: string): number | null {
  const t = parseOpened(openedAt);
  return t == null ? null : (Date.now() - t) / 3600000;
}

/** 「2026-11-20」离今天还有几天（负数 = 已过） */
function daysUntilDate(d?: string): number | null {
  const t = parseOpened(d);
  if (t == null) return null;
  const a = new Date(t);
  const n = new Date();
  const d0 = new Date(a.getFullYear(), a.getMonth(), a.getDate()).getTime();
  const d1 = new Date(n.getFullYear(), n.getMonth(), n.getDate()).getTime();
  return Math.round((d0 - d1) / 86400000);
}

function daysText(v?: number | null) {
  return v == null || Number.isNaN(Number(v)) ? "—" : Number(v).toFixed(2);
}

/**
 * 状态标签配色（款级分布 / 尺码行共用）
 *   待清零 / 已清零 → 红   待开预售 → 橙   预售中 → 绿   已过预售 → 灰
 */
function statusType(s?: string): "success" | "warning" | "danger" | "info" {
  if (s === "预售中") return "success";
  if (s === "待开预售" || String(s ?? "").startsWith("待")) return "warning";
  if (s === "已清零" || s === "待清零") return "danger";
  return "info";
}

/**
 * SHEIN 清零记录的状态标签：
 *   已清零 —— 线上现在仍是 0（这一页只放这种）
 *   未核对 —— 台账还没跑过对账（onlineNow 缺失）；线上>0 的记录后端已过滤掉，不展示
 */
function statusTagType(row: PresaleWatchItem | any) {
  if (row && row.platform === "TEMU") return statusType(row.status);
  return row.onlineNow == null ? "info" : "danger";
}

const base = computed<PresaleWatchItem[]>(() => {
  const limit = (data.value.presaleDays ?? 7) * 24;
  return (data.value.items ?? []).map((x) => {
    if (x.platform === "SHEIN") return x; // 清零记录：状态/天数都由台账给定
    // ⚠ 2026-11-13 用户指正：TEMU 的「已清零」记录被这里强行改成了「预售中」
    //   （207220-4SetA-XXL 明明是清零，却挂在预售中；202504-5SetA 也因此重复出现）。
    //   清零记录没有开预售时间，状态原样保留，不参与 7 天到期计算。
    if (x.status === "已清零" || !x.openedAt) return x;   // 清零/待办行：原样返回
    const h = ageHoursOf(x.openedAt);
    const expired = h == null ? x.status === "已过预售" : h >= limit;
    return {
      ...x,
      ageDays: ageDaysOf(x.openedAt) ?? x.ageDays,
      status: (expired ? "已过预售" : "预售中") as PresaleWatchItem["status"],
      clearable: expired,
    };
  });
});

function matchKeyword(x: PresaleWatchItem) {
  const k = keyword.value.trim().toLowerCase();
  if (!k) return true;
  return `${x.spu ?? ""} ${x.code} ${x.variant ?? ""} ${x.seller ?? ""}`.toLowerCase().includes(k);
}

function matchScope(x: PresaleWatchItem) {
  if (x.platform !== platform.value) return false;
  if (store.value && x.store !== store.value) return false;
  return matchKeyword(x);
}

/**
 * 状态页签是否命中。
 * ⚠ 2026-11-18 顺手修掉的老问题：「待处理」不是数据里的真实状态（真实状态是 待开预售 / 待清零），
 *   原来用 `x.status !== status.value` 直接比，选中「待处理」时各店铺按钮全是 0，
 *   跟页签上的 83 对不上。这里改成前缀匹配，三个计数（平台 / 店铺 / 状态）口径一致。
 */
function matchStatusTab(x: PresaleWatchItem) {
  if (isShein.value) return true;
  if (status.value === "待处理") return String(x.status).startsWith("待");
  return x.status === status.value;
}

/** 平台按钮上的条数：只看平台本身（状态页签是选完平台之后的二级筛选，别跟着变） */
function platformCount(p: string) {
  return base.value.filter((x) => x.platform === p).length;
}

/** 店铺按钮上的条数：按当前平台 + 当前状态页签 + 搜索词算 */
function storeCount(s: string) {
  return base.value.filter((x) => {
    if (x.platform !== platform.value) return false;
    if (s && x.store !== s) return false;
    return matchStatusTab(x) && matchKeyword(x);
  }).length;
}

function countOf(s: "预售中" | "已清零" | "已过预售") {
  return base.value.filter((x) => x.status === s && matchScope(x)).length;
}

/** 「待处理」= 待开预售 + 待清零 */
function countPending() {
  return base.value.filter((x) => String(x.status).startsWith("待") && matchScope(x)).length;
}

/** 当前筛选下的**尺码级**数据（表格改成款级后，它只用于聚合与汇总条） */
const rows = computed(() => base.value.filter((x) => matchScope(x) && matchStatusTab(x)));

// ---------------------------------------------------------------- 款级聚合
// ⚠ 2026-11-18 用户要求：与「销量分析 · 加库存提醒」一样，一行一个款号 + 可展开。
//   聚合键：TEMU = 店铺|款号；SHEIN = 店铺|货号|变体（同货号不同变体是两块 SKC，不能合并）。
//   款级数字：件数相关取**合计**（在途/预售/现货/日均），天数取**最紧张**的（越小越急），
//   日期取**最近**的一批。这样一眼能看出「这款要不要动」，细节点开看。

type GroupStatus = { label: string; count: number; type: "success" | "warning" | "danger" | "info" };

interface StyleGroup {
  key: string;
  platform: string;
  store: string;
  code: string;
  variant: string;
  sizes: PresaleWatchItem[];
  statusCounts: GroupStatus[];
  /** 排序权重：越小越急（待清零 < 待开预售 < 预售中 < 已清零 < 已过预售） */
  rank: number;
  // —— TEMU ——
  qty: number;
  transit: number;
  date: string;
  dateDays: number | null;
  stock: number;
  daily: number;
  ageDays: number | null;
  clearable: number;
  spu: string;
  title: string;
  // —— SHEIN ——
  onlineNow: number | null;
  hasOnline: boolean;
  total: number | null;
  hasTotal: boolean;
  days: number | null;
  firstClearedAt: string;
  clearedAt: string;
  times: number;
  status: string;
}

const STATUS_RANK: Record<string, number> = {
  待清零: 0, 待开预售: 1, 预售中: 2, 已清零: 3, 已过预售: 4,
};

/** 尺码排序：S / M / L / XL / XXL 按常规顺序，认不出的排最后 */
const SIZE_ORDER = ["XXS", "XS", "S", "M", "L", "XL", "XXL", "XXXL", "3XL", "4XL", "5XL"];
function sizeRank(s?: string) {
  const i = SIZE_ORDER.indexOf(String(s ?? "").toUpperCase());
  return i < 0 ? 99 : i;
}

function minD0(g: StyleGroup) {
  let v = 9999;
  for (const s of g.sizes) {
    const d = s.d0;
    if (d != null && Number.isFinite(Number(d))) v = Math.min(v, Number(d));
  }
  return v;
}

const groups = computed<StyleGroup[]>(() => {
  const map = new Map<string, StyleGroup>();
  for (const r of rows.value) {
    const variant = String(r.variant ?? "");
    const key = isShein.value ? `${r.store}|${r.code}|${variant}` : `${r.store}|${r.code}`;
    let g = map.get(key);
    if (!g) {
      g = {
        key,
        platform: r.platform,
        store: r.store,
        code: r.code,
        variant,
        sizes: [],
        statusCounts: [],
        rank: 9,
        qty: 0,
        transit: 0,
        date: "",
        dateDays: null,
        stock: 0,
        daily: 0,
        ageDays: null,
        clearable: 0,
        spu: String(r.spu ?? ""),
        title: r.title || "",
        onlineNow: null,
        hasOnline: false,
        total: null,
        hasTotal: false,
        days: null,
        firstClearedAt: "",
        clearedAt: "",
        times: 0,
        status: r.status,
      };
      map.set(key, g);
    }
    g.sizes.push(r);

    // 件数合计
    g.qty += Number(r.qty ?? 0);
    g.transit += Number(r.transit ?? 0);
    g.stock += Number(r.temuStockExPresale ?? r.temuStock ?? 0);
    g.daily += Number(r.daily ?? 0);
    if (r.clearable) g.clearable += 1;

    // 补货日期：取最近的一批
    if (r.date) {
      if (!g.date || r.date < g.date) {
        g.date = r.date;
        g.dateDays = daysUntilDate(r.date);
      }
    }
    // 已开天数：取开得最久的那个（ageDays 缺失时用 openedAt 现算）
    const age = r.ageDays ?? ageDaysOf(r.openedAt);
    if (age != null && Number.isFinite(Number(age))) {
      g.ageDays = g.ageDays == null ? Number(age) : Math.max(g.ageDays, Number(age));
    }

    // SHEIN 清零口径：线上现值 / 仓内可用 / 可售天数（取最紧张）/ 首次清零取最早 / 最近清零取最晚 / 次数取最大
    if (r.onlineNow != null) {
      g.hasOnline = true;
      g.onlineNow = Number(g.onlineNow ?? 0) + Number(r.onlineNow);
    }
    if (r.total != null) {
      g.hasTotal = true;
      g.total = Number(g.total ?? 0) + Number(r.total);
    }
    if (r.days != null && Number.isFinite(Number(r.days))) {
      g.days = g.days == null ? Number(r.days) : Math.min(g.days, Number(r.days));
    }
    if (r.firstClearedAt && (!g.firstClearedAt || r.firstClearedAt < g.firstClearedAt))
      g.firstClearedAt = r.firstClearedAt;
    if (r.clearedAt && (!g.clearedAt || r.clearedAt > g.clearedAt)) g.clearedAt = r.clearedAt;
    g.times = Math.max(g.times, Number(r.times ?? 1));
    g.rank = Math.min(g.rank, STATUS_RANK[r.status] ?? 9);
  }

  for (const g of map.values()) {
    g.sizes.sort((a, b) => sizeRank(a.size) - sizeRank(b.size));
    const cnt = new Map<string, number>();
    for (const s of g.sizes) cnt.set(s.status, (cnt.get(s.status) || 0) + 1);
    g.statusCounts = [...cnt.entries()]
      .map(([label, count]) => ({ label, count, type: statusType(label) }))
      .sort((a, b) => (STATUS_RANK[a.label] ?? 9) - (STATUS_RANK[b.label] ?? 9));
    // 主状态取最急的那个（状态分布列里第一个就是它）
    g.status = g.statusCounts[0]?.label || g.status;
    if (!g.clearedAt) g.clearedAt = g.firstClearedAt;
  }

  return [...map.values()].sort(
    (a, b) =>
      a.rank - b.rank ||
      minD0(a) - minD0(b) ||
      a.code.localeCompare(b.code) ||
      a.store.localeCompare(b.store),
  );
});

/** 款行底色：待清零 → 红；待开预售 → 橙；其余不染色 */
function groupRowClass({ row }: { row: StyleGroup }) {
  if (row.status === "待清零") return "pw-row-danger";
  if (row.status === "待开预售") return "pw-row-warn";
  return "";
}

// ---------------------------------------------------------------- 展开 / 收起
const expandKeys = ref<string[]>([]);

function onExpandChange(row: StyleGroup, expanded: StyleGroup[] | boolean) {
  const open = Array.isArray(expanded)
    ? expanded.some((r) => r?.key === row.key)
    : Boolean(expanded);
  const set = new Set(expandKeys.value);
  if (open) set.add(row.key);
  else set.delete(row.key);
  expandKeys.value = [...set];
}

const expandedAll = computed(
  () => groups.value.length > 0 && groups.value.every((g) => expandKeys.value.includes(g.key)),
);

function toggleExpandAll() {
  expandKeys.value = expandedAll.value ? [] : groups.value.map((g) => g.key);
}

// ---------------------------------------------------------------- 复制货号
async function writeClipboard(text: string, tip: string) {
  try {
    await navigator.clipboard.writeText(text);
    ElMessage.success(tip);
  } catch {
    const ta = document.createElement("textarea");
    ta.value = text;
    ta.style.position = "fixed";
    ta.style.opacity = "0";
    document.body.appendChild(ta);
    ta.select();
    try {
      document.execCommand("copy");
      ElMessage.success(tip);
    } catch {
      ElMessage.error("复制失败，请手动选择");
    }
    document.body.removeChild(ta);
  }
}

/** 复制单个尺码的货号（TEMU 是 款号-尺码，SHEIN 是 商家SKU）。插槽给的是 DefaultRow，按 any 接住 */
function copySize(s: PresaleWatchItem | any) {
  const text = s.seller || (s.size ? `${s.code}-${s.size}` : s.code);
  return writeClipboard(text, `已复制 ${text}`);
}

function todayStr() {
  const n = new Date();
  const p = (v: number) => String(v).padStart(2, "0");
  return `${n.getFullYear()}-${p(n.getMonth() + 1)}-${p(n.getDate())}`;
}

const stats = computed(() => {
  const scoped = base.value.filter(matchScope);
  if (isShein.value) {
    const today = todayStr();
    return [
      { label: "已清零（线上仍为 0）", value: scoped.length, color: "#f56c6c" },
      {
        label: "今日清零",
        value: scoped.filter((x) => String(x.clearedAt ?? "").startsWith(today)).length,
        color: "#e6a23c",
      },
      { label: "涉及款数", value: new Set(scoped.map((x) => x.code)).size, color: "" },
      { label: "涉及店铺", value: new Set(scoped.map((x) => x.store)).size, color: "#909399" },
    ];
  }
  return [
    { label: "预售中（尺码）", value: scoped.filter((x) => x.status === "预售中").length, color: "#67c23a" },
    { label: "已过预售", value: scoped.filter((x) => x.status === "已过预售").length, color: "#909399" },
    { label: "可清除（满 7 天可重开）", value: scoped.filter((x) => x.clearable).length, color: "#e6a23c" },
    { label: "涉及款数", value: new Set(scoped.map((x) => x.code)).size, color: "" },
  ];
});

async function load() {
  loading.value = true;
  loadError.value = "";
  try {
    data.value = await PresaleWatchAPI.getPresaleWatchData();
  } catch (e: any) {
    loadError.value = e?.message || "加载 presale-watch.json 失败";
  } finally {
    loading.value = false;
  }
}

onMounted(load);
</script>

<style scoped>
.pw-page { padding-bottom: 32px; }
.pw-header { display: flex; justify-content: space-between; align-items: flex-start; margin-bottom: 12px; }
.pw-header__title { margin: 0; font-size: 20px; }
.pw-header__desc { margin: 6px 0 0; color: var(--el-text-color-secondary); font-size: 13px; }
.pw-header__meta { display: flex; gap: 8px; align-items: center; }
.pw-alert { margin-bottom: 12px; }
.pw-switch { display: flex; flex-wrap: wrap; gap: 12px; align-items: center; }
/* 第一行只有 TEMU / SHEIN，第二行放店铺+状态+搜索（用户 2026-11-10 要求分两行） */
.pw-switch--platform { margin-bottom: 8px; }
.pw-switch--sub { margin-bottom: 12px; }
.pw-switch__badge { margin-left: 6px; opacity: 0.75; }
.pw-switch__search { width: 220px; }
.pw-stats { margin-bottom: 12px; }
.pw-stat__label { color: var(--el-text-color-secondary); font-size: 13px; }
.pw-stat__value { font-size: 24px; font-weight: 600; margin-top: 4px; }

/* 汇总条：几款 / 几个尺码 + 一键展开 */
.pw-summary {
  display: flex;
  flex-wrap: wrap;
  gap: 10px;
  align-items: center;
  margin-bottom: 8px;
}
.pw-summary__title { font-size: 13px; color: var(--el-text-color-regular); }

.pw-hint { font-size: 12px; color: var(--el-text-color-secondary); }
.pw-hint--gap { margin-left: 8px; }
.pw-sub { display: block; margin-top: 1px; font-size: 11px; line-height: 1.25; color: var(--el-text-color-secondary); }
.pw-strong { font-weight: 700; color: var(--el-color-primary); }
.pw-warn { font-weight: 600; color: var(--el-color-warning); }
.pw-zero { font-weight: 600; color: var(--el-color-danger); }
.pw-sizecnt { font-weight: 600; color: var(--el-color-primary); }
.pw-th-hint { margin-left: 2px; font-size: 12px; color: var(--el-text-color-placeholder); vertical-align: -2px; }
.pw-tags { display: inline-flex; flex-wrap: wrap; gap: 3px; align-items: center; }
.pw-sku { font-variant-numeric: tabular-nums; }
.pw-sku__store { margin-right: 4px; vertical-align: 1px; }

:deep(.pw-row-danger) { --el-table-tr-bg-color: var(--el-color-danger-light-9); }
:deep(.pw-row-warn) { --el-table-tr-bg-color: var(--el-color-warning-light-9); }

/* 展开行：该款全部尺码 */
.pw-sizes { padding: 8px 12px 12px 48px; background: var(--el-fill-color-lighter); }
.pw-sizes__title { margin-bottom: 8px; font-size: 12px; color: var(--el-text-color-regular); }
.pw-sizes__table { width: 100%; }
</style>
