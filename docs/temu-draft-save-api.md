# TEMU 保存草稿接口（实测确认）

来源：`product-create-edit.7f5d05a7.chunk.js` 第 503,387 字符处的真实调用代码。
店铺：BHY（TM-BHY-YJF），2026-11-26

## 一、接口

```
POST /visage-agent-seller/product/draft/save
headers: content-type: application/json
         mallid: <cookie 里的 mallid>        # 634418228526171
```

**不需要 `Anti-Content`**（与 `draft/query`、`children/list` 一致）。

### 证据（chunk 源码原样）

```js
ne = useCallback(async () => {
  const e = f().parse(location.search) || {},
        t = Number(e.productDraftId) || 0,
        a = M.formApi.getValues(),
        { errorMsg: r, isEmptySkc: i } = (0, ft.AQ)(a, M);
  if (r) y.default.error(r);
  else {
    m(!0);
    try {
      const r = await (0, T.hi)({ value: a, formStore: M, grayInfo: b }),
            l = (0, T.Mi)({ formStore: M });
      await (o = {
          ...l,
          ...r,
          productSkcReqs: i ? [] : r.productSkcReqs,
          sellOutProductId: Number(e.soldOutId) || void 0,
          productDraftId: t
        },
        (0, X.b)(le.Response, "/visage-agent-seller/product/draft/save", o, undefined)
      );
      (0, ge.uB)({ durationTrackInfo: M.durationTrackInfo,
                   operateType: "saveDraft", productDraftId: t, productId: void 0 });
      const c = h.default.alert({ ... "saveDraft 成功弹窗" });
```

## 二、请求体怎么拼

```
o = {
  ...(0, T.Mi)({ formStore: M }),          // 基础字段
  ...(0, T.hi)({ value, formStore, grayInfo }),   // 表单字段（后面覆盖前面）
  productSkcReqs: isEmptySkc ? [] : r.productSkcReqs,
  sellOutProductId: Number(soldOutId) || undefined,   // 清仓模式才有
  productDraftId: 57682030962                          // 从 URL query 取
}
```

要点：
- **`productDraftId` 从 URL 的 `?productDraftId=` 取**，不是从表单里取
- **`productSkcReqs` 在「无 SKC」时会被强制置空 `[]`**（`isEmptySkc` 判定）
- 提交前先跑校验 `(0, ft.AQ)(values, store)`，有 `errorMsg` 就直接弹错、不发请求
- 保存成功后会弹 `check-circle_filled` 成功框，同时发埋点 `operateType:"saveDraft"`

## 三、与草稿读接口的关系

之前读到的草稿结构（`draft/query` 返回的 39 字段）就是 `draft/save` 请求体的**回形状**。
两者的字段名高度对应：

| 草稿读字段 | 说明 |
|---|---|
| `productDraftId` | 草稿 ID（save 的入参） |
| `categories` | 类目链 cat1~cat10 |
| `productName` / `productI18nList` | 名称 + 多语言 |
| `productPropertyList` | 商品属性（17 条） |
| `productSpecPropertyVOS` | 规格属性（颜色 1001 / 尺码 3001） |
| `productSkcList` | SKC 列表，内嵌 `productSkuList` |
| `carouselImageUrls` / `guideFilePageImages` | 图片（本稿为空） |
| `sizeTemplateId` / `sizeTemplateIds` | 尺码模板 |
| `productWhExtAttr` | 包装（体积/重量） |

**推断（未验证）**：`draft/save` 大概率接受与 `draft/query` 同构的 `result` 对象，
即「读出来 → 改字段 → 原样写回」。要确认只能真发一次保存请求。

## 三之二、请求体字段命名规律（从源码归纳）

虽然没能把 `T.hi` / `T.Mi` 的内部实现完整扒出来，但从同一 chunk 里多处调用可以看出
请求字段的命名规律（**同一套 apiData 对象被复用**）：

| 字段名 | 含义 |
|---|---|
| `productName` | 商品名 |
| `productI18nReqs` | 多语言名称（数组，形如 `[{language, productName}]`） |
| `goodsI18nReqs` | 商品级多语言（`inMultipleSpecLangGray` 灰度下才带） |
| `productPropertyReqs` | **商品属性数组** |
| `productSkcReqs` | **SKC 数组**（内含 SKU） |
| `productSizeTemplateReq` | `{sizeTemplateIds, showSizeTemplateIds}` |
| `productSaleExtAttrReq` | 销售扩展属性（含 `ipCodes`、`productNoCharger`） |
| `productGuideFileNewReqList` / `productGuideFileI18nReqs` | 资质文件 |
| `ipCodes` | 知识产权/产地码 |
| `apiParams` / `apiData` | 上述整体的容器名 |

属性数组的结构（源码里显式构造，可直接照抄）：

```js
productProperties: o.map(e => {
  const t = a.find(t => t.refPid === e.refPid),
        r = t?.values?.find(t => t.vid === e.vid);
  return {
    templatePid: t?.templatePid, pid: t?.pid, refPid: e.refPid,
    propName: e.propName, vid: e.vid, propValue: e.propValue,
    valueUnit: e.valueUnit, valueExtendInfo: r?.extendInfo,
    numberInputValue: e.numberInputValue
  };
})
```

> 这与 `draft/query` 读回来的 `productPropertyList` **字段完全一致** ——
> 佐证了「读出来 → 改 → 原样写回」的推断。

**结论**：`draft/save` 的入参基本就是 `draft/query` 返回的 `result`，
把 `productPropertyList` → `productPropertyReqs`、`productSkcList` → `productSkcReqs`
这类「List → Reqs」改名后即可。**但仍需实发一次确认。**

### 保存函数里还做了这些事（源码可见）

1. `(0, ft.AQ)(values, store)` 先校验，返回 `errorMsg` → 直接弹错、**不发请求**
2. 同时返回 `isEmptySkc` → 决定 `productSkcReqs` 传 `[]` 还是真数据
3. `forPage` 是「商品发布」时，还会走 `ze(...)` 检查**资质文件**是否必需
   （`needGuideFile` 为真时弹警告框，`continue:false` 中断提交）
4. 成功后才发埋点 `operateType:"saveDraft"`

## 四、相关接口一览

| 接口 | 作用 | 验证程度 |
|---|---|---|
| `POST product/draft/pageQuery` | 列草稿 | ✅ 实测 200 |
| `POST product/draft/query` | 读单条草稿 | ✅ 实测 200，拿到 39 字段 |
| `POST product/draft/save` | **保存草稿** | ✅ 代码确认，⚠ 未实发 |
| `POST product/draft/add` | 新建草稿（返回 productDraftId） | ✅ 第一轮实测触发过 |
| 提交发布 | 未知 | ❌ 未找到 |

## 五、下一步（等人工检查完再动）

按你的要求**不做提交**，只保存。要验证 `draft/save`，最稳的做法是：

1. 在草稿页手动改一个无害字段（比如名称末尾加个空格）
2. 点保存
3. 我再调 `draft/query` 回读，确认字段被写入

这样既不提交、也不影响你后续人工检查。

## 六、工具

```bash
# 列草稿
python scripts/goods/read_draft.py --port 61407 --list
# 读草稿
python scripts/goods/read_draft.py --port 61407 --id 57682030962
# 扫页面 JS 找接口路径
python scripts/goods/find_save_api.py --port 61407
# 看某接口在源码里的调用上下文
python scripts/goods/inspect_api_call.py --port 61407 --api product/draft/save --chunk product-create-edit
```
