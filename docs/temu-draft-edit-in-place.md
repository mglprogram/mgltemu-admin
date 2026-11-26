# 草稿原地修改（不新建）

> 2026-11-26 用户明确要求：**「修改内容以后在原草稿修改」**。

## 结论

改一个已经存在的草稿，只要把 `productDraftId` 带进 `draft/save` 请求体就能原地覆盖：

```bash
python scripts/goods/run_temu_publish.py --store BHY --spec my_spec.json \
  --stage plan,apply,verify --apply --draft-id 55363913492
```

输出里会标 `（原地更新）`，不会出现 `draft/add → 新草稿`。

## 实测细节

`draft/query` 回读里**根本不返回** `productDraftId` / `productSkcId` / `productSkuId` / `goodsId`
（全是 `null`），所以不可能从回读里"继承"这些 id。但把这三个 id 都传 `0`、
只带 `productDraftId` 依旧能正确覆盖：

| 字段 | 传值 | 结果 |
|---|---|---|
| `productDraftId` | 目标草稿 ID | 必填，决定改哪条 |
| `productSkcId` | `0` | 平台按 `extCode`（如 `BHY-202301-10SetA`）定位，够用 |
| `productSkuId` | `0` | 同上，按 SKU 的 `extCode` 定位 |
| `goodsId` / `productId` | 不传 | 草稿阶段为空，无需传 |

验证方式：改一次 `productName` → `draft_list` 立刻能看到新标题；再改回原值 → 同样生效。
草稿 ID 不变、列表条数不变。

## 为什么必须原地改

- **草稿没有删除接口**：`product/draft/delete`、`draft/batchDelete` 都返回 HTTP 405；
  扫了 50 个 JS chunk 也没找到删除相关的方法名。
  记录：`deleteDraft`、`draft/delete`、`removeDraft` 均无命中。
- 也就是说每 `draft/add` 一次，列表里就多一条**只能人工在后台删**的废草稿。
- 所以 pipeline 的规则是：**第一次建（不带 `--draft-id`），之后所有修正都带 `--draft-id`**。

## 相关代码

- `scripts/goods/run_temu_publish.py` 的 `stage_apply`：`did` 有值就标记 `reused=True`，
  跳过 `draft/add`，直接 `draft/save`。
- `--draft-id` 也可以写进 spec（`"product_draft_id": 55363913492`），这样不用每次敲参数。
