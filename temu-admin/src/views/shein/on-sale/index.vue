<!--
  SHEIN 运营 · 在售款明细

  用户 2026-11-25 要求：侧边栏加一个与「TEMU 运营」「产品-库存」同级的「SHEIN 运营」栏目。

  落地页 = 在售款明细（BS / BD / BAN / BE「已上架」款 × 平台销量 × 线上库存）。
  表格本体复用 views/temu/goods/shein-goods.vue —— **一处逻辑**，避免
  TEMU 运营里的「在售款明细 · SHEIN」页签与本页各写一份、日后口径漂移。

  数据：public/data/shein-goods.json
    （python scripts/goods/fetch_shein_sales.py 生成，每天早上随定时任务刷新）

  ⚠ name 必须与 defineOptions({ name: "SheinOnSale" }) 一致，否则 keepAlive 匹配不上。
-->
<template>
  <div class="shein-on-sale app-container">
    <div class="so-header">
      <div>
        <h2 class="so-header__title">在售款明细</h2>
        <p class="so-header__desc">
          SHEIN 半托管 BS / BD / BAN / BE 「已上架」款 × 平台销量（昨日 / 近7天 / 近30天）× 线上可用库存。
          点行首箭头展开看每个尺码。
        </p>
      </div>
    </div>

    <SheinGoods />
  </div>
</template>

<script setup lang="ts">
import SheinGoods from "@/views/temu/goods/shein-goods.vue";

defineOptions({ name: "SheinOnSale" });
</script>

<style scoped lang="scss">
.so-header {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 16px;
  margin-bottom: 12px;

  &__title {
    margin: 0;
    font-size: 18px;
    font-weight: 600;
    color: var(--el-text-color-primary);
  }

  &__desc {
    margin: 4px 0 0;
    font-size: 12px;
    color: var(--el-text-color-secondary);
  }
}
</style>
