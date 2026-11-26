# TEMU `draft/save` 请求体完整结构（实测）

店铺：BHY（TM-BHY-YJF），2026-11-26 14:34:02
触发：在草稿页 `goods/edit?productDraftId=57682030962` 点「保存」，监听器旁听记录
原始请求体：`out/draftsave/draft_save_payload.json`（**12,506 字节 / 41 个顶层字段**）

> 这是**实发请求**，不是推测。响应 `{"success":true,"errorCode":1000000,"errorMsg":null,"result":{}}`

---

## 一、接口

```
POST /visage-agent-seller/product/draft/save
content-type: application/json
mallid: 634418228526171
```

**不需要 `Anti-Content`。** 响应成功时 `result` 是空对象 `{}`。

---

## 二、响应与副作用（已验证）

保存后回读 `draft/query`，草稿从 19,878 → 21,919 字节，变化：

| 字段 | 保存前 | 保存后 |
|---|---|---|
| `carouselImageUrls` | `[]` | 2 个 `fancy/` URL |
| `materialImgUrl` | `""` | `.../temu-avi/image-crop/96707a5a....jpg` |
| `productSkcList[0].previewImgUrls` | `[]` | 2 个 URL |
| `productSkcList[0].colorImageUrl` | `null` | `.../fancy/84bfec16....png` |
| `productSkcList[0].productSkcImageExtList` | `[]` | `[{imgUrl, imgTag:0, isOcr:null}] × 2` |

---

## 三、41 个顶层字段

```
类目（10 个平铺字段，不是嵌套！）
  cat1Id..cat10Id

名称
  productName                    "title title title title"
  productI18nReqs                [{productName, language}]   language="en"
  materialMultiLanguages         []

属性 / 规格
  productPropertyReqs            list[17]   商品属性
  productSpecPropertyReqs        list[6]    颜色/尺码定义
  goodsModelReqs                 list[1]    模特试穿

SKU / 尺码
  productSkcReqs                 list[1]    SKC（含 5 个 SKU）
  sizeTemplateIds                [17595937603747]
  showSizeTemplateIds            [17595937603747]

图片 / 视频
  carouselImageUrls              [2 个 fancy URL]
  carouselImageI18nReqs          []
  materialImgUrl                 "https://img.kwcdn.com/product/temu-avi/image-crop/..."
  productOuterPackageImageReqs   []
  productCarouseVideoReqList     []
  productDetailVideoReqList      []

扩展属性
  productWhExtAttrReq            {outerGoodsUrl, productOrigin}
  productSaleExtAttrReq          {bodyShape, manufacturingLocation}
  productNonAuditExtAttrReq      {california65WarningInfoReq, cosmeticInfoReq}
  goodsLayerDecorationReqs       []
  goodsLayerDecorationCustomizeI18nReqs  []
  goodsAdvantageLabelTypes       []

物流 / 站点
  productShipmentReq             {freightTemplateId, shipmentLimitSecond}
  productWarehouseRouteReq       {targetRouteList:[{warehouseId, siteIdList}]}
  productSemiManagedReq          {bindSiteIds, semiLanguageStrategy}

资质 / 合规
  productGuideFileNewReqList     []
  productGuideFileI18nReqs       []
  productOriginCertFileReqs      []
  sensitiveTransNormalFileReqs   []
  productOuterPackageReq         {}

其他
  personalizationSwitch          0
  productDraftId                 57682030962
```

---

## 四、关键子结构

### 类目：10 个平铺字段

```json
{"cat1Id":27011,"cat2Id":30327,"cat3Id":30328,"cat4Id":30465,"cat5Id":30467,
 "cat6Id":0,"cat7Id":0,"cat8Id":0,"cat9Id":0,"cat10Id":0}
```
> **和 `draft/query` 返回的 `categories`（嵌套 cat1~cat10 对象）结构不同**，
> 写回时要拍平。不足 10 级填 `0`。

### SKC

```json
{"previewImgUrls":[...],
 "productSkcCarouselImageI18nReqs":[],
 "extCode":"BHY-102105-9SetB",
 "mainProductSkuSpecReqs":[...],
 "productSkuReqs":[5 个],
 "productSkcId":0,                  ← 新建草稿是 0
 "colorImageUrl":".../fancy/....png",
 "isBasePlate":false}
```

### SKU（最复杂的一层）

```json
{"thumbUrl":"https://img.kwcdn.com/product/fancy/....jpg",
 "productSkuThumbUrlI18nReqs":[],
 "extCode":"BHY-102105-9SetB-S",
 "siteSupplierPrices":[{"siteId":100,"supplierPrice":6999}],   ← 申报价
 "currencyType":"USD",
 "productSkuSpecReqs":[
   {"parentSpecId":1001,"parentSpecName":"颜色","specId":21263,
    "specName":"混合色","groupId":11,"vid":26419},
   {"parentSpecId":3001,"parentSpecName":"尺码","specId":10004,
    "specName":"S","groupId":2,"vid":315}],
 "productSkuId":0,
 "productSkuUsSuggestedPriceReq":{},
 "productSkuWhExtAttrReq":{
   "productSkuVolumeReq":{"len":300,"width":250,"height":40,
     "inputLen":"30","inputWidth":"25","inputHeight":"4","inputUnit":"cm"},
   "productSkuWeightReq":{"value":540000,"inputValue":"540","inputUnit":"g"},
   "productSkuBarCodeReqs":[],
   "productSkuSensitiveAttrReq":{"isSensitive":0,"sensitiveList":[]}},
 "productSkuStockQuantityReq":...,
 "productSkuAccessoriesReq":...,
 "productSkuNonAuditExtAttrReq":...}
```

**注意两点**：
1. `supplierPrice: 6999` 是**分**（69.99 USD）
2. 体积要同时给 `len/width/height`（毫米）和 `inputLen/inputWidth/inputHeight`（厘米字符串）+ `inputUnit:"cm"`

### 站点 / 物流

```json
"productSemiManagedReq":{"bindSiteIds":[100],"semiLanguageStrategy":2}
"productShipmentReq":{"freightTemplateId":"HFT-17641354508533933163",
                      "shipmentLimitSecond":172800}          // 48 小时
"productWarehouseRouteReq":{"targetRouteList":[
   {"warehouseId":"WH-04940925214853163","siteIdList":[100]},
   {"warehouseId":"WH-09107063234693163","siteIdList":[100]}]}
```

### 产地

```json
"productWhExtAttrReq":{
  "outerGoodsUrl":"",
  "productOrigin":{"countryShortName":"CN","region2Id":43000000000006}}
```

### 模特试穿

```json
"goodsModelReqs":[{"modelId":2603040384118,"modelName":"Jared","modelType":1,
  "modelFeature":1,"modelProfileUrl":"...","modelHeight":"177.5",
  "modelBust":"98","modelWaist":"82","modelHip":"98",
  "sizeSpecId":9005,"sizeSpecName":"M","tryOnResult":1}]
```

---

## 五、`draft/query` → `draft/save` 的字段映射（写回要用）

| 读（query 返回） | 写（save 入参） | 变化 |
|---|---|---|
| `categories.cat1.catId` … `cat10` | `cat1Id` … `cat10Id` | **嵌套 → 平铺** |
| `productName` | `productName` | 同名 |
| `productI18nList` | `productI18nReqs` | List → Reqs |
| `productPropertyList` | `productPropertyReqs` | List → Reqs |
| `productSpecPropertyVOS` | `productSpecPropertyReqs` | VOS → Reqs |
| `productSkcList` | `productSkcReqs` | List → Reqs |
| `productSkcList[].productSkuList` | `productSkcReqs[].productSkuReqs` | 层级内改名 |
| `productWhExtAttr` | `productWhExtAttrReq` | 加 Req |
| `productSaleExtAttr` | `productSaleExtAttrReq` | 加 Req |
| `productNonAuditExtAttr` | `productNonAuditExtAttrReq` | 加 Req |
| `carouselImageUrls` | `carouselImageUrls` | 同名 |
| `sizeTemplateIds` | `sizeTemplateIds` | 同名 |
| `goodsModelList` | `goodsModelReqs` | List → Reqs |

**规律**：`List → Reqs`、`VOS → Reqs`、`XxxExtAttr → XxxExtAttrReq`；类目是唯一需要拍平的。

---

## 六、需要新建时要注意

- `productSkcId: 0`、`productSkuId: 0`、`productSkuId` 全为 0 → 表示新建
- `productDraftId` 必须带（从 URL query 取）
- 保存成功后 `result` 是 `{}`，**不返回新 ID**；要拿最新状态得再调 `draft/query`

---

## 七、脚本

```bash
# 旁听保存（只监听，不发请求）
python scripts/goods/watch_draft_save.py --port 61407 --out out/draftsave --minutes 20

# 回读草稿确认
python scripts/goods/read_draft.py --port 61407 --id 57682030962
```
