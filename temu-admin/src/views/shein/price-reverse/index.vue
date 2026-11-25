<!--
  SHEIN 运营 · SHEIN前端价推倒器（原名：希音最优前端价倒推器）

  用户 2026-11-25 要求：把 WorkBuddy 分享的 HTML 面板（希音最优前端价倒推器.html）搬到本站，
  跟在「在售款明细」同级，命名为「SHEIN前端价推倒器」。

  搬运原则：
    · **公式与口径一字不改** —— 锚点就是原页面的 JSON 与脚本逐行核对过的：
        F = k·S + A  ⇒  降幅基准=供货价时 S* = A÷(1−d)、F* = A×(1 + k÷(1−d))；
                        降幅基准=前端价时 S* = d·A÷(k(1−d))、F* = A÷(1−d)（与 k 无关）。
      改口径请先动原页面，再同步这里，别在这边另起一套。
    · 原页面是写死深底的独立页；本站有明暗两套主题，所以颜色全部走 CSS 变量，
      **不跟随**原页面的深色底（否则浅色主题下会变成黑块）。

  纯前端计算，无数据文件、无接口 —— 打开即用。
  ⚠ name 必须与 defineOptions({ name: "SheinPriceReverse" }) 一致，否则 keepAlive 匹配不上。
-->
<template>
  <!--
    布局（2026-11-25 优化）：原来是「整页 max-width:1180px + 单列往下堆」，
    宽屏下右侧空出 ~300px、底部也留大片空白（用户截图红框）。
    现在按屏宽分档：
      ≥1500px：输入卡 | 推导卡 | 两个结论（竖排）三列同排，左侧输入不再独占一行高度；
      1100~1500：输入卡 | 推导卡两列，结论横排在下面；
      <1100：全部单列。
  -->
  <div class="shein-price-rev app-container">
    <div class="spr-head">
      <div>
        <h2 class="spr-head__title">SHEIN前端价推倒器</h2>
        <p class="spr-head__desc">
          币种 USD。活动供货价、加价率、活动降幅要求都不动 —— 只调供货价，求能拿到的最有竞争力的前端价。
        </p>
      </div>
      <div class="spr-formulas">
        <span class="spr-formula spr-formula--blue">前端价 F = 加价率 k × 供货价 S + 活动价 A</span>
        <span class="spr-formula spr-formula--teal">最优解 F* = A × ( 1 + k ÷ (1 − d) )</span>
      </div>
    </div>

    <!-- 结论 + 两张卡同排；≥1500px 时结论移到最右一列 -->
    <div class="spr-top">
      <div class="spr-top__input">
        <div class="spr-card">
          <h3 class="spr-card__title">
            固定不动的输入
            <span class="spr-card__hint">· 支持两位小数</span>
          </h3>

          <div class="spr-row">
            <label for="spr-a">
              活动供货价 A
              <span class="spr-hint">保持不变</span>
            </label>
            <div class="spr-inp">
              <el-input-number
                id="spr-a"
                v-model="inA"
                :min="0"
                :precision="2"
                :step="0.5"
                :controls="false"
                size="small"
              />
              <span class="spr-u">USD</span>
            </div>
          </div>

          <div class="spr-row">
            <label for="spr-k">
              加价率 k
              <span class="spr-hint">保持不变</span>
            </label>
            <div class="spr-inp">
              <el-input-number
                id="spr-k"
                v-model="inK"
                :min="0"
                :max="99"
                :precision="2"
                :step="1"
                :controls="false"
                size="small"
              />
              <span class="spr-u">%</span>
            </div>
          </div>

          <div class="spr-row">
            <label for="spr-d">
              活动降幅要求 d
              <span class="spr-hint">30% 起步</span>
            </label>
            <div class="spr-inp">
              <el-input-number
                id="spr-d"
                v-model="inD"
                :min="0"
                :max="99"
                :precision="2"
                :step="1"
                :controls="false"
                size="small"
              />
              <span class="spr-u">%</span>
            </div>
          </div>

          <div class="spr-row spr-row--block">
            <label class="spr-row__label">降幅的计算基准</label>
            <el-radio-group v-model="base" size="small" class="spr-seg">
              <el-radio-button value="supply">相对供货价</el-radio-button>
              <el-radio-button value="front">相对前端价</el-radio-button>
            </el-radio-group>
          </div>

          <!-- 当前参数下的关键换算（填充宽屏等高留白） -->
          <div class="spr-input-sum">
            <div class="spr-input-sum__item">
              <span class="spr-input-sum__k">加价价格 k × S</span>
              <span class="spr-input-sum__v">{{ money(k * S) }}</span>
            </div>
            <div class="spr-input-sum__item">
              <span class="spr-input-sum__k">前端价 ÷ 供货价</span>
              <span class="spr-input-sum__v">{{ ratio(F / S) }}</span>
            </div>
            <div class="spr-input-sum__item">
              <span class="spr-input-sum__k">前端价 ÷ 活动价</span>
              <span class="spr-input-sum__v">{{ ratio(F / A) }}</span>
            </div>
            <div class="spr-input-sum__item">
              <span class="spr-input-sum__k">活动降幅（实际）</span>
              <span
                class="spr-input-sum__v"
                :style="{ color: disc >= d - 1e-9 ? 'var(--spr-teal)' : 'var(--spr-red)' }"
              >
                {{ pct(disc) }}
              </span>
            </div>
          </div>
        </div>
      </div>

      <div class="spr-top__derive">
        <div class="spr-card">
          <h3 class="spr-card__title">为什么是这两个数</h3>
          <div class="spr-brk">
            <div v-for="(r, i) in breakdown" :key="i" class="spr-brk__row">
              <span>
                <span v-html="r.label" />
                <span class="spr-brk__note" v-html="r.note" />
              </span>
              <span class="spr-brk__val" :style="r.color ? { color: r.color } : undefined" v-html="r.value" />
            </div>
          </div>

          <h3 class="spr-card__title spr-mt">供货价可选范围</h3>
          <div class="spr-bar">
            <div class="spr-bar__z spr-bar__z--bad" :style="{ width: pS + '%' }" />
            <div class="spr-bar__z spr-bar__z--ok" :style="{ left: pS + '%' }" />
            <div class="spr-bar__mk" :style="{ left: `calc(${pS}% - 1px)` }" />
          </div>
          <div class="spr-tk">
            <i style="left: 0">$0</i>
            <i :style="{ left: pS + '%' }">S*<br />{{ money(S) }}</i>
            <i style="left: 100%">{{ money(top) }}</i>
          </div>
          <div class="spr-legend">
            <span><em class="spr-legend__bad" />低于底线：活动降幅凑不够，报不了</span>
            <span><em class="spr-legend__ok" />可行，但前端价会走高</span>
          </div>
        </div>
      </div>

      <div class="spr-top__answer">
        <div class="spr-big spr-big--act">
          <div class="spr-big__lb">最优供货价 S*（不能再低）</div>
          <div class="spr-big__vl">{{ money(S) }}</div>
          <div class="spr-big__nt">{{ sNote }}</div>
        </div>
        <div class="spr-big spr-big--win">
          <div class="spr-big__lb">最有竞争力的前端价 F*</div>
          <div class="spr-big__vl">{{ money(F) }}</div>
          <div class="spr-big__nt">{{ fNote }}</div>
        </div>
      </div>
    </div>

    <div class="spr-alerts">
      <div class="spr-alert spr-alert--warn">
        <b>效率提醒：</b>在这套结构下，供货价每降 $1.00，前端价只降 <b>{{ money(k) }}</b>（斜率就是加价率）。
        想把前端价再压 $1.00，你得再让出 <b>{{ money(eff) }}</b> 的供货价 ——
        降供货价换前端价，是一桩 1 : {{ ratio(k) }} 的买卖。
      </div>
      <div class="spr-alert spr-alert--ok">
        <b>真正的杠杆是加价率 k。</b>闭式解 <b>F* = A × (1 + k ÷ (1 − d))</b> 里，k 是唯一被乘在活动价上的系数：
        加价率每降 1 个百分点，前端价可再降 <b>{{ money(A / (1 - d) / 100) }}</b>。而降幅要求 d 由平台定，你自己压不动。
      </div>
    </div>

    <!-- 逐档对比 -->
    <div class="spr-card">
      <h3 class="spr-card__title">供货价一动，前端价跟着动多少（斜率 = 加价率 k）</h3>
      <el-table :data="tableRows" size="small" border class="spr-table" :row-class-name="rowClass">
        <el-table-column prop="S" label="供货价 S" width="120" align="right">
          <template #default="{ row }">{{ money(row.S) }}</template>
        </el-table-column>
        <el-table-column prop="F" label="前端价 F" width="120" align="right">
          <template #default="{ row }">{{ money(row.F) }}</template>
        </el-table-column>
        <el-table-column prop="disc" label="活动降幅" width="110" align="right">
          <template #default="{ row }">{{ pct(row.disc) }}</template>
        </el-table-column>
        <el-table-column label="活动可报" width="100" align="center">
          <template #default="{ row }">
            <span class="spr-badge" :class="row.ok ? 'spr-badge--y' : 'spr-badge--n'">
              {{ row.ok ? "可报" : "报不了" }}
            </span>
          </template>
        </el-table-column>
        <el-table-column label="F ÷ S" width="100" align="right">
          <template #default="{ row }">{{ ratio(row.F / row.S) }}</template>
        </el-table-column>
        <el-table-column label="说明" min-width="180">
          <template #default="{ row }">
            <span class="spr-mut">{{ row.note }}</span>
          </template>
        </el-table-column>
      </el-table>
    </div>

    <!-- 加价率杠杆 -->
    <div class="spr-card">
      <h3 class="spr-card__title">参考：如果加价率也能谈（这不是你现在的条件，仅作杠杆对比）</h3>
      <div class="spr-brk">
        <div v-for="(r, i) in kRows" :key="i" class="spr-brk__row">
          <span>
            加价率 {{ pct(r.k) }}
            <span v-if="r.now" class="spr-now">← 你现在</span>
            <span class="spr-brk__note">对应最优供货价 {{ money(r.S) }}</span>
          </span>
          <span class="spr-brk__val" :style="{ color: r.now ? 'var(--spr-amber)' : 'var(--spr-teal)' }">
            前端价 {{ money(r.F) }}
          </span>
        </div>
      </div>
    </div>

    <div class="spr-foot">
      <b>推导口径（币种 USD）</b><br />
      · <code>F = k · S + A</code>；k 与 A 固定 ⇒ F 是 S 的单调递增直线，斜率就是 k ⇒ <b>S 越小，F 越低</b>。<br />
      · 降幅基准 = <b>供货价</b>：<code>(S − A) / S ≥ d</code> ⟺ <code>S* = A ÷ (1 − d)</code>，
      代回得闭式解 <code>F* = A × (1 + k ÷ (1 − d))</code>。<br />
      · 降幅基准 = <b>前端价</b>：<code>(F − A) / F ≥ d</code> ⟺ <code>S* = d · A ÷ (k(1 − d))</code>，
      此时 <code>F* = A ÷ (1 − d)</code>，与 k 无关。<br />
      · 本版按原页面口径<b>不引入成本与毛利率</b>，所以最优点完全由活动降幅底线决定，没有第二条约束。
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed, ref } from "vue";

defineOptions({ name: "SheinPriceReverse" });

/* ---------------------------------------------------------------- 数值工具
 * 与原页面逐行一致：四舍五入到 2 位、去掉多余的 0，金额/百分比/倍数三种格式。
 * ⚠ 不要改成 toFixed 直接显示 —— 原页面 1.2857 这类倍数就是 4 位小数。
 */
function r2(x: number) {
  return Math.round((x + Number.EPSILON) * 100) / 100;
}
function trim(s: string) {
  return s.includes(".") ? s.replace(/0+$/, "").replace(/\.$/, "") : s;
}
function money(x: number) {
  if (!isFinite(x)) return "—";
  return `$${r2(x).toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
}
function pct(x: number) {
  if (!isFinite(x)) return "—";
  return `${trim(r2(x * 100).toFixed(2))}%`;
}
function ratio(x: number) {
  if (!isFinite(x)) return "—";
  return trim((r2(x * 10000) / 10000).toFixed(4));
}

/* ---------------------------------------------------------------- 输入
 * 默认值与分享的原页面一致：A=5、k=20%、d=30%、基准=相对供货价。
 * el-input-number 清空时给 null，这里兜住默认值（原页面用 _last 记住上一次） */
const inA = ref<number | null>(5);
const inK = ref<number | null>(20);
const inD = ref<number | null>(30);
const base = ref<"supply" | "front">("supply");

const A = computed(() => Math.max(0, inA.value ?? 5));
const k = computed(() => {
  let v = (inK.value ?? 20) / 100;
  if (!(v > 0)) v = 0.005; // 原页面：k ≤ 0 时兜成 0.5%，避免除零
  return v;
});
const d = computed(() => {
  let v = (inD.value ?? 30) / 100;
  if (v >= 1) v = 0.999;
  if (v < 0) v = 0;
  return v;
});

/* ---------------------------------------------------------------- 核心公式（原样搬运） */
const S = computed(() =>
  base.value === "supply" ? A.value / (1 - d.value) : (d.value * A.value) / (k.value * (1 - d.value)),
);
const F = computed(() => k.value * S.value + A.value);
/** 实际降幅：按所选基准算 */
const disc = computed(() =>
  base.value === "supply"
    ? S.value > 0
      ? (S.value - A.value) / S.value
      : -Infinity
    : F.value > 0
      ? (F.value - A.value) / F.value
      : -Infinity,
);
const eff = computed(() => 1 / k.value);

const sNote = computed(() =>
  base.value === "supply"
    ? `= 活动价 ${money(A.value)} ÷ (1 − ${pct(d.value)})`
    : `= ${pct(d.value)} × 活动价 ÷ ( 加价率 × (1 − ${pct(d.value)}) )`,
);
const fNote = computed(() => `= k ${pct(k.value)} × S ${money(S.value)} + A ${money(A.value)}`);

/* ---------------------------------------------------------------- 推导明细 */
const breakdown = computed(() => {
  const rows = [
    {
      label: `<b>活动降幅底线：S ≥ ${base.value === "supply" ? "A ÷ (1 − d)" : "d·A ÷ (k(1 − d))"}</b>`,
      value: `<b>${money(S.value)}</b>`,
      color: "",
      note: "<b>这就是最优供货价，低于它活动直接报不了</b>",
    },
    {
      label: "加价价格 = k × S",
      value: money(k.value * S.value),
      color: "var(--spr-mut)",
      note: "前端价 = 加价价格 + 活动价",
    },
    {
      label: "活动降幅（实际）",
      value: pct(disc.value),
      color: disc.value >= d.value - 1e-9 ? "var(--spr-teal)" : "var(--spr-red)",
      note: disc.value >= d.value - 1e-9 ? "刚好卡在要求上" : "不达标",
    },
    {
      label: "前端价 ÷ 供货价",
      value: ratio(F.value / S.value),
      color: "var(--spr-mut)",
      note: "加价倍率的倒数视角",
    },
    {
      label: "<b>前端价 ÷ 活动价</b>",
      value: `<b>${ratio(F.value / A.value)}</b>`,
      color: "",
      note: `<b>= 1 + k ÷ (1 − d) = 1 + ${ratio(k.value / (1 - d.value))}</b>`,
    },
  ];
  return rows;
});

/* ---------------------------------------------------------------- 可选范围条 */
const top = computed(() => S.value * 1.75);
const pS = computed(() => Math.max(0, Math.min(100, (S.value / top.value) * 100)));

/* ---------------------------------------------------------------- 逐档对比表 */
const tableRows = computed(() => {
  const lo = S.value * 0.75;
  const hi = S.value * 1.75;
  const steps = 10;
  const out = [];
  for (let s = 0; s < steps; s++) {
    const SS = lo + ((hi - lo) * s) / (steps - 1);
    const FF = k.value * SS + A.value;
    const dd =
      base.value === "supply"
        ? SS > 0
          ? (SS - A.value) / SS
          : -Infinity
        : FF > 0
          ? (FF - A.value) / FF
          : -Infinity;
    const ok = dd >= d.value - 1e-9;
    const isStar = Math.abs(SS - S.value) / S.value < 0.05;
    out.push({ S: SS, F: FF, disc: dd, ok, isStar });
  }
  // 文案在原页面里依赖 isStar / ok，补在算完之后
  return out.map((r) => ({
    ...r,
    note: r.isStar ? "最优点：前端价最低且活动可报" : r.ok ? "多让利，前端价更高" : "降幅不够，报不了",
  }));
});

function rowClass({ row }: { row: { isStar: boolean; ok: boolean } }) {
  if (row.isStar) return "spr-row-cur";
  if (!row.ok) return "spr-row-bad";
  return "";
}

/* ---------------------------------------------------------------- 加价率杠杆 */
const kRows = computed(() =>
  [0.2, 0.15, 0.12, 0.1, 0.08, 0.05].map((kk) => {
    const S2 = base.value === "supply" ? A.value / (1 - d.value) : (d.value * A.value) / (kk * (1 - d.value));
    return { k: kk, S: S2, F: kk * S2 + A.value, now: Math.abs(kk - k.value) < 0.005 };
  }),
);
</script>

<style scoped lang="scss">
/*
 * 颜色全部走本站主题变量 + 本页自己的 --spr-* 强调色，
 * 明暗主题下都成立（原页面写死深底，浅色主题下会变黑块）。
 */
.shein-price-rev {
  --spr-line: var(--el-border-color-lighter);
  --spr-card: var(--el-bg-color-overlay);
  --spr-card2: var(--el-fill-color-light);
  --spr-tx: var(--el-text-color-primary);
  --spr-mut: var(--el-text-color-regular);
  --spr-dim: var(--el-text-color-secondary);
  --spr-blue: #6ea8fe;
  --spr-teal: #3aa77e;
  --spr-amber: #ef9f27;
  --spr-red: #e05561;

  color: var(--spr-tx);
  font-size: 14px;
  line-height: 1.6;
}

// 页头：标题+说明在左，两个公式贴右边，宽屏不再白白空一行
.spr-head {
  display: flex;
  flex-wrap: wrap;
  align-items: flex-end;
  justify-content: space-between;
  gap: 10px 24px;
  margin-bottom: 12px;

  &__title {
    margin: 0 0 6px;
    font-size: 20px;
    font-weight: 600;
    color: var(--spr-tx);
  }

  &__desc {
    margin: 0;
    font-size: 13px;
    color: var(--spr-mut);
  }
}

.spr-formulas {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
}

.spr-formula {
  padding: 8px 14px;
  font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
  font-size: 13px;
  background: var(--spr-card);
  border: 1px solid var(--spr-line);
  border-radius: 8px;

  &--blue {
    color: var(--spr-blue);
  }

  &--teal {
    color: var(--spr-teal);
    border-color: rgb(58 167 126 / 35%);
  }
}

/*
 * 主区：输入 | 推导 | 结论 —— 用 auto-fit 按**可用宽度**自动分档，不写死断点。
 *
 * 为什么用 auto-fit 而不是 @media：
 *   内容区的实际宽度 = 浏览器宽 − 侧边栏(224px)，和视口宽不是一回事。
 *   之前用 min-width:1500/1700 的视口断点，1916 宽窗口的内容区只有 ~1500，
 *   结果就卡在两列 —— 结论被挤到第二行，首屏看不到两个最重要的大数字
 *   （用户 2026-11-25 截图红框就是这个现象）。
 *   minmax(260px, …) 的含义：宽度够放下第三列（结论卡 260px 起）就自动撑出来，
 *   不够就自动降成两列/一列，任何窗口尺寸都不会出现「右侧一整块空白」。
 */
.spr-top {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(260px, 1fr));
  gap: 16px;
  align-items: stretch;
  margin-bottom: 16px;

  &__answer {
    display: grid;
    grid-template-columns: repeat(2, minmax(0, 1fr));
    gap: 14px;
    grid-column: 1 / -1;

    @media (max-width: 720px) {
      grid-template-columns: 1fr;
    }
  }
}

// 输入卡比推导卡矮，靠卡片等高 + 底部摘要把空白吃掉，不靠拉伸留白
.spr-top__input,
.spr-top__derive,
.spr-top__answer {
  min-width: 0;
}

.spr-top__input .spr-card,
.spr-top__derive .spr-card {
  height: 100%;
  margin-bottom: 0;
}

/*
 * ≥1180px（内容区够宽）：结论收成右侧一列，两个大数字上下叠；
 * 输入/推导各自加宽一档，整块一行放下，三个信息块首屏全见。
 *
 * ⚠ 这里的 <1180px 结果与上面的 auto-fit 等价（三项 × 260 + 两个 16px 间距），
 *   不是新档位：auto-fit 只是第三列放不下时的保底形态。
 */
@media (min-width: 1180px) {
  .spr-top {
    grid-template-columns: minmax(0, 1.05fr) minmax(0, 1.3fr) minmax(280px, 0.78fr);
  }

  .spr-top__answer {
    grid-column: auto;
    grid-template-columns: 1fr;
  }
}

.spr-big {
  display: flex;
  flex-direction: column;
  justify-content: center;
  padding: 18px 22px;
  border-radius: 12px;

  // 宽屏右列两卡上下排时会被拉到整列高，内容垂直居中，避免大片空白贴在卡片底
  @media (min-width: 1180px) {
    flex: 1;
  }

  &__lb {
    margin-bottom: 6px;
    font-size: 12px;
    color: var(--spr-mut);
  }

  &__vl {
    font-size: 32px;
    font-weight: 600;
    line-height: 1.1;
    letter-spacing: -0.8px;
    font-variant-numeric: tabular-nums;
  }

  &__nt {
    margin-top: 7px;
    font-size: 11.5px;
    line-height: 1.5;
    color: var(--spr-dim);
  }

  &--act {
    background: rgb(110 168 254 / 14%);
    border: 1px solid rgb(110 168 254 / 40%);

    .spr-big__vl {
      color: var(--spr-blue);
    }
  }

  &--win {
    background: rgb(58 167 126 / 13%);
    border: 1px solid rgb(58 167 126 / 40%);

    .spr-big__vl {
      color: var(--spr-teal);
    }
  }
}

.spr-card {
  padding: 18px;
  margin-bottom: 16px;
  background: var(--spr-card);
  border: 1px solid var(--spr-line);
  border-radius: var(--card-radius, 8px);

  &__title {
    margin: 0 0 14px;
    font-size: 13px;
    font-weight: 600;
    color: var(--spr-mut);
    letter-spacing: 0.5px;
  }

  &__hint {
    font-weight: 400;
    color: var(--spr-dim);
  }
}

.spr-mt {
  margin-top: 20px;
}

/*
 * 输入卡底部补两条「当前结论」摘要：
 * 宽屏三列时输入卡比推导卡矮，原来靠等高拉伸撑出一大块空白；
 * 与其留白，不如把这几个换算结果直接摆给用户看（与右侧「为什么是这两个数」互补，
 * 这里只说「现在这套参数意味着什么」）。
 */
.spr-input-sum {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 8px 14px;
  padding-top: 12px;
  margin-top: 14px;
  border-top: 1px solid var(--spr-line);

  &__item {
    display: flex;
    flex-direction: column;
    gap: 1px;
    min-width: 0;
  }

  &__k {
    font-size: 11px;
    color: var(--spr-dim);
  }

  &__v {
    font-size: 15px;
    font-weight: 600;
    font-variant-numeric: tabular-nums;
    color: var(--spr-tx);
  }
}

// 输入行
.spr-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  padding: 7px 0;

  & + & {
    border-top: 1px solid var(--spr-line);
  }

  label {
    font-size: 13px;
    color: var(--spr-tx);
  }

  &--block {
    display: block;

    .spr-row__label {
      display: block;
      margin-bottom: 6px;
    }
  }
}

.spr-hint {
  display: block;
  font-size: 11px;
  color: var(--spr-dim);
}

.spr-inp {
  display: flex;
  flex: 0 0 auto;
  align-items: center;
  gap: 6px;

  :deep(.el-input-number) {
    width: 110px;
  }

  :deep(.el-input__inner) {
    text-align: right;
    font-variant-numeric: tabular-nums;
  }
}

.spr-u {
  min-width: 26px;
  font-size: 12px;
  color: var(--spr-dim);
}

.spr-seg {
  display: flex;
  width: 100%;

  :deep(.el-radio-button),
  :deep(.el-radio-button__inner) {
    flex: 1;
  }

  :deep(.el-radio-button__inner) {
    width: 100%;
  }
}

// 推导明细
.spr-brk {
  &__row {
    display: flex;
    justify-content: space-between;
    gap: 14px;
    padding: 7px 0;
    font-size: 13px;
    border-bottom: 1px solid var(--spr-line);

    &:last-child {
      border-bottom: none;
    }
  }

  &__note {
    display: block;
    font-size: 11px;
    color: var(--spr-dim);
  }

  &__val {
    flex: 0 0 auto;
    font-weight: 500;
    font-variant-numeric: tabular-nums;
  }
}

.spr-now {
  color: var(--spr-amber);
}

// 范围条
.spr-bar {
  position: relative;
  height: 26px;
  overflow: hidden;
  background: var(--spr-card2);
  border: 1px solid var(--spr-line);
  border-radius: 7px;

  &__z {
    position: absolute;
    top: 0;
    bottom: 0;

    &--bad {
      left: 0;
      background: rgb(224 85 97 / 38%);
    }

    &--ok {
      right: 0;
      background: rgb(58 167 126 / 38%);
    }
  }

  &__mk {
    position: absolute;
    top: -5px;
    bottom: -5px;
    width: 2px;
    background: var(--spr-amber);
    border-radius: 1px;
  }
}

.spr-tk {
  position: relative;
  height: 38px;
  margin-top: 5px;
  font-size: 11px;
  color: var(--spr-dim);

  i {
    position: absolute;
    font-style: normal;
    text-align: center;
    white-space: nowrap;
    transform: translateX(-50%);
  }
}

.spr-legend {
  display: flex;
  flex-wrap: wrap;
  gap: 18px;
  margin-top: 4px;
  font-size: 11.5px;
  color: var(--spr-dim);

  em {
    display: inline-block;
    width: 10px;
    height: 10px;
    margin-right: 5px;
    font-style: normal;
    vertical-align: -1px;
    border-radius: 2px;
  }

  &__bad {
    background: rgb(224 85 97 / 55%);
  }

  &__ok {
    background: rgb(58 167 126 / 55%);
  }
}

// 提示条
.spr-alerts {
  display: flex;
  flex-direction: column;
  gap: 10px;
  margin-bottom: 16px;
}

.spr-alert {
  padding: 12px 14px;
  font-size: 13px;
  line-height: 1.7;
  border-radius: 9px;

  &--warn {
    color: var(--el-text-color-primary);
    background: rgb(239 159 39 / 10%);
    border: 1px solid rgb(239 159 39 / 32%);
  }

  &--ok {
    color: var(--el-text-color-primary);
    background: rgb(58 167 126 / 9%);
    border: 1px solid rgb(58 167 126 / 30%);
  }
}

.spr-table {
  :deep(.spr-row-cur) {
    --el-table-tr-bg-color: rgb(58 167 126 / 11%);
  }

  :deep(.spr-row-bad) {
    opacity: 0.55;
  }
}

.spr-badge {
  display: inline-block;
  padding: 1px 7px;
  font-size: 11px;
  border-radius: 5px;

  &--y {
    color: var(--spr-teal);
    background: rgb(58 167 126 / 16%);
  }

  &--n {
    color: var(--spr-red);
    background: rgb(224 85 97 / 16%);
  }
}

.spr-mut {
  color: var(--spr-mut);
}

.spr-foot {
  padding-top: 16px;
  margin-top: 4px;
  font-size: 11.5px;
  line-height: 1.9;
  color: var(--spr-dim);
  border-top: 1px solid var(--spr-line);

  code {
    padding: 0 3px;
    font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
    color: var(--spr-mut);
    background: var(--spr-card2);
    border-radius: 3px;
  }
}
</style>
