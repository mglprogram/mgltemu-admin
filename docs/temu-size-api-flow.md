# 尺码表自动化：完整接口链路（2026-11-26 实测）

> 目标：不点页面，只用接口把尺码表挂到草稿上，并**验证后台真的渲染**。

## 一、三个接口就够了

```
① POST /anniston-agent-seller/sizespec/createSizeSpec   ← 建模板（商品私有）
② POST /visage-agent-seller/product/draft/save           ← 把模板 id 写进草稿
③ POST /visage-agent-seller/product/draft/query          ← 回读校验
```

页面上「点添加 → 选模板 → 确认 → 保存」这四步，实际只发了 ①（确认那一下）
和 ②（保存那一下）。**没有"挂载"专用接口**，挂载就是 ② 里的字段。

## 二、createSizeSpec 的真实请求体（从页面抓包，748 字节）

```json
{
  "content": {
    "meta": {
      "elements": [
        {"id": 10002, "name": "胸围全围"},
        {"id": 10003, "name": "衣长"},
        {"id": 10004, "name": "袖长"}
      ],
      "groups": [{"id": 1, "name": "尺码", "supplierUnnecessary": null}]
    },
    "records": [
      {"values": {"1": "S", "10002": "96", "10003": "63", "10004": "16"}},
      {"values": {"1": "M", "10002": "100", "10003": "65", "10004": "17"}},
      {"values": {"1": "L", "10002": "104", "10003": "67", "10004": "18"}},
      {"values": {"1": "XL", "10002": "110", "10003": "69", "10004": "19"}},
      {"values": {"1": "XXL", "10002": "116", "10003": "71", "10004": "20"}}
    ],
    "localSizeSource": 1,
    "generalSizeType": 1
  },
  "catId": 31910,
  "classId": 5,
  "name": "202301",
  "reusable": false,
  "ext": {
    "manualGroupIdList": [],
    "isDoubleSize": false,
    "unEnbleAutoSelectedList": [10001, 10002, 10003, 10004, 10005, 10024, 10011, 30011, 30010]
  },
  "productCreateTime": null
}
```

要点：

- `catId` 必须是**当前商品叶子类目**，`reusable` 必须 `false`
- `records` 只传 `values`，**不要传 valueList**
- `elements` 只传 `{id, name}`，**不要传 unit**
- `ext.unEnbleAutoSelectedList` 是必需字段；页面用的是
  `[10001,10002,10003,10004,10005,10024,10011,30011,30010]`（比我们早先写的多了 10011/30011/30010）
- 返回值只有 `{businessId, classId}`，**没有内容回显**

## 三、draft/save 里怎么带尺码表

三个字段都要写，缺一不能渲染：

```json
{
  "productDraftId": 55363913492,
  "sizeTemplateId": 17595938694909,
  "sizeTemplateIds": [17595938694909],
  "showSizeTemplateIds": [17595938694909]
}
```

- `sizeTemplateId`（单数）早期版本漏了，实测**只要数组字段也能渲染**
  （后台会用 `sizeTemplateIds[0]`），但补上更稳，和页面回读结构一致。
- 私有模板（`reusable=false`）在 `rollPageQueryData` 里**查不到**，
  只能从 `draft/query` 的 `sizeTemplateIds` 里拿到。

## 四、怎么验证真的挂上了（关键）

不要只看 `draft/query` 的 `sizeTemplateIds`——公共模板也会写进去但**不渲染**。
要开一个新标签页读 DOM：

```bash
python scripts/goods/_verify_size_dom.py 55363913492
```

判定标准（DOM 文本里尺码表区的样子）：

| 状态 | DOM |
|---|---|
| ✅ 挂上了 | `尺码表 | 已添加 | 查看 编辑 删除` |
| ❌ 没挂上 | `尺码表 | 添加尺码表 | 填写尺码信息...` |

> ⚠️ 必须用**新标签页**（`/json/new?url`）打开，同页刷新会命中前端缓存，
> 之前就是被这个骗了——`draft/query` 明明有值，页面却仍显示「+ 添加尺码表」。

## 五、抓包踩的坑

- `Network.requestWillBeSent` 的 `request.postData` 对大 body **经常是空的**，
  必须再调 `Network.getRequestPostData {requestId}` 取一次。
- 页面随时可能被关掉，监听脚本不要硬编码 `/goods/edit` 选择器，要兜底任选卖家中心页。
- `Fetch` 域拦截（`Fetch.enable`）会暂停请求，处理不当整页卡死；用 `Network` 域更安全。

## 六、代码位置

- 建模板：`temu_publish_build.build_sizespec_payload`（`cat_id` 必传、`reusable=False`）
- 选/建模板：`pick_size_template` / `ensure_size_template`（含 `size_spec_is_usable` 私有判定）
- 阶段入口：`run_temu_publish.py` 的 `stage_size`
- DOM 验证：`scripts/goods/_verify_size_dom.py`
- 抓包：`scripts/goods/_cap_size.py`
