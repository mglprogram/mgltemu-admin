# 多复色（多组 SKC + 多组图）

> 2026-11-27 实测固化。用户口径：**一个链接可以同时上多个复色，每组一套图。**
> 第二个写「混合色1」，第三个「混合色2」，依次递增。

## 一、TEMU 的硬规则

| 规则 | 说明 |
|---|---|
| **一个颜色只能一个 SKC** | 两组都写「混合色」→ 第二个被**静默吞掉**，`draft/save` 仍返回 success，不报错 |
| **颜色名必须互不相同** | 第 2 组起用自定义名「混合色1 / 混合色2 / …」（`specId=0` 自由文本） |
| **extCode 必须唯一** | SKC 的 `extCode` 和每个 SKU 的 `extCode` 都不能撞；撞了那条会被**静默丢弃** |

第一条和第三条是同一个坑的两面：平台按颜色去重 SKC、按 extCode 定位 SKU，
重复的静默不报错，所以 pipeline 里必须本地先拦。

## 二、spec 写法

```json
{
  "cat_id": 31883,
  "title_en": "...",
  "skcs": [
    {"ext_code": "BHY-102105-9SetD",   "color": "混合色",  "images": [...], "sizes": {...}},
    {"ext_code": "BHY-102105-9SetD-B", "color": "混合色1", "images": [...], "sizes": {...}},
    {"ext_code": "BHY-102105-9SetD-C", "color": "混合色2", "images": [...], "sizes": {...}},
    {"ext_code": "BHY-102105-9SetD-D", "color": "混合色3", "images": [...], "sizes": {...}}
  ]
}
```

每个 SKC 的 `sizes` 里各码 `ext_code` 也要带自己的后缀（`...-B-S`、`...-C-S`）。

## 三、自动补序号（`color` 可以不写）

`skcs` 有多条时，`color` 重复或缺失会自动改名：

| 输入 color | 输出 color |
|---|---|
| `混合色` / `混合色` / `混合色` | `混合色` / `混合色1` / `混合色2` |
| `混合色` / (空) / (空) | `混合色` / `混合色1` / `混合色2` |
| `混合色` / `黑色` / `灰色` | 原样保留 |

⚠️ **不会自动改 ext_code**——ext_code 是货号，必须你自己保证唯一，
撞了会直接报错（`SKC extCode 重复` / `SKU extCode 重复`）并终止。

## 四、自定义颜色的两个副作用

1. **不进 `productSpecPropertyReqs`**：那里只放类目标准表里的值。
   `specId=0` 的自定义色放进去会让整条属性的 vid/specId 变 0，干扰后台校验。
   所以 `productSpecPropertyVOS` 里只有第一条「混合色」，这是正常的。
2. **后台颜色列显示的就是「混合色1」这种文本**，能看到、能选。已实测刷新正常。

## 五、验证结果（草稿 50589964144）

```
BHY-102105-9SetD    颜色=混合色     5 SKU
BHY-102105-9SetD-B  颜色=混合色1    5 SKU
BHY-102105-9SetD-C  颜色=混合色2    5 SKU
BHY-102105-9SetD-D  颜色=混合色3    5 SKU
```

`verify` 回读 `skc=(4, 4) sku=(20, 20)`，DOM 尺码表区正常。

## 六、踩过的坑

- **只提交 1 个 SKC 会把其余全删掉**：`draft/save` 对 SKC 是**整体替换**，不是合并。
  所以改一个色也要把全部 SKC 一起提交（spec 里写全）。
- 早期测试「4 个只成 2 个」不是数量上限，是**货号撞了**：
  当时第二个 SKC 的 SKU 货号还沿用第一个的前缀，被静默丢弃。

## 七、代码位置

- 自动补色号 + 唯一性校验：`temu_publish_build.build_draft_save_payload`
  （`skc_specs` 处理段 + `# 6.5) ext_code 唯一性校验`）
- 自定义色解析：`_resolve_sale_spec(..., allow_custom=True)`
- 单测：`scripts/goods/tests/test_multi_color.py`（8 项）
