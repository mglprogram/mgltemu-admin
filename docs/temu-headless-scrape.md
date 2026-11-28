# 无头浏览器抓 TEMU 前台热销标题：为什么走不通

> 2026-11-28 实测记录。用户要求「改成无头打开浏览器，用完就关闭」，
> 结论：**无头方案不可用**，热销标题仍必须借紫鸟店铺窗口的已登录标签页。

## 一、结论

| 方案 | 结果 |
|---|---|
| 无头 + 临时 profile | 100% 跳 `login.html`，只看到「Continue with Google」 |
| 无头 + 注入 `.temu.com` 全部 cookie（39 个） | 仍是 `login.html`（cookie 顶不住） |
| 无头 + 持久 profile（人工有头登录过一次） | 过了登录墙，但搜索页**商品永远为空** |
| 有头 + 同一持久 profile | 一样空 |
| 紫鸟店铺窗口（已登录卖家账号） | 也跳 `login.html`（⚠ 见下） |

## 二、为什么搜索页拿不到商品

拦截 XHR 看到真正原因：

```
403 /api/seo/get_page_seo_data
429 /api/poppy/v1/search_suggest?scene=search_suggest
429 /api/poppy/v2/search_activation?scene=search_activation
```

搜索接口被限流（429）。表现是：

- `document.querySelectorAll('img[alt]').length` = **44**（全是图标/页脚图）
- `a[href*="-g-"]` = **0**（一条商品链接都没有）
- 页面正文只有页脚文案（约 1812 字符）
- 首页 `temu.com/` **能**出商品（`imgAlt=131`、正文 7094 字符）

试过的无效手段：UA 覆盖、`navigator.webdriver` 抹除、`window.chrome` 伪造、
地理定位/locale/timezone 覆盖、`Accept-Language` 头、从首页搜索框输入再提交、
换关键词（hoodies / shoes / t-shirt / 9pcs hoodies）、换 URL 形态。

## 三、保留的实现（`scripts/goods/scrape_temu_headless.py`）

代码留着，因为「用完就关闭」这套骨架本身是对的，只是数据拿不到：

```bash
python scripts/goods/scrape_temu_headless.py --q "9pcs hoodies"   # 默认持久 profile
python scripts/goods/scrape_temu_headless.py --login              # 有头登录一次，存持久 profile
python scripts/goods/scrape_temu_headless.py --temp-profile       # 一次性临时 profile（必被踢）
```

设计要点（这些经验仍有效）：

- `--headless=new`：老版 `--headless` 拿不到元素布局尺寸，取不到按钮坐标
- 持久 profile 放 `data/temu-www-profile/`；有头窗口关掉后要清 `LOCK` /
  `Singleton*` 才能被无头复用
- profile 里有 Google 登录态（`.google.com`、`accounts.google.com`），但没用

## 四、实际可用的路径

```bash
# 借紫鸟店铺窗口（默认，也是唯一稳定的）
python scripts/goods/run_temu_publish.py --store BHY --spec my_spec.json \
  --stage title --title-query "9pcs hoodies"
```

`--title-backend headless` 参数保留，但实测抓不到商品，仅作备用。
抓不到时用 `--title-offline` 完全跳过抓取，只用 spec 自带 `title_facts` 生成。

## 五、代码位置

- 无头实现：`scripts/goods/scrape_temu_headless.py`
- 紫鸟实现（默认）：`scripts/goods/scrape_temu_www.py`
- 阶段入口：`run_temu_publish.py` 的 `stage_title(..., backend=...)`
- 参数：`--title-backend {www,headless}`
