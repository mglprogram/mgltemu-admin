# TEMU 上架草稿的完整字段结构（实测）

数据来源：`POST /visage-agent-seller/product/draft/query`（`{"productDraftId": 57682030962}`）
店铺：BHY（TM-BHY-YJF，mallid `634418228526171`）
时间：2026-11-26
原始响应：`out/draft/draft_57682030962_20261126-141648.json`（19,878 字节，39 个顶层字段）

> 这份是**真实填好的草稿**回读结果，不是猜的。上一轮抓包失败的部分（属性/SKU/价格结构），这里全部补上了。

---

## 一、读取草稿的两个接口（已验证）

| 接口 | 请求体 | 返回 |
|---|---|---|
| `POST /visage-agent-seller/product/draft/pageQuery` | `{"page":1,"pageSize":50}` | `result.pageItems[]`，本店 26 个草稿 |
| `POST /visage-agent-seller/product/draft/query` | `{"productDraftId":57682030962}` | `result` = 完整草稿对象 |

两个都只需 `mallid` 头，**不需要 `Anti-Content`**。

列表项字段：`productDraftId, productName, sourceType, updatedAt, categories, leafCat, copyFromFullyToSemiInfo, copyFromSemiToFullyInfo`

脚本：`python scripts/goods/read_draft.py --port <端口> --list|--id <id>|--latest`

---

## 二、草稿顶层结构（39 字段）

```
categories                      类目链 cat1~cat10
productName / productI18nList   商品名 + 多语言（en: "Title Title Title"）
sizeTemplateId / sizeTemplateIds 尺码模板 17595937603747
supplierId                      634418228526171（= mallid）
productType / sourceType        sourceType=0
mainParentSpecIds               主规格维度
productPropertyList             ★17 条商品属性
productSpecPropertyVOS          ★6 条规格属性（颜色/尺码）
productSkcList                  ★1 个 SKC（货号）
carouselImageUrls               轮播图（本稿为空）
guideFilePageImages / materialImgUrl / outerPackageImages / 视频
imageLanguageList / carouseVideoVOList / detailVideoVOList
productWhExtAttr / productLocalExtAttr / productSaleExtAttr / productNonAuditExtAttr
goodsAdvantageLabelVOList / goodsLayerDecorationVOList …
```

## 三、类目（`categories`）

```json
{
  "cat1": {"catId": 27011, "catName": "服装、鞋靴和珠宝饰品", "catEnName": null, "catType": null},
  "cat2": {"catId": 30327, "catName": "男士时尚"},
  "cat3": {"catId": 30328, "catName": "男装"},
  "cat4": {"catId": 30465, "catName": "男士时尚帽衫和卫衣"},
  "cat5": {"catId": 30467, "catName": "男士时尚帽衫"},
  "cat6": {"catId": 0, "catName": ""},      // 5 级就到底了，后面补 0
  ...
  "catType": 1
}
```

**要点**：不足 10 级的类目，后续 `catN` 填 `catId:0 / catName:""`，不能省字段。

## 四、商品属性（`productPropertyList`，17 条）

字段：`templatePid, pid, refPid, propName, vid, propValue, valueUnit, valueExtendInfo, numberInputValue`

| propName | propValue | 数值 |
|---|---|---|
| 材质 | 针织面料 | |
| 成分 | 聚酯纤维(涤纶） | 100.00 % |
| 风格 | 运动 | |
| 护理说明 | 可机洗且不可干洗 | |
| 领型 | 连帽 | |
| 面料 | 微弹 | |
| 是否透明 | 否 | |
| 图案 | 纯色 | |
| 季节 | 春,夏,秋,冬 | |
| 细节 | 无 | |
| 版型 | 修身 | |
| 适用人群 | 男士 | |
| 织造方式 | 针织(含钩织、毛织面料) | |
| 印花类型 | 无印花 | |
| 面料纹理1 | 光面 | |
| 里料纹理 | 无里料/无内衬 | |
| 面料克重1（g/m²) | | 140 |

样例：
```json
{"templatePid":1226935,"pid":2,"refPid":15,"propName":"成分","vid":98,
 "propValue":"聚酯纤维(涤纶）","valueUnit":"%","valueExtendInfo":"","numberInputValue":"100.00"}
```

> 提交时要带 `templatePid / pid / vid` 三件套，**只有中文名不够** —— 这些 ID 来自
> `POST /anniston-agent-seller/category/template/query`（按类目取模板）。

## 五、规格属性（`productSpecPropertyVOS`，6 条）

在商品属性字段基础上多 4 个：`parentSpecId, specId, valueGroupId, valueGroupName`

```json
{"propName":"颜色","propValue":"混合色","vid":26419,"valueExtendInfo":"(255,255,254,1)",
 "parentSpecId":1001,"specId":21263,"valueGroupId":11,"valueGroupName":"其他"}
{"propName":"尺码","propValue":"S","parentSpecId":3001,"specId":10004,"valueGroupId":2,"valueGroupName":"中国码"}
```

- `parentSpecId` 固定两个维度：**1001=颜色，3001=尺码**
- 颜色带 `valueExtendInfo` 是 RGBA，形如 `(255,255,254,1)`

## 六、SKC / SKU（`productSkcList`）

本稿 1 个 SKC、5 个 SKU。

### SKC（货号层）
```
extCode            BHY-102105-9SetB
productSkcSpecList [{parentSpecId:1001, parentSpecName:"颜色", specId:21263, specName:"混合色"}]
previewImgUrls / colorImageUrl / productSkcImageExtList   （本稿为空）
productSkuList     5 个
```

### SKU（尺码层）
字段：`productSkuId, extCode, productSkuSpecList, currencyType, supplierPrice,
siteSupplierPrices, siteReferPriceList, productSkuSuggestedPrice,
productSkuWhExtAttr（包装）, productSkuQuantity, productSkuSensitiveApprove, thumbUrl`

| extCode | 规格 | 申报价 | 体积 | 重量 |
|---|---|---|---|---|
| BHY-102105-9SetB-S | 混合色/S | 6999 | 300×250×40 | 540 g |
| BHY-102105-9SetB-B | 混合色/M | 6999 | 300×250×40 | 570 g |
| BHY-102105-9SetB-L | 混合色/L | 6999 | 300×250×40 | 600 g |
| BHY-102105-9SetB-XL | 混合色/XL | 6999 | 300×250×40 | 630 g |
| BHY-102105-9SetB-XXL | 混合色/XXL | 6999 | 300×250×40 | 660 g |

价格结构：
```json
"currencyType": "USD",
"supplierPrice": null,                                    // 顶层为空
"siteSupplierPrices": [{"siteId":100,"supplierPrice":6999,"maxSupplierPrice":null}]
```
> **申报价写在 `siteSupplierPrices[].supplierPrice`，不是顶层 `supplierPrice`。**
> `6999` = **69.99 美元**（以分为单位，整数）。`siteId: 100` = 美国站。

包装（`productSkuWhExtAttr`）：
```json
"productSkuVolume": {"len":300,"width":250,"height":40, ...},
"productSkuWeight": {"value":540000,"inputValue":"540","inputUnit":"g"}
```
> `value` 单位是**克×1000**（540 g → 540000），提交时用 `inputValue: "540"` + `inputUnit: "g"`。

> ⚠ 尺码的 extCode 后缀是 `-B` 表示 M（不是 `-M`），这是该店的货号命名习惯
> （`S / B / L / XL / XXL`），**不是接口约束**，接口只认 `extCode` 字符串本身。

## 七、从草稿到真正发布，还缺什么

草稿里 `productId` / `goodsId` 都是 `null`，说明**还没有提交**。发布那一刻要调的接口本轮仍未抓到（浏览器被关掉了）。

已知的候选（来自接口清单，未验证）：
- 保存草稿：可能有 `product/draft/update` 之类的写入接口（**没见到**）
- 提交发布：页面底部有「保存」+「提交」，对应接口未知

## 八、复现命令

```bash
# 列草稿（26 个）
python scripts/goods/read_draft.py --port 61407 --list

# 读指定草稿，落盘到 out/draft/
python scripts/goods/read_draft.py --port 61407 --id 57682030962
```

端口取法：扫高位端口找 CDP → 反查 `chrome_<storeId>` 目录（BHY=27697436126374）。
本次 BHY 窗口端口为 **61407**。
