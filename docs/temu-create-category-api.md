# TEMU 上架「选类目」接口探测（2026-11-26）

探测对象：`https://agentseller.temu.com/goods/create/category`（BHY 店，合作仓域 `agentseller.temu.com`）

## 结论

该页是 qiankun 微前端，首屏只打 **2 个**类目相关接口，都是 `POST` + JSON，都**不需要 `Anti-Content` 签名头**，只要 cookie 里的 `mallid` 放到请求头就能调通。这是好消息 —— 现有 `api_hx_stock.py` 那套改库存的接口要 `Anti-Content`（会过期、要重新抓），类目接口没有这个负担。

| 接口 | 作用 | 请求体 | 返回 |
|---|---|---|---|
| `/anniston-agent-seller/category/children/list` | 取类目树（按父节点逐层下钻） | `{}` → 全部 25 个一级类目<br>`{"parentCatId": 27011}` → 该节点的直接子节点 | `result.categoryNodeVOS[]` |
| `/visage-agent-seller/product/queryCatHints` | 类目提示（热门/推荐叶子路径） | `{}`（空 body，仍要带 `mallid`） | `result.catHints[].cat1..cat10` |

### 请求格式

```
POST https://agentseller.temu.com/anniston-agent-seller/category/children/list
content-type: application/json
mallid: <cookie 里的 mallid>          # 实测本店 634418228526171
body: {"parentCatId": 27011}

→ {"success":true,"errorCode":1000000,"errorMsg":null,
   "result":{"categoryNodeVOS":[{"catId":27012,"catName":"传统服装","catEnName":null,
    "catLevel":2,"catType":0,"parentCatId":27011,"isLeaf":false,"isHidden":false,
    "hiddenType":0,"isSemiManagedHidden":false,"semiManagedHiddenType":0}, ...]}}
```

### 鉴权实测（三档）

| 请求头 | children/list | queryCatHints |
|---|---|---|
| 只 `content-type`（无 mallid） | **403** `{"error_code":400020037,"error_msg":"No Permission to Access"}` | 403 |
| `+ mallid` | **200** ✅ | **200** ✅ |
| `+ Anti-Content` | 不需要 | 不需要 |

### 下钻参数名（易错点）

第一版试 `{"catId": 27011}` → **返回的还是 25 个根**，看着像成功了，其实是参数被忽略。
逐个试了 `parentCatId / parentId / parentCategoryId / catId / categoryId / parent_cat_id / id / catID / parentCat / pCatId`，**只有 `parentCatId` 生效**（返回 27011 的 12 个子节点，且结果里不再含 27011 自己）。

> 判据：生效 ⇒ 返回的是子节点、首条不是根；不生效 ⇒ 原样返回 25 个根。

### 节点字段

`catId, catName, catEnName, catLevel, catType, parentCatId, isLeaf, isHidden, hiddenType, isSemiManagedHidden, semiManagedHiddenType`

- `isLeaf: true` ⇒ 可选到这一级就停（上架时用它的 `catId`）
- `catLevel` 从 1 递增；实测最深 7 级（保暖内衣 → 男士保暖内衣上衣）
- `catType: 0` = 中间层，`1` = 叶子（`catHints` 里的末级）

### 已验证的完整链路

`--hint 2` 用 `queryCatHints` 给的路径反查 `children/list`，逐级命中到叶子：

```
服装、鞋靴和珠宝饰品(27011) > 男士时尚(30327) > 男装(30328) > 男士休闲运动服(30416)
  > 男士打底和压缩单品(30445) > 保暖内衣(30448) > 男士保暖内衣上衣(30449)  ★叶子
```

另有一条最简单的 3 级链：`CD和黑胶唱片(1) > 电子音乐、DJ舞曲(2) > 氛围音乐(4)` ★叶子。

## 类目规模（2026-11-26 实抓，深度 ≤ 3）

`data/temu-category/category_tree_20261126.json`（672 KB）：一级 **25**、二级 **326**、三级 **3500**，合计 **3851** 个节点，344 次串行 XHR 约 40 秒。深度 ≤ 3 内已经是叶子的有 1633 个 —— 很多类目 3 级就到底了，剩下的要更深。**

## 脚本

| 脚本 | 用途 |
|---|---|
| `scripts/goods/probe_create_category.py` | 打开类目页 + 抓包，列出所有类目相关请求，落 `out/probe_create_category/hits_*.json` |
| `scripts/goods/probe_create_category_req.py` | 抓 `method / postData / headers`（谁带了什么头） |
| `scripts/goods/probe_create_category_replay.py` | 复放接口，三档鉴权对照 + 下钻试错 |
| `scripts/goods/probe_category_children_arg.py` | 参数名爆破（找 `parentCatId`） |
| `scripts/goods/api_category_tree.py` | **可用工具**：`--hint N` 验链路、`--find 关键字` 搜类目路径、`--full` 全树落盘 |

用法：

```bash
python scripts/goods/api_category_tree.py --port 63854 --hint 5
python scripts/goods/api_category_tree.py --port 63854 --find 护膝
python scripts/goods/api_category_tree.py --port 63854 --full   # 落 data/temu-category/
```

## 店铺窗口 CDP 端口对照（2026-11-26）

紫鸟的 CDP 端口是**随机高位端口**，不在固定区。取法：

```powershell
# 1) 扫高位端口找 CDP
foreach ($p in (Get-NetTCPConnection -State Listen | % LocalPort | Sort-Object -Unique)) {
  try { $null = Invoke-RestMethod "http://127.0.0.1:$p/json/version" -TimeoutSec 1; "CDP $p" } catch {}
}
# 2) 端口 → PID → 店铺 userdata 目录（chrome_<storeId>）
```

本次实测：

| storeId | 店 | 端口 |
|---|---|---|
| 27697436126374 | TM-BHY-YJF | **63854** |
| 27677489075954 | TM-BEL-MGL | 54050 |
| 27364108847582 | TM-KinglamanSport | 61644 |

⚠ `store open` 返回 `reused: true` 时**不会**新开页到 `--url`，已开着的那个窗口里的页面不变 —— 别以为它打开了你给的那个 URL。

## 踩坑

1. **CDP 里不能用 `return fetch(...)`**：`NetCapture.eval` 是 `awaitPromise: False`，返回 Promise 拿不到结果；而且页面 fetch 下外层包装会报 `Illegal invocation`。用**同步 XHR**（`x.open("POST", url, false)`）最稳。
2. **同一页面上 CDP 重复求值，`const X_` 会报 `Identifier 'X_' has already been declared`** —— 变量要么包进 IIFE 参数，要么用 `var`。
3. **返回体别截断**：一开始 `slice(0,3000)` 把 JSON 切断了，`json.loads` 直接报 `Unterminated string`。类目树单个响应可能几十 KB。
4. **`{"catId": ...}` 假成功**：参数名不对时接口不报错，只是忽略参数返回根节点 —— 必须用「返回里还含不含父节点自己」来判定是否真生效。
5. **`store open` 的 `reused: true`**：不会把已存在窗口导航到你给的 URL。
