# TEMU 图片上传完整链路（实测，请求体+响应体齐全）

店铺：BHY（TM-BHY-YJF），2026-11-26 14:28–14:30
触发：在草稿页 `goods/edit?productDraftId=57682030962` 手动上传 3 张图
原始数据：`out/imgup3/imgup_20261126-142802.jsonl` / `.log`

> 这是**实测**结果，不是推测。每条请求的请求体与响应体都完整录到了。

---

## 一、完整时序（真实上传 3 张）

```
① POST /phoenix-mms/material/folder/query           {}  → 文件夹（无关紧要）
② POST /phoenix-mms/material/pageQuery              素材列表（2333 张）
③ POST /general_auth/get_signature?...              取上传签名
④ POST /api/galerie/v3/store_image                  直传图片（multipart）
⑤ POST /phoenix-mms/material/render/compress        裁剪压缩（可选）
⑥ POST /phoenix-mms/material/queryByMd5             MD5 查重
⑦ POST /phoenix-mms/material/create                 登记素材（status=0）
⑧ POST /api/galerie/v3/store_image                  直传（第二张）
⑨ POST /phoenix-mms/material/create                 登记
⑩ POST /phoenix-mms/material/edit                   回写 URL（status=3）★成功标志
⑪ POST /phoenix-mms/material/edit                   回写
⑫ POST /api/galerie/v3/store_image                  直传（第三张，带 unadorned 关联）
⑬ POST /phoenix-mms/material/create                 登记（带 imgUrl）
```

---

## 二、① 取签名

```
POST /general_auth/get_signature?tag_name=product-material-tag&scene_id=agent-seller
content-type: application/json
body: {"bucket_tag":"product-material-tag"}

res: {"signature":"<约 600 字符的 base64 串，按需现取，不要硬编码>"}
```

- `tag_name` / `bucket_tag` = `product-material-tag`
- `scene_id` = `agent-seller`
- 签名**可复用**（3 张图用同一个签名）

## 三、② 直传图片（关键：multipart）

```
POST /api/galerie/v3/store_image?tag_name=product-material-tag
content-type: multipart/form-data; boundary=----WebKitFormBoundary...

字段:
  url_width_height = "true"
  image            = <二进制>
      Content-Disposition: form-data; name="image"; filename="xxx.jpg"
      Content-Type: image/jpeg
  upload_sign      = <①的签名>

res: {"url":"https://img.kwcdn.com/product/21ae960ae2/f00b8d19-...._1350x1800.jpeg",
      "width":1350, "height":1800, "size":335356,
      "etag":"b4fa9236afcebde5efedbe387bb511e3"}
```

**要点**：
- **`etag` 就是 MD5**，后续 `material/create` 的 `materialMd5` 用它
- `url` 是原图地址；**最终展示用的是 `material/edit` 返回的 `fancy/` 处理图**
- `url_width_height: true` → 返回里带真实宽高

## 四、⑤ 裁剪压缩（可选）

```
POST /phoenix-mms/material/render/compress
body: {"requestItemList":[{"originImageUrl":"https://img.kwcdn.com/..._1500x1500.jpeg",
                           "cropSizeMode":4}]}

res: {"result":{"compressMap":{
       "https://img.kwcdn.com/.../origin_1500x1500.jpeg":
       "https://img.kwcdn.com/product/fancy/29af554b-....jpg"}}}
```

## 五、⑥ 查重

```
POST /phoenix-mms/material/queryByMd5
body: {"materialType":1, "md5IdList":["b4fa9236afcebde5efedbe387bb511e3",
                                       "9fc80e9b3ef1a9911f8c938e65d83675"]}

res: {"result":{"responseDetailList":[]}}      # 空 = 库里没有，需要新建
```

> 我单独试过 `{"materialType":1,"md5":"..."}` 也能返回 200（但恒为空），
> **批量查重必须用 `md5IdList` 数组**。

## 六、⑦ 登记素材

```
POST /phoenix-mms/material/create
body: {"folderId":0,
       "createDetailList":[{"materialType":1,
                            "materialMd5":"b4fa9236afcebde5efedbe387bb511e3",
                            "materialName":"6b1e1edc-05c7-4b63-aaf2-e4fa680a3104"}]}

res: {"result":{"responseDetailList":[{
        "id":261126198692793,
        "materialName":"6b1e1edc-...",
        "alreadyExists":false,
        "uploadStatus":0,            ← 刚登记，还没图
        "materialMd5":"b4fa...",
        "imgUrl":""}]}}
```

变体（第三张图，带原图关联）：
```json
{"folderId":0,"materialProcessingTag":3,
 "createDetailList":[{"materialType":1,"materialMd5":"9fc8...",
   "materialName":"微信图片_20240224173933-22",
   "unadornedMaterialId":261126198669786,
   "imgUrl":"https://img.kwcdn.com/product/21ae960ae2/914be429-..._1350x1800.jpeg"}]}
```
- `unadornedMaterialId` = 关联的原素材 id（同一张图的未修饰版本）
- `materialProcessingTag: 3` = 处理类型

## 七、⑩ 回写 URL（★ 这一步才算成功）

```
POST /phoenix-mms/material/edit
body: {"id":261126198692793,
       "url":"https://img.kwcdn.com/product/21ae960ae2/f00b8d19-..._1350x1800.jpeg",
       "materialName":"6b1e1edc-05c7-4b63-aaf2-e4fa680a3104",
       "uploadStatus":3,
       "materialType":1}

res: {"result":{"id":261126198692793,
                "uploadStatus":3,
                "imgUrl":"https://img.kwcdn.com/product/fancy/dc9297c1-....jpg"}}
```

**`uploadStatus` 状态机**：
- `0` = 已登记未上传
- `3` = 完成（有 `fancy/` 处理图）
- `5` = 失败/待重试（`pageQuery` 里 `uploadStatusList:[5,1,2]` 就是在查这些）
- `1`、`2` = 处理中

**最终写进草稿的应该是 `imgUrl`（`fancy/` 那张处理图），不是原图 URL。**

## 八、素材列表查询（有用的附带信息）

```
POST /phoenix-mms/material/pageQuery
body: {"pageInfo":{"pageNo":1,"pageSize":50},
       "uploadStatusList":[3], "folderId":0, "materialTypeList":[1],
       "minMaterialHeight":1785, "minMaterialWidth":1340,
       "maxMaterialSize":2097152, "proportion":"3:4", "proportionList":["3:4"]}
```

返回本店 **2333 张**素材。单条字段：

```
id, supplierId, materialName, materialType, uploadStatus, materialSource,
materialSize, height, width, materialFormat, uploadTime,
productBindCount, productItemList, failReason,
materialMd5, originalMaterialUrl, firstImageUrl, destMaterialUrl, materialDuration
```

### 图片规格（来自 pageQuery 参数，很有用）

| 限制 | 值 |
|---|---|
| 最小宽 | 1340 px |
| 最小高 | 1785 px |
| 最大文件 | 2097152 B = **2 MB** |
| 比例 | **3:4** |

> 注意：这是**列表筛选**参数，说明商品素材的标准就是 3:4、≥1340×1785、≤2MB。
> 实际传的图 1350×1800 是通过的（略大于下限）。

---

## 九、脚本

```bash
# 已验证可用的监听器（请求体+响应体都取）
python scripts/goods/watch_image_upload2.py --port 61407 --out out/imgup3 --minutes 15
```

> ⚠ 之前版本 `watch_image_upload.py` 有 bug（在 `loadingFinished` 里读不存在的
> `request.url`，导致 0 条），已废弃，用 `watch_image_upload2.py`。

## 十、要不要自己实现上传

如果要脚本化上传，最小路径：

1. `get_signature` 拿签名（可复用一段时间）
2. `store_image` multipart 传文件 → 拿 `etag`(md5) 和 `url`
3. `material/create` 登记 → 拿素材 `id`
4. `material/edit` 回写 `url` + `uploadStatus:3` → 拿最终 `imgUrl`
5. 把 `imgUrl` 写进草稿的 `carouselImageUrls`

**替代方案**：`material/pageQuery` 能看到已有 2333 张素材，如果图已在库里，
可直接按 `materialMd5` 命中复用，省掉 2~4 步。
