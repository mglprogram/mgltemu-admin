# -*- coding: utf-8 -*-
"""TEMU 上链接（草稿发布）API 封装。

复用本机已登录的紫鸟店铺窗口（CDP 协议）：
  - 只借用 cookie + 同源 XHR，不导航、不刷新用户正在操作的页面；
  - 仅需 `mallid` 请求头（本模块涵盖的类目/模板/草稿/素材接口均**无需 Anti-Content**）；
  - 单连接串行 RPC + `ping_interval=None`，规避 1011 ping timeout 与 recv 互锁坑。
"""
from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import mimetypes
import sys
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
GOODS_DIR = Path(__file__).resolve().parent
POWER_LIB = ROOT / "scripts" / "power" / "lib"

for _p in (str(GOODS_DIR), str(POWER_LIB)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import cdp_net  # noqa: E402
import websockets  # noqa: E402
from ziniao_port import probe_port  # noqa: E402

BASE = "https://agentseller.temu.com"

#: 店铺简称 → 紫鸟窗口标题前缀 & storeId
STORES: dict[str, dict[str, str]] = {
    "BHY": {"window": "TM-BHY-YJF", "store_id": "27697436126374"},
    "BEL": {"window": "TM-BEL-MGL", "store_id": "27677489075954"},
    "KLS": {"window": "TM-KinglamanSport", "store_id": "27364108847582"},
}

#: 同步 XHR（JSON POST）
_JS_POST_SYNC = (
    '(function(U_,B_,H_){try{var x=new XMLHttpRequest();x.open("POST",U_,false);'
    'x.setRequestHeader("content-type","application/json");'
    'for(var k in H_){try{x.setRequestHeader(k,H_[k]);}catch(e){}}'
    'x.send(JSON.stringify(B_));'
    'return JSON.stringify({s:x.status,b:String(x.responseText||"")});}'
    'catch(e){return JSON.stringify({s:0,b:"XHR_ERR "+String(e)});}})'
    "(%s,%s,%s)"
)

#: 异步 FormData 上传（multipart/form-data，用于 store_image）
_JS_UPLOAD_IMAGE = (
    "new Promise(function(resolve){try{"
    "var b64=%s,mime=%s,fn=%s,sign=%s,url=%s;"
    "var bin=atob(b64),arr=new Uint8Array(bin.length);"
    "for(var i=0;i<bin.length;i++){arr[i]=bin.charCodeAt(i);}"
    "var blob=new Blob([arr],{type:mime});"
    "var fd=new FormData();"
    'fd.append("url_width_height","true");'
    'fd.append("image",blob,fn);'
    'fd.append("upload_sign",sign);'
    "var x=new XMLHttpRequest();"
    'x.open("POST",url,true);x.withCredentials=true;'
    "x.onload=function(){resolve(JSON.stringify({s:x.status,b:String(x.responseText||'')}));};"
    'x.onerror=function(){resolve(JSON.stringify({s:0,b:"XHR_NET_ERR"}));};'
    "x.send(fd);"
    '}catch(e){resolve(JSON.stringify({s:-1,b:"JS_ERR "+String(e)}));}})'
)

_JS_MALLID = (
    "(function(){var ps=document.cookie.split(';');"
    "for(var i=0;i<ps.length;i++){var p=ps[i].trim();"
    "if(p.indexOf('mallid=')===0)return p.substring(7);}return '';})()"
)


def resolve_port(store: str, port: int = 0) -> int:
    """按店铺名探紫鸟 CDP 端口；已显式传 --port 则直接复用。"""
    if port and int(port) > 0:
        return int(port)
    key = store.upper()
    if key not in STORES:
        raise ValueError("未知店铺 %r，可选：%s" % (store, ", ".join(STORES)))
    p = probe_port(STORES[key]["window"], attempts=3)
    if not p:
        raise RuntimeError(
            "探不到 %s（窗口 %s）的 CDP 端口，请先在紫鸟打开该店铺"
            % (key, STORES[key]["window"])
        )
    return int(p)


def pick_page_ws(port: int) -> tuple[str, str]:
    """挑一个已打开的 agentseller.temu.com 页面，返回 (ws_url, page_url)。"""
    targets = cdp_net.http_json("http://127.0.0.1:%d/json/list" % int(port))
    cands = [
        t
        for t in targets
        if t.get("type") == "page"
        and BASE in (t.get("url") or "")
        and "/auth/" not in (t.get("url") or "")
        and "/login" not in (t.get("url") or "")
    ]
    if not cands:
        raise RuntimeError(
            "端口 %d 上没有已登录的 agentseller.temu.com 页面（可能未打开或掉登录态）" % port
        )
    cands.sort(key=lambda x: 0 if "/goods/" in (x.get("url") or "") else 1)
    return cands[0]["webSocketDebuggerUrl"], (cands[0].get("url") or "")


class TemuSession:
    """复用单条 CDP 连接执行同源 API 调用。"""

    def __init__(self, port: int):
        self.port = int(port)
        self.ws_url, self.page_url = pick_page_ws(self.port)
        self.mallid = ""

    async def _eval(self, expr: str, await_promise: bool = False, timeout: float = 60.0) -> Any:
        async with websockets.connect(
            self.ws_url,
            max_size=128 * 1024 * 1024,
            ping_interval=None,
            ping_timeout=None,
        ) as w:
            await w.send(
                json.dumps(
                    {
                        "id": 1,
                        "method": "Runtime.evaluate",
                        "params": {
                            "expression": expr,
                            "returnByValue": True,
                            "awaitPromise": bool(await_promise),
                            "userGesture": True,
                        },
                    }
                )
            )
            end = asyncio.get_running_loop().time() + timeout
            while True:
                left = end - asyncio.get_running_loop().time()
                if left <= 0:
                    raise TimeoutError("CDP Runtime.evaluate 超时（%.0fs）" % timeout)
                try:
                    raw = await asyncio.wait_for(w.recv(), min(left, 10.0))
                except asyncio.TimeoutError:
                    continue
                msg = json.loads(raw)
                if msg.get("id") != 1:
                    continue
                res = msg.get("result") or {}
                if res.get("exceptionDetails"):
                    raise RuntimeError(
                        "页面 JS 执行异常: %s" % json.dumps(res["exceptionDetails"], ensure_ascii=False)[:300]
                    )
                return (res.get("result") or {}).get("value")

    def get_mallid(self) -> str:
        if self.mallid:
            return self.mallid
        v = str(asyncio.run(self._eval(_JS_MALLID, timeout=20)) or "").strip()
        if not v:
            raise RuntimeError("未能从 %s 的 cookie 读到 mallid（请确认已登录）" % self.page_url)
        self.mallid = v
        return v

    def post(self, path: str, body: dict | None = None, timeout: float = 60.0) -> dict:
        """发送 POST JSON 请求并校验 HTTP 200 + success=True。"""
        url = path if path.startswith("http") else (BASE + path)
        hdr = {"mallid": self.get_mallid()}
        js = _JS_POST_SYNC % (
            json.dumps(url),
            json.dumps(body if body is not None else {}),
            json.dumps(hdr),
        )
        raw = asyncio.run(self._eval(js, await_promise=False, timeout=timeout))
        resp = json.loads(raw or "{}")
        status = resp.get("s")
        text = resp.get("b") or ""
        if status != 200:
            raise RuntimeError("HTTP %s %s → %s" % (status, path, text[:300]))
        try:
            data = json.loads(text)
        except Exception as exc:  # noqa: BLE001
            raise RuntimeError("响应非 JSON (%s): %s" % (path, text[:250])) from exc
        if not data.get("success", True) or (
            "success" in data and not data.get("success")
        ):
            raise RuntimeError(
                "接口业务失败 %s: code=%s msg=%s"
                % (path, data.get("errorCode"), data.get("errorMsg"))
            )
        return data

    def post_raw(self, path: str, body: dict | None = None, timeout: float = 60.0) -> dict:
        """与 post 相同，但保留 success=False 的 JSON 给调用方判断。"""
        url = path if path.startswith("http") else (BASE + path)
        hdr = {"mallid": self.get_mallid()}
        js = _JS_POST_SYNC % (
            json.dumps(url),
            json.dumps(body if body is not None else {}),
            json.dumps(hdr),
        )
        raw = asyncio.run(self._eval(js, await_promise=False, timeout=timeout))
        resp = json.loads(raw or "{}")
        if resp.get("s") != 200:
            raise RuntimeError("HTTP %s %s → %s" % (resp.get("s"), path, (resp.get("b") or "")[:300]))
        return json.loads(resp.get("b") or "{}")

    def upload_store_image(self, file_path: Path, upload_sign: str, timeout: float = 90.0) -> dict:
        """通过 galerie/v3/store_image 上传本地图片文件，返回 {url, width, height, size, etag}。"""
        raw_bytes = file_path.read_bytes()
        b64 = base64.b64encode(raw_bytes).decode("ascii")
        mime = mimetypes.guess_type(str(file_path))[0] or "image/jpeg"
        url = BASE + "/api/galerie/v3/store_image?tag_name=product-material-tag"
        js = _JS_UPLOAD_IMAGE % (
            json.dumps(b64),
            json.dumps(mime),
            json.dumps(file_path.name),
            json.dumps(upload_sign),
            json.dumps(url),
        )
        out = asyncio.run(self._eval(js, await_promise=True, timeout=timeout))
        resp = json.loads(out or "{}")
        if resp.get("s") != 200:
            raise RuntimeError("store_image 失败 HTTP %s: %s" % (resp.get("s"), (resp.get("b") or "")[:300]))
        return json.loads(resp.get("b") or "{}")

    # ---------------- 业务接口封装 ----------------

    def site_list(self) -> list[dict]:
        r = self.post("/visage-agent-seller/config/common/site/query", {})
        return (r.get("result") or {}).get("siteBaseList") or []

    def warehouse_list(self, site_ids: list[int]) -> list[dict]:
        r = self.post(
            "/darwin-mms/api/kiana/ghost/btg/sales/stock/querySiteCanSelectWarehouseList",
            {"siteIdList": [int(x) for x in site_ids]},
        )
        dtos = (r.get("result") or {}).get("warehouseDTOList") or []
        out: list[dict] = []
        for d in dtos:
            out.extend(d.get("validWarehouseList") or [])
        return out

    def freight_templates(self, site_ids: list[int] | None = None, wh_ids: list[str] | None = None) -> list[dict]:
        body: dict[str, Any] = {}
        if site_ids:
            body["siteIdList"] = [int(x) for x in site_ids]
        if wh_ids:
            body["warehouseIdList"] = list(wh_ids)
        r = self.post("/visage-agent-seller/freight/template/list", body)
        return (r.get("result") or {}).get("freightTmplSimpleList") or []

    def category_children(self, parent_cat_id: int = 0) -> list[dict]:
        body = {"parentCatId": int(parent_cat_id)} if parent_cat_id else {}
        r = self.post("/anniston-agent-seller/category/children/list", body)
        return (r.get("result") or {}).get("categoryNodeVOS") or []

    def category_path(self, cat_id: int) -> dict:
        r = self.post("/anniston-agent-seller/category/path/list", {"catIds": [int(cat_id)]})
        lst = (r.get("result") or {}).get("queryCatPathRespList") or []
        if not lst:
            raise RuntimeError("查不到类目 %s 的完整路径" % cat_id)
        return lst[0].get("categoryPathVO") or {}

    def category_hints(self) -> list[dict]:
        r = self.post("/visage-agent-seller/product/queryCatHints", {})
        return (r.get("result") or {}).get("catHints") or []

    def category_template(self, cat_id: int) -> dict:
        r = self.post("/anniston-agent-seller/category/template/query", {"catId": int(cat_id)})
        return r.get("result") or {}

    def image_template(self, cat_id: int) -> dict:
        r = self.post("/anniston-agent-seller/image/upload/template/queryTemplate", {"catId": int(cat_id)})
        return (r.get("result") or {}).get("templateVO") or {}

    def spec_parents(self, cat_id: int) -> list[dict]:
        r = self.post("/anniston-agent-seller/sku/spec/parent/list", {"catId": int(cat_id)})
        return (r.get("result") or {}).get("parentSpecVOList") or []

    def sizespec_list(self, cat_id: int | None = None, offset: int = 0, limit: int = 50) -> list[dict]:
        body: dict[str, Any] = {"offset": int(offset), "limit": int(limit)}
        if cat_id:
            body["catId"] = int(cat_id)
        r = self.post("/anniston-agent-seller/sizespec/rollPageQueryData", body)
        return (r.get("result") or {}).get("sizeSpecDataVOList") or []

    def sizespec_detail(self, business_id: int, reusable: bool = False) -> dict:
        """按 businessId 取尺码表全量数值。

        ⚠ 参数名是 `businessId`，不是 `sizeSpecId`；
          传错会报 `Size Spec ID cannot be null`（1000002）。

        返回 sizeSpecDataVO，其中 contentVO.records[].values 形如
          {"1":"S", "10002":"100", "10003":"71", "10004":"77.5"}
          key 含义见 meta.elements（1=尺码, 10002=胸围全围, 10003=衣长, 10004=袖长）
        """
        r = self.post(
            "/anniston-agent-seller/sizespec/query",
            {"businessId": int(business_id), "reusable": bool(reusable)},
        )
        return (r.get("result") or {}).get("sizeSpecDataVO") or {}

    def sizespec_create(self, payload: dict) -> int:
        """新建尺码表模板，返回新的 businessId。

        请求体结构来自前端 `Ft` 构造器（7681 chunk @188686）：
            {"name": 模板名,
             "content": {...contentVO,
                         "meta": {"elements": [{id,name,unit}],
                                  "groups":   [{id,name}]},
                         "records": [{values:{...}}]},
             ...其余 contentVO 顶层字段}

        ⚠ 这是**新建**，不会改动源模板；要改数值请先 sizespec_detail 取出，
          改 records 后用 build_sizespec_payload 组装再调本方法。
        """
        r = self.post("/anniston-agent-seller/sizespec/createSizeSpec", payload, timeout=60.0)
        bid = (r.get("result") or {}).get("businessId")
        if not bid:
            raise RuntimeError("createSizeSpec 未返回 businessId: %s"
                               % json.dumps(r, ensure_ascii=False)[:300])
        return int(bid)

    def extcode_batch_check(self, ext_codes: list[str]) -> dict:
        r = self.post("/visage-agent-seller/risk/extCode/batchCheck", {"extCodes": list(ext_codes)})
        return r.get("result") or {}

    def draft_list(self, page: int = 1, page_size: int = 50) -> dict:
        r = self.post("/visage-agent-seller/product/draft/pageQuery", {"page": int(page), "pageSize": int(page_size)})
        return r.get("result") or {}

    def draft_query(self, draft_id: int | str) -> dict:
        r = self.post("/visage-agent-seller/product/draft/query", {"productDraftId": int(draft_id)})
        return r.get("result") or {}

    def draft_add(
        self,
        cat_id: int,
        bind_site_ids: list[int],
        warehouse_route_req: dict,
    ) -> int:
        body: dict[str, Any] = {
            "catId": int(cat_id),
            "productSemiManagedReq": {"bindSiteIds": [int(s) for s in bind_site_ids]},
            "productWarehouseRouteReq": warehouse_route_req,
        }
        r = self.post("/visage-agent-seller/product/draft/add", body)
        did = (r.get("result") or {}).get("productDraftId")
        if not did:
            raise RuntimeError("draft/add 未返回 productDraftId: %s" % json.dumps(r, ensure_ascii=False)[:300])
        return int(did)

    def draft_save(self, payload: dict) -> dict:
        r = self.post("/visage-agent-seller/product/draft/save", payload, timeout=90.0)
        return r

    # ---------------- 图片素材接口 ----------------

    def get_upload_signature(self, bucket_tag: str = "product-material-tag") -> str:
        path = "/general_auth/get_signature?tag_name=%s&scene_id=agent-seller" % bucket_tag
        url = BASE + path
        hdr = {"mallid": self.get_mallid()}
        js = _JS_POST_SYNC % (json.dumps(url), json.dumps({"bucket_tag": bucket_tag}), json.dumps(hdr))
        raw = asyncio.run(self._eval(js, await_promise=False, timeout=30.0))
        resp = json.loads(raw or "{}")
        if resp.get("s") != 200:
            raise RuntimeError("get_signature 失败 HTTP %s" % resp.get("s"))
        sig = (json.loads(resp.get("b") or "{}")).get("signature") or ""
        if not sig:
            raise RuntimeError("get_signature 返回空 signature")
        return sig

    def material_query_by_md5(
        self, md5_list: list[str], material_type: int = 1
    ) -> list[dict]:
        """按 MD5 查素材库（只回 id / materialStatus，**不含 imgUrl**）。

        ⚠️ 不要用它判断「是否已上传过」：`materialStatus=3` 表示素材记录已建，
        但不代表处理图 URL 可读（实测 3 的照片 material/edit 仍能补出 fancy URL）。
        需要 fancy URL 时统一走 `material_ensure_uploaded()`。
        """
        r = self.post(
            "/phoenix-mms/material/queryByMd5",
            {"materialType": int(material_type), "md5IdList": list(md5_list)},
        )
        return (r.get("result") or {}).get("responseDetailList") or []

    def material_ensure_uploaded(self, file_path: Path, sign: str = "") -> tuple[str, str]:
        """确保本地图已进素材库并返回 (fancy_img_url, sign)。

        历史 bug（2026-11-26 修）：原 `upload_local_image` 依赖 queryByMd5 的
        `imgUrl` 做复用判断，但该字段永远不返回 ⇒ 同一张图每次 plan 都重传、
        重复占素材库；且 `material/create` 对已存在 MD5 只回
        `{id, alreadyExists: True}`（无 uploadStatus/imgUrl），上游误判后拿空 URL。
        现在：queryByMd5 命中就用其 id 直接 `material/edit`（id 幂等，返回 fancy URL）；
        未命中才走完整 签名→store_image→create→edit 链路。
        """
        raw_bytes = file_path.read_bytes()
        md5_hex = hashlib.md5(raw_bytes).hexdigest()
        stem = file_path.stem[:80] or ("img_%d" % int(time.time()))

        existing = self.material_query_by_md5([md5_hex], material_type=1)
        if existing:
            mat_id = existing[0].get("id")
            if not mat_id:
                raise RuntimeError("素材已存在但无 id: %s" % existing[0])
            edited = self.material_edit(int(mat_id), "", stem)
            fancy = str(edited.get("imgUrl") or "")
            if fancy:
                return fancy, sign

        sign = self.get_upload_signature()
        up = self.upload_store_image(file_path, sign)
        origin_url = str(up.get("url") or "")
        etag = str(up.get("etag") or md5_hex)
        if not origin_url:
            raise RuntimeError("store_image 未返回 url: %s" % json.dumps(up, ensure_ascii=False)[:250])

        created = self.material_create(etag, stem)
        mat_id = created.get("id")
        if not mat_id:
            raise RuntimeError("material/create 未返回 id: %s" % json.dumps(created, ensure_ascii=False)[:250])
        edited = self.material_edit(int(mat_id), origin_url, stem)
        fancy = str(edited.get("imgUrl") or "")
        if not fancy:
            raise RuntimeError("material/edit 未拿到 imgUrl (status=%s): %s"
                               % (edited.get("uploadStatus"), edited))
        return fancy, sign

    def upload_local_image(self, file_path: Path, sign: str = "") -> tuple[str, str]:
        """本地图片完整上传链路（向后兼容的别名，见 `material_ensure_uploaded`）。"""
        return self.material_ensure_uploaded(file_path, sign)

    def material_create(self, md5_hex: str, name: str, folder_id: int = 0, material_type: int = 1) -> dict:
        r = self.post(
            "/phoenix-mms/material/create",
            {
                "folderId": int(folder_id),
                "createDetailList": [
                    {"materialType": int(material_type), "materialMd5": md5_hex, "materialName": name}
                ],
            },
        )
        lst = (r.get("result") or {}).get("responseDetailList") or []
        return lst[0] if lst else {}

    def material_edit(self, mat_id: int, origin_url: str, name: str, material_type: int = 1) -> dict:
        r = self.post(
            "/phoenix-mms/material/edit",
            {
                "id": int(mat_id),
                "url": origin_url,
                "materialName": name,
                "uploadStatus": 3,
                "materialType": int(material_type),
            },
        )
        return r.get("result") or {}

