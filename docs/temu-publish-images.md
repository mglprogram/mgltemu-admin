# TEMU 上架：图片要求与上传（部分实测）

店铺：BHY（TM-BHY-YJF），2026-11-26

## 一、图片位置模板（✅ 实测 200）

```
POST /anniston-agent-seller/image/upload/template/queryTemplate
headers: content-type: application/json + mallid
body:    {"catId": 30467}          # 叶子类目 ID，必填
```

不传 `catId` → `{"success":false,"errorCode":1000002,"errorMsg":"Category ID cannot be empty"}`

### 返回（类目 30467 男士时尚帽衫）

```json
{"result":{"templateVO":{"itemList":[
  {"itemId":5582445866,"itemType":1,"itemRequirement":"","sampleFile":"",
   "positions":[
     {"positionId":1,"positionType":"正面图","positionDesc":"展示商品正面效果"},
     {"positionId":2,"positionType":"背面图","positionDesc":"展示商品背面效果"},
     {"positionId":3,"positionType":"","positionDesc":"高清大图"},
     {"positionId":4,"positionType":"车缝细节","positionDesc":"展示车缝工艺细节"},
     {"positionId":5,"positionType":"面料纹理","positionDesc":"展示服饰面料纹理细节"}
   ]},
  {"itemId":5638274236,"itemType":3,"itemRequirement":"图片需避免难以辨认的小字","sampleFile":"","positions":[]},
  {"itemId":5983953918,"itemType":2,"itemRequirement":"","sampleFile":"","positions":[]}
]}}}
```

**要点**：
- `itemType:1` = 商品图（带 positions，5 个位置）
- `itemType:3` = 合规/资质图（有额外要求文案）
- `itemType:2` = 其他
- **不同类目模板不同**，必须按叶子类目查

## 二、上传接口：没找到（如实说明）

扫了发布页全部 68 个 JS chunk，`image/upload/*` 只命中一个 `queryTemplate`（模板查询），
**没有找到真正的文件上传接口**。

判断：
- 上传大概率走**共享组件库**（`@temu` 通用上传组件）或独立上传域名，
  不在「上架」这条业务线的 chunk 里
- 之前流量抓包里也从未出现过上传请求（因为没传过图）

参考：同一页还有 `/visage-agent-seller/product/image/dimensionImageCheckCatInGray`（尺寸图校验），
说明图片相关接口分散在 `visage-agent-seller/product/image/*` 与 `anniston-agent-seller/image/*` 两处。

## 三、草稿里图片字段的实际形状

`draft/query` 返回（本稿全部为空）：

| 字段 | 类型 | 含义 |
|---|---|---|
| `carouselImageUrls` | array | 轮播图 URL |
| `carouseVideoVOList` | array | 轮播视频 |
| `detailVideoVOList` | array | 详情视频 |
| `materialImgUrl` | string | 素材图 |
| `outerPackageImages` | array | 外包装图 |
| `guideFilePageImages` | array | 资质文件页图 |
| `imageLanguageList` | null | 图片语言（多语言素材灰度用） |
| `productSkcCarouselImageI18nVOList` | array | SKC 级多语言轮播图 |
| `productSkcImageExtList` | array | SKC 扩展图 |
| `colorImageUrl` | string | 颜色图 |

**结论**：草稿存的是 **URL 数组**，说明上传是独立一步（先传得到 URL，再写进草稿）。

## 四、下一步要拿上传接口，最省力的方式

在你手动传一张图的时候挂一次轻量监听，只需抓那一下。
之前抓包失败是因为同时挂太多页面 + 拉响应体导致互锁，只挂单页、只抓请求不拉响应体是稳的。

```bash
# 已经可用（不 reload、单页、只记 URL/方法/请求体）
python scripts/goods/watch_publish_flow.py --port 61407 --out out/up
```

## 五、本轮新增工具

| 脚本 | 用途 |
|---|---|
| `scan_js_for.py` | 在页面已加载 JS 里搜任意关键词，报路径 + chunk |
| `find_save_api.py` | 专找 draft/save 这类接口路径 |
| `inspect_api_call.py` | 打印某接口在源码里的调用上下文 |

```bash
python scripts/goods/scan_js_for.py --port 61407 --pattern "image/upload"
python scripts/goods/inspect_api_call.py --port 61407 --api product/draft/save --chunk product-create-edit
```
