# 尺码表：默认口径 + 「先查后建」

> 2026-11-26 用户口径。以后所有上链接的尺码都按这个来。

## 一、默认口径（不可自行加减）

| 品类 | 只填这三项 | TEMU 量体项 ID |
|---|---|---|
| 上衣（T恤/卫衣/背心/外套…） | 衣长、胸围、袖长 | `10003` / `10002` / `10004` |
| 裤子（长裤/短裤/卫裤/紧身裤） | 裤长、臀围、腰围 | `10005` / `10026` / `10024` |

- **肩宽、袖肥、裤口、前浪、后浪、裙长一律不填**，即使工厂尺码表里有。
- **「胸围」= TEMU 的「胸围全围」，填多少就是多少，不乘 2。**
  （用户 2026-11-26："这次的胸阔不乘以2"。工厂表写 96 就填 96。）
- 数值原样保留一位小数，不四舍五入。

spec 里这样写，剩下的交给 pipeline：

```json
"size_profile": "top",
"size_values": {
  "S":   {"衣长": 63, "胸围": 96,  "袖长": 16},
  "M":   {"衣长": 65, "胸围": 100, "袖长": 17},
  "L":   {"衣长": 67, "胸围": 104, "袖长": 18},
  "XL":  {"衣长": 69, "胸围": 110, "袖长": 19},
  "XXL": {"衣长": 71, "胸围": 116, "袖长": 20}
}
```

`size_profile` 可省略，会按类目名/title 猜（含 裤/pants/shorts → `pants`，否则 `top`）。
写了口径以外的字段（比如 `肩宽`）会**直接报错**，不会静默忽略。

## 二、先查后建（`size` 阶段）

```bash
python scripts/goods/run_temu_publish.py --store BHY --spec my_spec.json --stage size
```

流程：

1. 拉该店该类目下**所有**尺码表模板（按 `classId` 归属，`catId` 只是过滤条件）
2. 逐个比对：**量体项集合一致** 且 **每个码每个值一致** → 复用
3. 全都对不上 → 造新的；`--apply` 才真正 `createSizeSpec`

比对规则细节：

- 模板比本次多几码（S~XXXL 对 S~XXL）**不算冲突**，直接复用
- 模板缺本次要用的码 / 量体项多了少了 / 任何一个数值不同 → 都算不一致
- 报告会逐个模板列出前 4 处差异，方便判断是不是只是尺码不同

## 三、模板命名 = 货号数字（2026-11-26 用户定）

模板名直接用**产品货号里的数字**，不带前后缀：

| 货号 | 模板名 |
|---|---|
| `BHY-202301-10SetA` | `202301` |
| `BHY-102105-9SetA` | `102105` |

`size` 阶段会**先按名字找**：如果已有同名模板，且量体项与每码取值都一致，就直接复用。
这样同一个款的多个套装（10SetA / 10SetB / 7SetA）都指向同一张 `202301` 模板，
后台下拉里干净，也不会越建越多。

spec 里对应写：

```json
"size_template_name": "202301",
"size_template_id": 17595938497252
```

只写 `size_template_name` 也能跑（更稳，跨店不会指错 id 也不影响，因为模板本来就是这家店的）。

## 四、创建时必须 `catId=null + reusable=true`（★ 踩过的坑）

`createSizeSpec` 的 `catId` 决定模板进哪套清单：

| 传参 | 结果 |
|---|---|
| `catId=<叶子类目>` + `reusable=false` | 模板**只属于那一个商品**，`rollPageQueryData` 查不到，后台「选择尺码表模板」下拉也看不到 → 建完就丢，且**没有删除接口**，成孤儿 |
| `catId=null` + `reusable=true` | 进**店铺公共模板库**，列表立刻可查，下次同类目能复用 |

所以 pipeline 建模板一律走第二种。实测：`17595938465074`（带 catId）建完查不到；
`17595938484332` / `17595938489551`（不带 catId）列表里立刻可见。

## 五、已知限制

- **没有删除/更新接口**：`sizespec/deleteSizeSpec`、`updateSizeSpec` 都是 HTTP 405。
  建错了只能在后台手工删，或者留着一个新名字重建。
- 列表接口 `rollPageQueryData` 的 `offset` 翻不动（返回固定游标 18233784，翻页结果不变），
  只能拿到最近 `limit` 条，默认 10 条。要拿更多得加大 `limit`。
- `sizespec/query` 的 `reusable` 参数必须和创建时一致，否则返回空对象：
  公共模板用 `reusable=true` 查，商品私有模板用 `reusable=false` 查。

## 六、相关文件

- 口径与选模板：`scripts/goods/temu_publish_build.py`
  （`SIZE_PROFILES` / `normalize_size_values` / `size_spec_matches` /
   `pick_size_template` / `ensure_size_template` / `build_sizespec_payload`）
- 阶段入口：`scripts/goods/run_temu_publish.py` 的 `stage_size`
- 单测：`scripts/goods/tests/test_temu_size_profile.py`、`test_size_template_reusable.py`
