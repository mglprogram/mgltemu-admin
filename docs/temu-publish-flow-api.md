# TEMU 上架（新链接）接口探测 — 第二轮：发布流程

探测对象：`/goods/create/category` → `/goods/edit`（商品发布表单）
店铺：BHY（TM-BHY-YJF，mallid `634418228526171`）
时间：2026-11-26

> 第一轮（只探类目树）见 `temu-create-category-api.md`。
> **本轮大量结论是「看到了接口但没拿到响应体」，逐条标注了可信度，别当成已验证。**

---

## 一、关键发现：发布页就是 `/goods/edit`

`/goods/create/category` 走完第 1 步（选站点 → 选类目 → 下一步）后，页面**跳转**到：

```
/goods/edit?productDraftId=<草稿ID>&from=menu&st_time=<ts>
```

也就是说：
- **新建 = 带 `productDraftId` 的 `goods/edit`**，不是另一个独立页面
- 菜单里「商品发布」的 route 就是 `/goods/edit`
- `/goods/create/publish` 是**空壳路由**（不在权限表里，页面显示「该区暂无权限」）

### 权限：建商品是允许的

`POST /bert/api/page/info/agentSeller/pageInfo`（带 `mallid` 头）返回 **422 条页面权限**，`/goods/create/*` 只有一条：

```
/goods/create/category     ✅ 有权限
/goods/create/publish      ❌ 不在权限表
/goods/edit                ✅ 有权限
```

---

## 二、第 1 步（选类目）实测确认

| 步骤 | 接口 | 结论 |
|---|---|---|
| 选站点 | 无接口 | 纯前端下拉，选项含「美国站/加拿大站/英国站/…」 |
| 选发货仓 | `/darwin-mms/.../sales/stock/querySiteCanSelectWarehouseList` | 选站点后才可用，返回仓列表（kansas / 新泽西2仓 / 加州三仓 / 恒信加州3 / 乐航加州3仓） |
| 选类目 | `/anniston-agent-seller/category/children/list` | ✅ 已验证（第一轮） |
| 仓库路由模板 | `/darwin-mms/.../route/queryWarehouseGoodsRouteTemplateList` | 选类目后触发 |
| **下一步** | `POST /visage-agent-seller/product/draft/add` | **创建草稿，返回 `productDraftId`** |

**必填顺序（踩过的坑）**：不选「经营站点」→ 点下一步只出红字「请选择经营站点」，**不发任何请求**；不选发货仓 → 红字「不能为空」。

---

## 三、第 2 步（基本信息）打出的接口

打开 `goods/edit?productDraftId=...` 后实测打到 **约 45 个**业务接口。按功能归类：

### 类目 / 模板 / 属性
| 接口 | 作用 |
|---|---|
| `/anniston-agent-seller/category/path/list` | 类目路径（面包屑） |
| `/anniston-agent-seller/category/template/query` | **类目属性模板（核心：属性字段定义）** |
| `/visage-agent-seller/config/common/pg/template/query` | 页面模板配置 |
| `/anniston-agent-seller/product/check/cat/queryConfig` | 类目级校验配置 |
| `/anniston-agent-seller/product/check/common/queryConfig` | 通用校验配置 |
| `/phoenix-mms/model/queryMapping` | 属性值映射 |
| `/anniston-agent-seller/category/template/vehicle/library/match` | 车型库（部分类目） |
| `/visage-agent-seller/product/queryGuideFileRule` | 资质文件规则 |
| `/visage-agent-seller/product/queryProductSensitiveLimitInfo` | 敏感词/限售校验 |

### 品牌 / 白名单
| 接口 | 作用 |
|---|---|
| `/anniston-agent-seller/category/queryWhiteList` | 类目白名单 |
| `/anniston-agent-seller/category/brand/property/value/pageQuery` | **品牌属性值列表** |

### 尺码 / SKU
| 接口 | 作用 |
|---|---|
| `/anniston-agent-seller/sizespec/query` | 尺码规格列表 |
| `/anniston-agent-seller/sizespec/querySizeSpecConfig` | 尺码规格配置 |
| `/anniston-agent-seller/sizespec/querySizeSpecClassCatByClassId` | 按类目取尺码表 |
| `/anniston-agent-seller/sizespec/queryChGroupSizeList` | 尺码分组 |
| `/anniston-agent-seller/sizespec/querySizeSpecMetaByClassId` | 尺码元数据 |
| `/anniston-agent-seller/sizespec/allowRange` | 尺码允许范围 |
| `/anniston-agent-seller/sizespec/rollPageQueryData` | 尺码分页数据 |
| `/anniston-agent-seller/sizespec/createSizeSpec` | 新建尺码规格 |
| `/anniston-agent-seller/sizespec/element/measurementImages/list` | 测量图 |
| `/anniston-agent-seller/sku/spec/parent/list` | **SKU 规格父级（维度定义）** |
| `/anniston-agent-seller/config/common/size/query` | 通用尺码配置 |

### 价格 / 物流
| 接口 | 作用 |
|---|---|
| `/visage-agent-seller/product/sku/site/suggestedPrice/gray/query` | **建议申报价（灰度）** |
| `/visage-agent-seller/product/estimatedFreightAmount/query` | **预估运费** |
| `/visage-agent-seller/freight/template/list` | 运费模板列表 |
| `/visage-agent-seller/supplier/mall/settle/time` | 结算时间 |

### 图片 / 素材
| 接口 | 作用 |
|---|---|
| `/visage-agent-seller/product/image/dimensionImageCheckCatInGray` | 尺寸图校验 |
| `/anniston-agent-seller/image/upload/template/queryTemplate` | 图片上传模板 |
| `/visage-agent-seller/product/queryMaterialMultiLanguages` | 多语言素材 |

### 草稿 / 商品本体
| 接口 | 作用 |
|---|---|
| `POST /visage-agent-seller/product/draft/add` | **建草稿** |
| `/visage-agent-seller/product/draft/query` | **读草稿（含全部已填字段）** |
| `/visage-agent-seller/product/query` | 读商品 |
| `/visage-agent-seller/product/accessory/pageQuery` | 配件 |

### 站点 / 语言
`/visage-agent-seller/config/common/{site,region,supplier}/query`、
`/bg-brando-mms/goods/site/language/relation/list`（**语言关系：英文名必填的依据**）

### 交互中触发（用户填表时）
| 接口 | 触发时机 |
|---|---|
| `/visage-agent-seller/risk/extCode/batchCheck` | **校验货号（MSKU）是否重复** |
| `/visage-agent-seller/supplier/entity/officeAddress/query` | 产地/办公地址 |
| `/visage-agent-seller/category/demo/images/list` | 类目示例图 |
| `/visage-agent-seller/config/common/category/query` | 类目配置 |

---

## 四、页面表单结构（DOM 实测）

商品发布页（`goods/edit?productDraftId=...`）第 2 步的必填/分组，肉眼可见：

```
经营站点            美国站（只读，来自第 1 步）
商品分类            <7 级路径>  修改
*素材语言           英语/西班牙语/葡萄牙语/法语/俄语/阿拉伯语/韩语（多选）
*商品名称           + 名称结构推荐（[节日活动]+[几件装]+[人群]+…）
*英文名称
*商品产地           + 产地证明（选填）
*商品属性           属性填写率 0/25
                   材质 Material、护理说明、领型、面料、是否透明、图案、细节、
                   成分（比例合计须=100%）、版型、织造方式、面料纹理1/2、
                   里料纹理、适用人群、场景、次要材质、门襟、袖型、袖长、长度、
                   品牌名、面料克重 …
```

---

## 五、抓包基础设施（重要踩坑，供后续复用）

本轮花了大量时间在抓包工具本身，结论：

1. **紫鸟 CDP 端口是随机高位端口**，要靠扫端口 + 反查 `chrome_<storeId>` 定位（BHY=63854）。
2. **不能 `el.click()` 点 React 控件** —— React 走合成事件，必须用 CDP `Input.dispatchMouseEvent` 发真实鼠标序列。
3. **`store open` 返回 `reused:true` 时不会导航到你给的 URL**。
4. **页面导航会重置 CDP 连接**（`WinError 64 / no close frame`）—— 这是紫鸟的特性，
   导致「挂包 + reload 录首屏」这条路不可靠。
5. **一条 ws 只能有一个 `recv()` 消费者**。把事件读循环和 `getResponseBody` 放两个协程里
   各自 await，会互锁 → body 全空（本轮 77 条请求 0 条带 body 的直接原因）。
6. `Network.enable` 只听**开启之后**的请求，页面先加载完再挂包，首屏那批永远录不到。

**因此：想拿完整流量，必须让页面在挂包之后才开始加载，且不能靠 reload 触发（会断连）。**
更稳的路子是**直接读后端已存的草稿**（`product/draft/query`），而不是抓填表过程。

---

## 六、未完成项（诚实标注）

| 项目 | 状态 |
|---|---|
| 各接口的**请求体/响应体结构** | ❌ 全部没拿到（getResponseBody 因互锁失败） |
| 属性模板的具体字段/必填规则 | ❌ 只知道页面有 25 个属性，不知道接口返回结构 |
| SKU/尺码的组合方式与 payload | ❌ 只知道调了哪些接口 |
| 价格字段与申报价规则 | ❌ 仅知道有 `suggestedPrice` 和 `estimatedFreightAmount` |
| 图片上传接口 | ❌ 只看到 `queryTemplate`，没看到真正的上传 |
| 保存/提交（发布）接口 | ❌ 完全没抓到 |
| 草稿 `productDraftId=57682030962` 的内容 | ❌ 未读取（浏览器已关闭） |

**下一步最省力的路径**：重新打开 BHY 窗口 → 用 `product/draft/query` 直接读那个填好的草稿，
一次性拿到完整字段结构（类目、属性、SKU、价格、图片全在里面），比抓包可靠得多。
