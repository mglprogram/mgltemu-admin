# TEMU 标题复刻参考（半自动流程）

用竞品 Top sales 标题提炼结构，再按防范规则改写。

## 一、抓竞品标题

```bash
# 需先在店铺窗口打开 www.temu.com（任意页面即可）
python scripts/goods/scrape_temu_www.py --port 61407 --q "9pcs hoodies" --scroll 8
python scripts/goods/scrape_temu_www.py --port 61407 --q "hooded fishing shirt" --scroll 6
```

- 自动点 `Sort by` → `Top sales`（成功后 URL 带 `&filter_items=1%3A1`）
- 标题从 `img[alt]` 取，剥掉 `item picture` 前缀
- 落盘到 `out/temu-www-scrape/search_<query>_<时间>.json`

**实测坑**：
- 页面懒加载，`--scroll` 给少了会漏；`8` 次约能拿到首屏 20~40 个
- `img[alt]` 里混有 `.png.slim.png` 这类非商品图，要按长度/内容过滤
- 抓到的标题里偶有空格断词（如 `built   shield`），是页面渲染问题，不影响结构判断

## 二、竞品标题骨架（2026-11-26，39 条样本）

```
[件数] + [性别] + [品类词] + [材质观感] + [功能1] + [功能2] + [场景]
```

| 词块 | 出现次数 | 典型写法 |
|---|---|---|
| 件数 | 24 | `9pcs` / `9 pack` / `nine pack` / `10 pack` |
| `mens` | 22 | — |
| `hoodie` / `hooded` | 37 | `hoodie` `hooded sweatshirt` `hooded shirt` |
| `breathable` | 18 | — |
| `long sleeve` | 16 | — |
| `lightweight` | 15 | — |
| `outdoor` | 15 | — |
| `fishing` | 14 | — |
| `quick dry` | 13 | — |
| `hiking` | 12 | — |

## 三、三项防范规则

### ① 颜色：不逐色罗列

多色款标题里**不写具体颜色**。竞品少数写 `navy grey black` 的，一旦实际 SKU 与标题不符即属描述不符。
要体现就用 `Multi Color`，且默认不加。

### ② 件数：必须与实物一致

件数是最强的点击词（24/39 都有），但**写成几就必须发几件**。
没确认实物件数前，用不带件数的安全版。

### ③ 违禁词：竞品在用 ≠ 你能用

从 39 条竞品标题里挑出的高风险表达：

| 词 | 风险 | 说明 |
|---|---|---|
| `UPF50` / `uv` / `sun protection` | 高 | 防晒功效，需资质 |
| `rash guard` / `rash prevention` / `rash resistant` | 高 | 暗示防护/医疗功能 |
| `windproof` | 中 | 功能性声明，可能要求举证 |
| `labor day gifts` / `as a surprise gift` | 中 | 节日/促销词，时效性强 |
| `sublimation blank` | 低 | 工艺描述，一般可写 |
| `with mask` / `built in mask` | 低 | 结构描述，客观可写 |

> ⚠ 这份清单是从竞品行为反推的，**不是 TEMU 官方词库**。
> 有官方词表时应用官方表复核。

## 四、产出

候选标题至少给 5 个版本，逐条标注借用了哪个竞品结构、规避了哪些词：

| 版本 | 用途 |
|---|---|
| A 主推 | 拍图卖点直译，含件数 |
| B 偏运动 | 用 `compression` / `workout` |
| C 偏户外 | 用 `outdoor` / `activewear` |
| D 安全版 | 不写件数，防件数不符 |
| E 卖点前置 | `Multi Pack N Pcs` 开头 |

## 五、写回草稿

选定后改 spec 的 `title`（中文名）/ `title_en`（英文名），再覆盖保存：

```bash
python scripts/goods/run_temu_publish.py --store BHY \
  --spec out/102105-9SetA/spec_102105-9SetA.json \
  --draft-id <草稿ID> --apply
```

## 实例：102105-9SetA（2026-11-26）

- 主图：TELALEO **9 PACK**，9 色连帽长袖
- 最终标题：`9 Pcs Men's Lightweight Hooded Long Sleeve Shirts Breathable Quick Dry Active Tops`（82 字符）
- 中文名：男士轻量连帽长袖速干运动上衣九件套
- 写回草稿 `51435953256`，回读 `title=True`
- 候选与对照见 `out/102105-9SetA/标题候选.md`
