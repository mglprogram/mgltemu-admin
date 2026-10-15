<!--
  TEMU 运营 · 订单分布（美国州/市）

  数据链路（2026-10-14 建）：
    帕沃尔海外仓 powerwarehouse.net
      · 发货列表 deliver.php?act=list&pb=all  → 按「店铺ID + 时间」筛出两店(BEL/BHY)全部出库单
      · 每单 deliver.php?act=order_info&id=<出库单id> → 「交货信息」里的 收件地址/邮编
    采集 + 聚合脚本：scripts/pw_orders/run_pw_order_geo.py
    产物：public/data/order-geo.json（前端直接 fetch，不走后端）

  ⚠ 口径：
    · 一「单」= 帕沃尔的一个出库单（= TEMU 的一个包裹），不是 SKU 行；
    · 州名用全名（与 ECharts 美国地图的 name 对齐），另附两字母缩写；
    · 城市可能带 * 号——帕沃尔对部分订单的城市做了脱敏（州名始终完整）。
-->
<template>
  <div class="geo-page app-container">
    <!-- 页头 -->
    <div class="geo-header">
      <div>
        <h2 class="geo-header__title">订单分布 · 美国州/市</h2>
        <p class="geo-header__desc">
          两个 TEMU 本土店（BEL / BHY）在帕沃尔海外仓的出库单，按收件州-市汇总 —— 看主要出单州，决定 ad
          投放/备货偏哪边
        </p>
      </div>
      <div class="geo-header__meta">
        <el-tag v-if="data?.generatedAt" type="info" effect="plain" size="small">
          数据生成 {{ data.generatedAt }}
        </el-tag>
        <el-tag v-if="data?.range" type="success" effect="plain" size="small">
          {{ data.range.start }} ~ {{ data.range.end }}
        </el-tag>
        <el-button :loading="loading" @click="load">
          <el-icon><Refresh /></el-icon><span>刷新</span>
        </el-button>
      </div>
    </div>

    <el-alert
      v-if="data"
      type="info"
      :closable="false"
      show-icon
      class="geo-alert"
      title="口径"
    >
      <div>
        一「单」= 帕沃尔一个出库单（一个包裹）· 州名全称 · 城市可能带 <b>*</b> 号（帕沃尔对部分订单的城市脱敏，
        州名始终完整）· 产品取自列表页 <b>[详情]</b> 弹窗（<code>act=order_info&amp;type=view</code>），
        <b>一单可能挂多个产品</b>（实测 10.9%，帕沃尔自带导出的 Excel 一单只出一个产品，不能用）·
        「款」= 产品名去掉末尾尺码
      </div>
      <div v-if="data.sourceNote" class="geo-source-note">
        <el-tag size="small" type="success" effect="plain">并来源</el-tag>
        <span>{{ data.sourceNote }}</span>
      </div>
    </el-alert>

    <el-skeleton v-if="!data && loading" :rows="8" animated />

    <el-empty v-else-if="!data" description="还没有订单分布数据（public/data/order-geo.json 未生成）">
      <el-button type="primary" @click="load">重新加载</el-button>
    </el-empty>

    <template v-else>
      <!-- 店铺切换 -->
      <div class="geo-bar">
        <el-radio-group v-model="storeKey" size="small">
          <el-radio-button v-for="t in tabs" :key="t.key" :value="t.key">
            {{ t.label }}<span class="geo-bar__num">（{{ t.count }} 单）</span>
          </el-radio-button>
        </el-radio-group>
        <span class="geo-bar__hint">
          已解析出州的订单 {{ num(cur.geoOrders) }} / {{ num(cur.orders) }}
          <template v-if="missCount">（{{ num(missCount) }} 单未拿到地址）</template>
        </span>
      </div>

      <!-- 概览 -->
      <el-row :gutter="12" class="geo-stats">
        <el-col v-for="s in stats" :key="s.label" :xs="12" :sm="12" :md="6">
          <el-card shadow="never" :body-style="{ padding: '14px 16px' }">
            <div class="geo-stat__label">{{ s.label }}</div>
            <div class="geo-stat__value" :style="{ color: s.color }">{{ s.value }}</div>
            <div class="geo-stat__hint">{{ s.hint }}</div>
          </el-card>
        </el-col>
      </el-row>

      <!-- 地图 + Top 州 -->
      <el-card shadow="never" class="geo-card">
        <template #header>
          <div class="geo-card__head">
            <span class="geo-card__title">订单分布 · 美国地图</span>
            <span class="geo-card__sub">颜色越亮 = 出单越多 · 悬停看「单量 + 该州畅销款 Top3」</span>
          </div>
        </template>
        <div class="geo-map-wrap">
          <div ref="mapRef" class="geo-map" />
          <div class="geo-rank">
            <div class="geo-rank__head">
              <span>主要出单州 Top 10</span>
              <span class="geo-rank__pct">占比</span>
            </div>
            <div v-for="(s, i) in cur.states.slice(0, 10)" :key="s.code" class="geo-rank__row">
              <span class="geo-rank__idx" :class="{ 'is-top': i < 3 }">{{ i + 1 }}</span>
              <span class="geo-rank__name">{{ s.name }}</span>
              <span class="geo-rank__bar">
                <i :style="{ width: barWidth(s), background: barColor(i) }" />
              </span>
              <span class="geo-rank__num font-din">{{ num(s.orders) }}</span>
              <span class="geo-rank__share font-din">{{ pct(s.share) }}</span>
            </div>
          </div>
        </div>
      </el-card>

      <!-- 产品查询（用户 2026-10-14 要求：具体每个产品的出单在这儿查） -->
      <el-card shadow="never" class="geo-card geo-query">
        <template #header>
          <div class="geo-card__head">
            <span class="geo-card__title">产品出单查询</span>
            <span class="geo-card__sub">
              搜款号 / 数字短码 / Fnsku（如 <b>102105</b>、<b>BH-102105-3BHL</b>、<b>H430353826</b>）
              · 覆盖全部 {{ productIndex?.productCount || 0 }} 个款
            </span>
          </div>
        </template>

        <div class="geo-query__bar">
          <el-input
            v-model="query"
            :placeholder="indexReady ? '输入款号 / 数字短码 / Fnsku' : '产品索引加载中…'"
            :disabled="!indexReady"
            clearable
            size="large"
            class="geo-query__input"
          >
            <template #prefix><el-icon><Search /></el-icon></template>
          </el-input>
          <el-tag v-if="query" type="info" effect="plain" size="small">
            命中 {{ queryHits.length }} 个款
          </el-tag>
          <span v-if="!query" class="geo-hint">
            出单最多的款：
            <el-link
              v-for="p in hotProducts"
              :key="p.name"
              type="primary"
              :underline="false"
              class="geo-query__hot"
              @click="pick(p.name)"
            >
              {{ p.code }}
            </el-link>
          </span>
        </div>

        <!-- 命中列表 -->
        <el-table
          v-if="query && !selected"
          :data="queryHits.slice(0, 50)"
          size="small"
          stripe
          max-height="320"
          highlight-current-row
          @row-click="(row: GeoProductDetail) => pick(row.name)"
        >
          <el-table-column prop="name" label="款号" min-width="180" />
          <el-table-column prop="code" label="数字码" width="90" />
          <el-table-column prop="orders" label="单量" width="90" align="right" sortable>
            <template #default="{ row }">{{ num(row.orders) }}</template>
          </el-table-column>
          <el-table-column label="头号州" min-width="140">
            <template #default="{ row }">{{ row.topState }} ({{ row.topStateCode }})</template>
          </el-table-column>
          <el-table-column label="集中度" width="100" align="center">
            <template #default="{ row }">
              <el-tag :type="liftTag(row.lift)" size="small" effect="plain">x{{ row.lift }}</el-tag>
            </template>
          </el-table-column>
          <el-table-column label="Fnsku" min-width="140">
            <template #default="{ row }">
              <span class="geo-hint">{{ (row.fnskus || []).slice(0, 2).join(" / ") || "—" }}</span>
            </template>
          </el-table-column>
        </el-table>
        <el-empty
          v-else-if="query && !queryHits.length"
          :image-size="60"
          description="没有匹配的款（试试数字短码，如 102105）"
        />

        <!-- 某一款的明细 -->
        <template v-if="selected">
          <div class="geo-detail__head">
            <div>
              <span class="geo-detail__code">{{ selected.code }}</span>
              <span class="geo-detail__name">{{ selected.name }}</span>
              <el-tag
                v-for="f in selected.fnskus"
                :key="f"
                size="small"
                effect="plain"
                type="info"
                class="geo-code"
              >
                {{ f }}
              </el-tag>
            </div>
            <el-button link type="primary" @click="selected = null">返回列表</el-button>
          </div>

          <el-row :gutter="12" class="geo-detail__stats">
            <el-col :xs="12" :sm="8" :md="4">
              <div class="geo-stat__label">单量</div>
              <div class="geo-stat__value font-din">{{ num(selected.orders) }}</div>
            </el-col>
            <el-col :xs="12" :sm="8" :md="4">
              <div class="geo-stat__label">件数</div>
              <div class="geo-stat__value font-din">{{ num(selected.qty) }}</div>
            </el-col>
            <el-col :xs="12" :sm="8" :md="4">
              <div class="geo-stat__label">占大盘</div>
              <div class="geo-stat__value font-din">{{ pct(selected.share) }}</div>
            </el-col>
            <el-col :xs="12" :sm="8" :md="4">
              <div class="geo-stat__label">覆盖州数</div>
              <div class="geo-stat__value font-din">{{ selected.states.length }}</div>
            </el-col>
            <el-col :xs="12" :sm="8" :md="4">
              <div class="geo-stat__label">头号州</div>
              <div class="geo-stat__value" style="font-size: 18px">
                {{ selected.topState }} ({{ selected.topStateCode }})
              </div>
              <div class="geo-stat__hint">占该款 {{ pct(selected.topStateShare) }}</div>
            </el-col>
            <el-col :xs="12" :sm="8" :md="4">
              <div class="geo-stat__label">地域集中度</div>
              <div class="geo-stat__value">
                <el-tag :type="liftTag(selected.lift)" size="large" effect="plain">
                  x{{ selected.lift }}
                </el-tag>
              </div>
              <div class="geo-stat__hint">≥3 ≈ 专供某州</div>
            </el-col>
          </el-row>

          <el-row :gutter="12">
            <el-col :xs="24" :md="14">
              <div class="geo-detail__sub">州分布（全部 {{ selected.states.length }} 个州）</div>
              <el-table :data="selected.states" size="small" stripe max-height="360">
                <el-table-column label="#" type="index" width="52" />
                <el-table-column prop="name" label="州" min-width="130" />
                <el-table-column prop="code" label="缩写" width="70" />
                <el-table-column prop="orders" label="单量" width="90" align="right" sortable>
                  <template #default="{ row }">{{ num(row.orders) }}</template>
                </el-table-column>
                <el-table-column label="占该款" width="110" align="right" sortable prop="share">
                  <template #default="{ row }">{{ pct(row.share) }}</template>
                </el-table-column>
                <el-table-column label="累计" width="90" align="right">
                  <template #default="{ row }">{{ pct(cumShare(selected!.states, row)) }}</template>
                </el-table-column>
              </el-table>
            </el-col>
            <el-col :xs="24" :md="10">
              <div class="geo-detail__sub">月度出单</div>
              <div ref="queryChartRef" class="geo-query__chart" />
              <div class="geo-detail__sub">城市 Top8</div>
              <el-table :data="selected.cities" size="small" max-height="200">
                <el-table-column prop="name" label="城市" min-width="130" />
                <el-table-column prop="code" label="州" width="70" />
                <el-table-column prop="orders" label="单量" width="90" align="right">
                  <template #default="{ row }">{{ num(row.orders) }}</template>
                </el-table-column>
              </el-table>
            </el-col>
          </el-row>
        </template>
      </el-card>

      <!-- 各州畅销款 -->
      <el-card shadow="never" class="geo-card">
        <template #header>
          <div class="geo-card__head">
            <span class="geo-card__title">各州畅销款 Top3</span>
            <span class="geo-card__sub">
              只显示款号里的数字（全名见下方「款 · 州分布」表）· 按该州单量排 · 已去重
            </span>
          </div>
        </template>
        <el-table :data="stateTopProductRows" size="small" stripe max-height="420">
          <el-table-column label="#" type="index" width="52" />
          <el-table-column prop="state" label="州" min-width="130">
            <template #default="{ row }">{{ row.state }} ({{ row.code }})</template>
          </el-table-column>
          <el-table-column prop="orders" label="该州单量" width="100" align="right" sortable>
            <template #default="{ row }">{{ num(row.orders) }}</template>
          </el-table-column>
          <el-table-column label="畅销款 Top3" min-width="200">
            <template #default="{ row }">
              <el-tag
                v-for="(c, i) in row.codes"
                :key="c"
                :type="i === 0 ? 'warning' : 'info'"
                size="small"
                effect="plain"
                class="geo-code"
              >
                {{ c }}
              </el-tag>
            </template>
          </el-table-column>
          <el-table-column label="头号款单量" width="130" align="right">
            <template #default="{ row }">
              <span class="font-din">{{ num(row.topProductOrders) }}</span>
              <span class="geo-hint"> · {{ pct(row.topProductShare) }}</span>
            </template>
          </el-table-column>
          <el-table-column label="头号款全名" min-width="190">
            <template #default="{ row }">
              <span class="geo-prod">{{ row.topProduct }}</span>
            </template>
          </el-table-column>
        </el-table>
      </el-card>

      <!-- 款 × 州（地域集中度） -->
      <el-card shadow="never" class="geo-card">
        <template #header>
          <div class="geo-card__head">
            <span class="geo-card__title">款 · 州分布与地域集中度（Top {{ curProducts.length }}）</span>
            <span class="geo-card__sub">
              「款」= 产品名去掉尺码 · 集中度 x≥3 ≈ 专供某州 · 点行展开该款的州分布
            </span>
          </div>
        </template>
        <el-table
          :data="curProducts"
          size="small"
          stripe
          max-height="460"
          :default-sort="{ prop: 'orders', order: 'descending' }"
        >
          <el-table-column type="expand">
            <template #default="{ row }">
              <div class="geo-cities">
                <el-tag
                  v-for="s in row.states"
                  :key="s.code"
                  size="small"
                  effect="plain"
                  class="geo-cities__tag"
                >
                  {{ s.name }} · {{ num(s.orders) }}（{{ pct(s.share) }}）
                </el-tag>
              </div>
            </template>
          </el-table-column>
          <el-table-column prop="name" label="款" min-width="170" sortable />
          <el-table-column prop="orders" label="单量" width="100" align="right" sortable>
            <template #default="{ row }">{{ num(row.orders) }}</template>
          </el-table-column>
          <el-table-column label="占大盘" width="100" align="right" sortable prop="share">
            <template #default="{ row }">{{ pct(row.share) }}</template>
          </el-table-column>
          <el-table-column label="头号州" min-width="150" sortable prop="topState">
            <template #default="{ row }">
              {{ row.topState }} ({{ row.topStateCode }})
            </template>
          </el-table-column>
          <el-table-column label="头号州占比" width="120" align="right" sortable prop="topStateShare">
            <template #default="{ row }">{{ pct(row.topStateShare) }}</template>
          </el-table-column>
          <el-table-column label="集中度" width="110" align="center" sortable prop="lift">
            <template #default="{ row }">
              <el-tag :type="liftTag(row.lift)" size="small" effect="plain">
                x{{ row.lift }}
              </el-tag>
            </template>
          </el-table-column>
          <el-table-column label="第 2 / 第 3 州" min-width="220">
            <template #default="{ row }">
              <span class="geo-hint">{{ topStatesText(row.states) }}</span>
            </template>
          </el-table-column>
        </el-table>
      </el-card>

      <!-- 州明细表 -->
      <el-card shadow="never" class="geo-card">
        <template #header>
          <div class="geo-card__head">
            <span class="geo-card__title">州明细（点行展开该州主要城市 + 畅销款 Top3）</span>
            <span class="geo-card__sub">共 {{ cur.states.length }} 个州有出单</span>
          </div>
        </template>
        <el-table
          :data="cur.states"
          size="small"
          stripe
          max-height="420"
          :default-sort="{ prop: 'orders', order: 'descending' }"
        >
          <el-table-column type="expand">
            <template #default="{ row }">
              <div class="geo-expand">
                <div class="geo-expand__block">
                  <span class="geo-expand__label">主要城市</span>
                  <el-tag v-for="c in row.cities" :key="c.name" size="small" effect="plain" class="geo-cities__tag">
                    {{ c.name || "—" }} · {{ num(c.orders) }}
                  </el-tag>
                  <span v-if="!row.cities?.length" class="geo-hint">该州没有可展开的城市</span>
                </div>
                <div class="geo-expand__block">
                  <span class="geo-expand__label">畅销款</span>
                  <el-tag
                    v-for="p in row.topProducts || []"
                    :key="p.name"
                    type="warning"
                    size="small"
                    effect="plain"
                    class="geo-cities__tag"
                  >
                    {{ p.name }} · {{ num(p.orders) }}（{{ pct(p.share) }}）
                  </el-tag>
                  <span v-if="!row.topProducts?.length" class="geo-hint">该州没有产品数据</span>
                </div>
              </div>
            </template>
          </el-table-column>
          <el-table-column label="#" type="index" width="52" />
          <el-table-column prop="name" label="州" min-width="140" sortable />
          <el-table-column prop="code" label="缩写" width="70" />
          <el-table-column prop="orders" label="订单数" width="100" sortable align="right">
            <template #default="{ row }">{{ num(row.orders) }}</template>
          </el-table-column>
          <el-table-column label="占比" width="140" sortable prop="share">
            <template #default="{ row }">
              <div class="geo-share">
                <el-progress
                  :percentage="Number((row.share * 100).toFixed(1))"
                  :stroke-width="10"
                  :show-text="false"
                  color="#409eff"
                />
                <span class="geo-share__txt font-din">{{ pct(row.share) }}</span>
              </div>
            </template>
          </el-table-column>
          <el-table-column label="畅销款" min-width="180">
            <template #default="{ row }">
              <template v-if="(row.topProducts || []).length">
                <el-tag
                  v-for="(c, i) in topSaleCodes(row.topProducts, 3)"
                  :key="c"
                  :type="i === 0 ? 'warning' : 'info'"
                  size="small"
                  effect="plain"
                  class="geo-code"
                >
                  {{ c }}
                </el-tag>
              </template>
              <span v-else class="geo-hint">—</span>
            </template>
          </el-table-column>
          <el-table-column label="该款在本州" width="140" align="right">
            <template #default="{ row }">
              <template v-if="row.topProducts?.length">
                <span class="font-din">{{ num(row.topProducts[0].orders) }}</span>
                <span class="geo-hint"> · {{ pct(row.topProducts[0].share) }}</span>
              </template>
              <span v-else class="geo-hint">—</span>
            </template>
          </el-table-column>        </el-table>
      </el-card>

      <!-- Top 城市 + 月度趋势 -->
      <el-row :gutter="12">
        <el-col :xs="24" :md="10">
          <el-card shadow="never" class="geo-card">
            <template #header>
              <div class="geo-card__head">
                <span class="geo-card__title">城市 Top 20</span>
                <span class="geo-card__sub">城市名可能带 * 号（脱敏）</span>
              </div>
            </template>
            <el-table :data="cur.topCities.slice(0, 20)" size="small" max-height="420">
              <el-table-column label="#" type="index" width="52" />
              <el-table-column prop="name" label="城市" min-width="140" />
              <el-table-column label="州" width="150">
                <template #default="{ row }">{{ row.name ? row.state : "—" }}</template>
              </el-table-column>
              <el-table-column prop="orders" label="订单数" width="100" align="right" sortable>
                <template #default="{ row }">{{ num(row.orders) }}</template>
              </el-table-column>
            </el-table>
          </el-card>
        </el-col>
        <el-col :xs="24" :md="14">
          <el-card shadow="never" class="geo-card">
            <template #header>
              <div class="geo-card__head">
                <span class="geo-card__title">月度出单趋势</span>
                <span class="geo-card__sub">柱=当月单量，线=当月第一名的州占该月比例</span>
              </div>
            </template>
            <div ref="trendRef" class="geo-trend" />
          </el-card>
        </el-col>
      </el-row>
    </template>
  </div>
</template>

<script lang="ts" setup>
import { computed, onMounted, onUnmounted, ref, watch, nextTick } from "vue";
import { Refresh } from "@element-plus/icons-vue";
import * as echarts from "echarts/core";
import { MapChart, BarChart, LineChart } from "echarts/charts";
import {
  TooltipComponent,
  VisualMapComponent,
  GridComponent,
  LegendComponent,
  TitleComponent,
} from "echarts/components";
import { CanvasRenderer } from "echarts/renderers";
import OrderGeoAPI, {
  type OrderGeoData,
  type GeoStoreBlock,
  type GeoProductDetail,
} from "@/api/temu/order-geo";
import { topSaleCodes } from "@/utils/sale-code";
import { onWeatherUpdate, startUsWeather, weatherLine } from "@/utils/us-weather";

echarts.use([
  MapChart,
  BarChart,
  LineChart,
  TooltipComponent,
  VisualMapComponent,
  GridComponent,
  LegendComponent,
  TitleComponent,
  CanvasRenderer,
]);

defineOptions({ name: "TemuOrderGeo" });

const MAP_NAME = "temu-usa";

const data = ref<OrderGeoData | null>(null);
const loading = ref(false);
const storeKey = ref<"ALL" | "BEL" | "BHY">("ALL");

const mapRef = ref<HTMLElement | null>(null);
const trendRef = ref<HTMLElement | null>(null);
let mapChart: echarts.ECharts | null = null;
let trendChart: echarts.ECharts | null = null;
let mapRegistered = false;
let mapJson: any = null;
let offWeather: (() => void) | undefined;

const num = (n?: number) => (n || 0).toLocaleString("en-US");
const pct = (v?: number) => `${((v || 0) * 100).toFixed(1)}%`;

/** 州行「主要城市」列：取前 3 个城市名 */
function topCityNames(cities?: { name: string }[]): string {
  const names = (cities || []).slice(0, 3).map((c) => c.name);
  return names.length ? names.join("、") : "—";
}

/** 款行「第 2 / 第 3 州」列 */
function topStatesText(states?: { name: string; code: string; orders: number }[]): string {
  const rest = (states || []).slice(1, 3);
  return rest.length ? rest.map((s) => `${s.name} ${num(s.orders)}`).join(" · ") : "—";
}

/** 集中度标签配色：x≥3 专供某州（红），x≥2 偏科（橙），其余中性 */
function liftTag(lift: number): "danger" | "warning" | "info" {
  if (lift >= 3) return "danger";
  if (lift >= 2) return "warning";
  return "info";
}

const EMPTY: GeoStoreBlock = {
  shopId: "",
  orders: 0,
  geoOrders: 0,
  states: [],
  topCities: [],
  months: [],
};

/** 当前口径（ALL = 两店合计；BEL/BHY = 单店） */
const cur = computed<GeoStoreBlock>(() => {
  const d = data.value;
  if (!d) return EMPTY;
  if (storeKey.value === "ALL") {
    return {
      shopId: "ALL",
      orders: d.totals?.orders || 0,
      geoOrders: d.totals?.geoOrders || 0,
      states: d.states || [],
      topCities: d.topCities || [],
      months: d.months || [],
    };
  }
  return d.stores?.[storeKey.value] || EMPTY;
});

const tabs = computed(() => {
  const d = data.value;
  const mk = (key: "ALL" | "BEL" | "BHY", label: string, n: number) => ({ key, label, count: n });
  return [
    mk("ALL", "全部", d?.totals?.orders || 0),
    mk("BEL", "BEL", d?.stores?.BEL?.orders || 0),
    mk("BHY", "BHY", d?.stores?.BHY?.orders || 0),
  ];
});

const missCount = computed(() => Math.max(0, cur.value.orders - cur.value.geoOrders));

/** 当前口径下的款榜单（ALL 用合计，单店用该店自己的） */
const curProducts = computed(() => {
  const d = data.value;
  if (!d) return [];
  if (storeKey.value === "ALL") return d.topProducts || [];
  return d.stores?.[storeKey.value]?.topProducts || [];
});

/**
 * 「各州畅销款 Top3」一行一个州：州 → 它的 Top1 款 + 该款在本州的单量/占比 + 第 2/3 款。
 * 地图上悬停能看到的，这里做成不用悬停也能一眼扫完的表。
 */
/**
 * 产品出单查询（用户 2026-10-14 要求）
 *
 * 索引文件 order-geo-products.json（全部 220 个款，~450KB）**按需加载**：
 * 页面先渲染地图/榜单，索引在后台拉，不阻塞首屏；数据大屏不加载它。
 */
const productIndex = ref<{ productCount: number; products: GeoProductDetail[] } | null>(null);
const indexReady = computed(() => !!productIndex.value);
const query = ref("");
const selected = ref<GeoProductDetail | null>(null);
const queryChartRef = ref<HTMLElement | null>(null);
let queryChart: echarts.ECharts | null = null;

/** 出单最多的 6 个款，作为「不知道搜什么」时的入口（按数字码去重，避免 102105 出现两次） */
const hotProducts = computed(() => {
  const out: GeoProductDetail[] = [];
  const seen = new Set<string>();
  for (const p of productIndex.value?.products || []) {
    if (seen.has(p.code)) continue;
    seen.add(p.code);
    out.push(p);
    if (out.length >= 6) break;
  }
  return out;
});

/** 搜索：款号 / 数字短码 / Fnsku 三个维度都匹配（不区分大小写） */
const queryHits = computed(() => {
  const all = productIndex.value?.products || [];
  const q = query.value.trim().toLowerCase();
  if (!q) return [];
  return all.filter(
    (p) =>
      p.name.toLowerCase().includes(q) ||
      p.code.includes(q) ||
      (p.fnskus || []).some((f) => f.toLowerCase().includes(q))
  );
});

/** 选中某一款（也支持从「热门」直接点） */
function pick(name: string) {
  const hit = (productIndex.value?.products || []).find((p) => p.name === name);
  if (!hit) return;
  selected.value = hit;
  query.value = hit.name;
  nextTick(() => drawQueryChart());
}

/** 州分布表里的累计占比（row 是表格行，形状与 states 项一致） */
function cumShare(states: { orders: number }[], row: unknown): number {
  const target = row as { orders: number };
  let acc = 0;
  for (const s of states) {
    acc += s.orders;
    if (s === target) break;
  }
  const total = states.reduce((a, s) => a + s.orders, 0) || 1;
  return acc / total;
}

/** 该款的月度出单柱状图 */
function drawQueryChart() {
  if (!queryChartRef.value || !selected.value) return;
  if (!queryChart) queryChart = echarts.init(queryChartRef.value);
  const months = selected.value.months;
  queryChart.setOption(
    {
      backgroundColor: "transparent",
      grid: { left: 40, right: 12, top: 16, bottom: 24 },
      tooltip: { trigger: "axis", formatter: "{b}<br/>{c} 单" },
      xAxis: {
        type: "category",
        data: months.map((m) => m.month.slice(5)),
        axisLabel: { fontSize: 10, color: "#909399" },
      },
      yAxis: {
        type: "value",
        axisLabel: { fontSize: 10, color: "#909399" },
        splitLine: { lineStyle: { color: "#f0f2f5" } },
      },
      series: [
        {
          type: "bar",
          barMaxWidth: 26,
          itemStyle: { color: "#4d9bff", borderRadius: [3, 3, 0, 0] },
          data: months.map((m) => m.orders),
        },
      ],
    },
    true
  );
}

async function loadProductIndex() {
  try {
    const idx = await OrderGeoAPI.getProductIndex();
    productIndex.value = { productCount: idx.productCount, products: idx.products };
  } catch (e) {
    console.error("加载产品索引失败:", e);
  }
}

const stateTopProductRows = computed(() =>  cur.value.states
    .filter((s) => (s.topProducts || []).length)
    .map((s) => {
      const ps = s.topProducts || [];
      return {
        state: s.name,
        code: s.code,
        orders: s.orders,
        /** 畅销款 Top3 数字短码（去重） */
        codes: topSaleCodes(ps, 3),
        topProduct: ps[0].name,
        topProductOrders: ps[0].orders,
        topProductShare: ps[0].share,
      };
    })
);

const stats = computed(() => {
  const c = cur.value;
  const top = c.states[0];
  const top3 = c.states.slice(0, 3).reduce((a, s) => a + s.orders, 0);
  const top3Share = c.geoOrders ? top3 / c.geoOrders : 0;
  return [
    { label: "出库单总数", value: num(c.orders), hint: "帕沃尔出库单（一单=一包裹）", color: "#409eff" },
    {
      label: "已定位到州",
      value: num(c.geoOrders),
      hint: `解析率 ${c.orders ? ((c.geoOrders / c.orders) * 100).toFixed(1) : "0.0"}%`,
      color: "#67c23a",
    },
    {
      label: "头号出单州",
      value: top ? `${top.name} ${top.code}` : "—",
      hint: top ? `${num(top.orders)} 单 · 占 ${pct(top.share)}` : "无数据",
      color: "#e6a23c",
    },
    {
      label: "Top3 州集中度",
      value: pct(top3Share),
      hint: c.states.slice(0, 3).map((s) => s.code).join(" / ") || "—",
      color: "#f56c6c",
    },
  ];
});

const maxOrders = computed(() => cur.value.states[0]?.orders || 1);
const barWidth = (s: { orders: number }) => `${Math.max(2, (s.orders / maxOrders.value) * 100)}%`;
const barColor = (i: number) => ["#00eaff", "#409eff", "#67c23a", "#e6a23c", "#a855f7"][Math.min(i, 4)];

async function ensureMap(): Promise<boolean> {
  if (mapRegistered) return true;
  if (!mapJson) {
    try {
      const res = await fetch(`${import.meta.env.BASE_URL}map/usa.json`, { cache: "force-cache" });
      mapJson = await res.json();
    } catch (e) {
      console.error("加载美国地图失败:", e);
      return false;
    }
  }
  echarts.registerMap(MAP_NAME, mapJson);
  mapRegistered = true;
  return true;
}

function renderMap() {
  // 地图没注册完就 setOption 会抛 "reading 'regions'"（底图是异步 fetch 的）
  if (!mapRef.value || !mapRegistered) return;
  if (!mapChart) mapChart = echarts.init(mapRef.value);
  const c = cur.value;
  const max = Math.max(1, ...c.states.map((s) => s.orders));
  mapChart.setOption(
    {
      backgroundColor: "transparent",
      tooltip: {
        trigger: "item",
        formatter: (p: any) => {
          const v = p.data?.value;
          if (v === undefined || v === null) return `${p.name}<br/>无出单`;
          /**
           * tooltip 内容（用户 2026-10-14 两次澄清后的口径）：
           *   ✅ 留 该州**总出单**（v 就是该州订单数）
           *   ✅ 留 该州畅销款 Top3（只给款号数字）
           *   ✅ 留 当天天气
           *   ❌ 不显示「哪个产品各卖了多少单」，也去掉了州占比（地图颜色 + 右侧榜单已表达）
           */
          const st = cur.value.states.find((s) => s.name === p.name);
          const codes = topSaleCodes(st?.topProducts, 3);
          const wx = weatherLine(p.name);
          return (
            `<b>${p.name}${st ? ` (${st.code})` : ""}</b><br/>${num(v)} 单` +
            (codes.length
              ? `<br/><span style="color:#ffd666">畅销款：${codes.join(" · ")}</span>`
              : "") +
            (wx ? `<br/><span style="color:#409eff">${wx}</span>` : "")
          );
        },
      },
      visualMap: {
        min: 0,
        max,
        left: 8,
        bottom: 8,
        itemWidth: 10,
        itemHeight: 80,
        text: ["多", "少"],
        textStyle: { color: "#909399", fontSize: 10 },
        inRange: { color: ["#e8f3ff", "#a8d2ff", "#4d9bff", "#1d6fd6", "#0b3f8f"] },
        calculable: true,
      },
      series: [
        {
          name: "订单数",
          type: "map",
          map: MAP_NAME,
          roam: false,
          zoom: 1.15,
          label: { show: true, fontSize: 8, color: "#5a6b7f" },
          itemStyle: { borderColor: "#c3d6ea", borderWidth: 0.6, areaColor: "#f4f7fb" },
          emphasis: {
            label: { show: true, fontSize: 11, color: "#0b3f8f", fontWeight: "bold" },
            itemStyle: { areaColor: "#ffd666" },
          },
          data: c.states.map((s) => ({ name: s.name, value: s.orders })),
        },
      ],
    },
    true
  );
}

function renderTrend() {
  if (!trendRef.value) return;
  if (!trendChart) trendChart = echarts.init(trendRef.value);
  const months = cur.value.months;
  trendChart.setOption(
    {
      backgroundColor: "transparent",
      grid: { left: 48, right: 48, top: 28, bottom: 28 },
      tooltip: {
        trigger: "axis",
        formatter: (ps: any[]) => {
          const m = ps[0]?.axisValue ?? "";
          const bar = ps.find((p) => p.seriesName === "当月单量");
          const line = ps.find((p) => p.seriesName !== "当月单量");
          const item = months.find((x) => x.month === m);
          const top = item?.topStates?.[0];
          return (
            `${m}<br/>单量：${num(bar?.value ?? 0)}` +
            (line ? `<br/>头号州占比：${line.value}%` : "") +
            (top ? `<br/>头号州：${top.name}（${num(top.orders)}）` : "")
          );
        },
      },
      legend: { right: 6, top: 0, textStyle: { color: "#909399", fontSize: 11 } },
      xAxis: {
        type: "category",
        data: months.map((m) => m.month.slice(5)),
        axisLine: { lineStyle: { color: "#dcdfe6" } },
        axisLabel: { color: "#909399", fontSize: 11 },
      },
      yAxis: [
        {
          type: "value",
          name: "单量",
          nameTextStyle: { color: "#909399", fontSize: 10 },
          axisLabel: { color: "#909399", fontSize: 10 },
          splitLine: { lineStyle: { color: "#f0f2f5" } },
        },
        {
          type: "value",
          name: "占比%",
          max: 100,
          nameTextStyle: { color: "#909399", fontSize: 10 },
          axisLabel: { color: "#909399", fontSize: 10, formatter: "{value}%" },
          splitLine: { show: false },
        },
      ],
      series: [
        {
          name: "当月单量",
          type: "bar",
          barMaxWidth: 34,
          itemStyle: { color: "#4d9bff", borderRadius: [4, 4, 0, 0] },
          data: months.map((m) => m.orders),
        },
        {
          name: "头号州占比",
          type: "line",
          yAxisIndex: 1,
          smooth: true,
          symbolSize: 6,
          lineStyle: { color: "#f56c6c", width: 2 },
          itemStyle: { color: "#f56c6c" },
          data: months.map((m) => {
            const t = m.topStates?.[0];
            return t && m.orders ? Number(((t.orders / m.orders) * 100).toFixed(1)) : 0;
          }),
        },
      ],
    },
    true
  );
}

async function draw() {
  await nextTick();
  const ok = await ensureMap();
  if (ok) renderMap();
  renderTrend();
}

async function load() {
  loading.value = true;
  try {
    data.value = await OrderGeoAPI.getOrderGeoData();
    await draw();
  } catch (e) {
    console.error("加载订单分布失败:", e);
  } finally {
    loading.value = false;
  }
}

watch(storeKey, () => {
  renderMap();
  renderTrend();
});
function onResize() {
  mapChart?.resize();
  trendChart?.resize();
}

onMounted(() => {
  load();
  // 产品索引 ~450KB，放后台拉：地图/榜单先出来，别让它阻塞首屏
  void loadProductIndex();
  window.addEventListener("resize", onResize);
  // 天气异步到达后要重设 option，tooltip 的 formatter 才会带上天气
  startUsWeather();
  offWeather = onWeatherUpdate(() => renderMap());
});

onUnmounted(() => {
  window.removeEventListener("resize", onResize);
  offWeather?.();
  mapChart?.dispose();
  trendChart?.dispose();
  queryChart?.dispose();
  mapChart = null;
  trendChart = null;
  queryChart = null;
});
</script>

<style lang="scss" scoped>
.geo-page {
  padding-bottom: 24px;
}

.geo-header {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 16px;
  margin-bottom: 12px;
}

.geo-header__title {
  margin: 0 0 4px;
  font-size: 20px;
  font-weight: 600;
  color: var(--el-text-color-primary);
}

.geo-header__desc {
  margin: 0;
  font-size: 13px;
  color: var(--el-text-color-secondary);
}

.geo-header__meta {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-shrink: 0;
}

.geo-alert {
  margin-bottom: 12px;
}

.geo-bar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  margin-bottom: 12px;
  flex-wrap: wrap;
}

.geo-bar__num {
  font-size: 12px;
  opacity: 0.8;
}

.geo-bar__hint {
  font-size: 12px;
  color: var(--el-text-color-secondary);
}

.geo-stats {
  margin-bottom: 4px;
}

.geo-stat__label {
  font-size: 13px;
  color: var(--el-text-color-secondary);
}

.geo-stat__value {
  margin-top: 4px;
  font-size: 24px;
  font-weight: 700;
  line-height: 1.2;
}

.geo-stat__hint {
  margin-top: 4px;
  font-size: 12px;
  color: var(--el-text-color-placeholder);
}

.geo-card {
  margin-top: 12px;
}

.geo-card__head {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: 12px;
}

.geo-card__title {
  font-size: 15px;
  font-weight: 600;
}

.geo-card__sub {
  font-size: 12px;
  color: var(--el-text-color-secondary);
}

.geo-map-wrap {
  display: flex;
  gap: 16px;
  align-items: stretch;
}

.geo-map {
  flex: 1;
  height: 470px;
  min-width: 0;
}

.geo-rank {
  width: 330px;
  flex-shrink: 0;
  border-left: 1px dashed var(--el-border-color-lighter);
  padding-left: 16px;
}

.geo-rank__head {
  display: flex;
  justify-content: space-between;
  font-size: 12px;
  color: var(--el-text-color-secondary);
  padding-bottom: 6px;
  border-bottom: 1px solid var(--el-border-color-lighter);
}

.geo-rank__row {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 6px 0;
  font-size: 13px;
}

.geo-rank__idx {
  width: 18px;
  height: 18px;
  line-height: 18px;
  text-align: center;
  border-radius: 50%;
  font-size: 11px;
  background: var(--el-fill-color);
  color: var(--el-text-color-secondary);
  flex-shrink: 0;

  &.is-top {
    background: #409eff;
    color: #fff;
  }
}

.geo-rank__name {
  width: 118px;
  flex-shrink: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.geo-rank__bar {
  flex: 1;
  height: 8px;
  background: var(--el-fill-color-light);
  border-radius: 4px;
  overflow: hidden;

  i {
    display: block;
    height: 100%;
    border-radius: 4px;
  }
}

.geo-rank__num {
  width: 56px;
  text-align: right;
  font-weight: 600;
  color: var(--el-text-color-primary);
}

.geo-rank__share {
  width: 52px;
  text-align: right;
  color: var(--el-text-color-secondary);
  font-size: 12px;
}

.geo-share {
  display: flex;
  align-items: center;
  gap: 8px;

  :deep(.el-progress) {
    flex: 1;
    min-width: 60px;
  }
}

.geo-share__txt {
  width: 48px;
  text-align: right;
  font-size: 12px;
  color: var(--el-text-color-secondary);
}

.geo-cities {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  padding: 4px 12px;
}

.geo-cities__tag {
  margin: 0;
}

.geo-expand {
  display: flex;
  flex-direction: column;
  gap: 6px;
  padding: 6px 12px;
}

.geo-expand__block {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 6px;
}

.geo-expand__label {
  width: 76px;
  flex-shrink: 0;
  font-size: 12px;
  color: var(--el-text-color-secondary);
}

.geo-prod {
  font-weight: 600;
  color: var(--el-color-warning);
}

/** 款号数字短码标签 */
.geo-code {
  margin: 0 4px 0 0;
  font-family: var(--el-font-family-monospace, monospace);
  letter-spacing: 0.4px;
}

/* 数据来源合并说明（帕沃尔 + 领星 OMS） */
.geo-source-note {
  margin-top: 6px;
  color: var(--el-text-color-regular);
}

/* ---------- 产品出单查询 ---------- */
.geo-query__bar {
  display: flex;
  align-items: center;
  gap: 12px;
  margin-bottom: 12px;
  flex-wrap: wrap;
}

.geo-query__input {
  max-width: 460px;
}

.geo-query__hot {
  margin-right: 10px;
  font-family: var(--el-font-family-monospace, monospace);
}

.geo-query__chart {
  height: 190px;
  margin-bottom: 10px;
}

.geo-detail__head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  padding: 4px 0 10px;
  flex-wrap: wrap;
}

.geo-detail__code {
  display: inline-block;
  min-width: 62px;
  padding: 2px 8px;
  margin-right: 8px;
  border-radius: 4px;
  background: var(--el-color-warning-light-9);
  color: var(--el-color-warning);
  font-family: var(--el-font-family-monospace, monospace);
  font-weight: 700;
  letter-spacing: 0.5px;
}

.geo-detail__name {
  font-size: 16px;
  font-weight: 600;
  margin-right: 8px;
}

.geo-detail__stats {
  margin-bottom: 8px;
}

.geo-detail__sub {
  font-size: 13px;
  font-weight: 600;
  color: var(--el-text-color-regular);
  margin: 8px 0 6px;
}

.geo-hint {
  font-size: 12px;
  color: var(--el-text-color-placeholder);
}

.geo-trend {
  height: 300px;
}

@media (max-width: 1200px) {
  .geo-map-wrap {
    flex-direction: column;
  }

  .geo-rank {
    width: 100%;
    border-left: none;
    padding-left: 0;
    border-top: 1px dashed var(--el-border-color-lighter);
    padding-top: 12px;
  }
}
</style>
