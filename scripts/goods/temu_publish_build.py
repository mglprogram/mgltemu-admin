# -*- coding: utf-8 -*-
"""TEMU 上链接本地构造与校验（纯离线逻辑，不碰网络）。

把人可读的 `spec.json` 翻译成 `POST /visage-agent-seller/product/draft/save`
所需的 41 字段完整 payload，并在发请求前做本地硬校验。
"""
from __future__ import annotations

import copy
import re
from pathlib import Path
from typing import Any


def _norm(s: Any) -> str:
    return str(s or "").strip().lower().replace("（", "(").replace("）", ")")


def flatten_cat_path(cat_path_vo: dict) -> dict[str, int]:
    """把 category/path/list 的 categoryPathVO 转成 cat1Id..cat10Id。"""
    out: dict[str, int] = {}
    for i in range(1, 11):
        node = cat_path_vo.get("cat%dNodeVO" % i) or {}
        out["cat%dId" % i] = int(node.get("catId") or 0)
    return out


def categories_dict_to_flat(categories: dict) -> dict[str, int]:
    """把 draft/query 返回的 categories{cat1..cat10} 转成 cat1Id..cat10Id。"""
    out: dict[str, int] = {}
    for i in range(1, 11):
        node = categories.get("cat%d" % i) or {}
        out["cat%dId" % i] = int(node.get("catId") or 0)
    return out


def draft_query_to_save_defaults(dq: dict) -> dict[str, Any]:
    """从 draft/query 的 result 提取可复用的底层默认配置（站点/物流/产地/模特/尺码模板）。"""
    out: dict[str, Any] = {}
    if dq.get("categories"):
        out["cat_ids_flat"] = categories_dict_to_flat(dq["categories"])
    if dq.get("sizeTemplateIds"):
        out["sizeTemplateIds"] = list(dq["sizeTemplateIds"])
    if dq.get("showSizeTemplateIds"):
        out["showSizeTemplateIds"] = list(dq["showSizeTemplateIds"])
    elif out.get("sizeTemplateIds"):
        out["showSizeTemplateIds"] = list(out["sizeTemplateIds"])
    if dq.get("goodsModelList"):
        out["goodsModelReqs"] = copy.deepcopy(dq["goodsModelList"])
    if dq.get("productWhExtAttr"):
        out["productWhExtAttrReq"] = copy.deepcopy(dq["productWhExtAttr"])
    if dq.get("productSaleExtAttr"):
        out["productSaleExtAttrReq"] = copy.deepcopy(dq["productSaleExtAttr"])
    if dq.get("productNonAuditExtAttr"):
        na = copy.deepcopy(dq["productNonAuditExtAttr"])
        # draft/query 里叫 california65WarningInfo，save 里叫 california65WarningInfoReq
        if "california65WarningInfo" in na and "california65WarningInfoReq" not in na:
            na["california65WarningInfoReq"] = na.pop("california65WarningInfo")
        if "cosmeticInfo" in na and "cosmeticInfoReq" not in na:
            na["cosmeticInfoReq"] = na.pop("cosmeticInfo")
        out["productNonAuditExtAttrReq"] = na
    return out


def check_local_image(path_str: str) -> list[str]:
    """用 Pillow 检查本地图片是否满足 3:4、>=1340x1785、<=2MB（仅告警，不拦截）。"""
    warns: list[str] = []
    p = Path(path_str)
    if not p.is_file():
        return ["本地图片不存在: %s" % path_str]
    sz = p.stat().st_size
    if sz > 2 * 1024 * 1024:
        warns.append("图片超 2MB (%.2f MB): %s" % (sz / 1024 / 1024, p.name))
    try:
        from PIL import Image  # noqa: PLC0415

        with Image.open(p) as im:
            w, h = im.size
        if w < 1340 or h < 1785:
            warns.append("图片小于 1340x1785 (%dx%d): %s" % (w, h, p.name))
        ratio = w / float(h or 1)
        if abs(ratio - 0.75) > 0.03:
            warns.append("图片比例非 3:4 (%dx%d, %.2f): %s" % (w, h, ratio, p.name))
    except Exception:
        pass
    return warns


def _match_prop_def(props: list[dict], name: str, sale_only: bool = False) -> dict | None:
    want = _norm(name)
    cands = [p for p in props if (not sale_only or p.get("isSale"))]
    for p in cands:
        if _norm(p.get("name")) == want:
            return p
    for p in cands:
        if want in _norm(p.get("name")) or _norm(p.get("name")).startswith(want):
            return p
    return None


def _match_prop_val(prop_def: dict, val_name: Any) -> dict | None:
    want = _norm(val_name)
    vals = prop_def.get("values") or []
    for v in vals:
        if _norm(v.get("value")) == want:
            return v
    for v in vals:
        if want in _norm(v.get("value")):
            return v
    return None


def resolve_site_ids(spec_sites: list[Any], schema: dict) -> tuple[list[int], list[str]]:
    errors: list[str] = []
    if not spec_sites:
        return [100], errors
    site_map = {_norm(s.get("siteName")): int(s["siteId"]) for s in (schema.get("sites") or [])}
    out: list[int] = []
    for item in spec_sites:
        if isinstance(item, int) or str(item).isdigit():
            out.append(int(item))
        else:
            sid = site_map.get(_norm(item))
            if not sid:
                # 部分匹配
                for k, v in site_map.items():
                    if _norm(item) in k:
                        sid = v
                        break
            if sid:
                out.append(sid)
            else:
                errors.append("未知经营站点: %r" % item)
    return out, errors


def resolve_warehouse_routes(
    spec_whs: list[Any],
    site_ids: list[int],
    schema: dict,
) -> tuple[list[dict], list[str]]:
    errors: list[str] = []
    wh_map = {
        _norm(w.get("warehouseName")): str(w["warehouseId"])
        for w in (schema.get("warehouses") or [])
    }
    valid_ids = {str(w["warehouseId"]) for w in (schema.get("warehouses") or [])}
    routes: list[dict] = []
    for item in spec_whs:
        s = str(item).strip()
        if s.startswith("WH-"):
            if valid_ids and s not in valid_ids:
                errors.append("店铺可选仓库里没有: %s" % s)
            routes.append({"warehouseId": s, "siteIdList": list(site_ids)})
            continue
        wid = wh_map.get(_norm(s))
        if not wid:
            for k, v in wh_map.items():
                if _norm(s) in k:
                    wid = v
                    break
        if wid:
            routes.append({"warehouseId": wid, "siteIdList": list(site_ids)})
        else:
            errors.append("未知发货仓 %r（可选：%s）" % (s, ", ".join(wh_map.keys()) or "空"))
    return routes, errors


def build_properties(
    spec_attrs: dict[str, Any],
    tpl_props: list[dict],
) -> tuple[list[dict], list[str], list[str]]:
    """把人类写的 attributes 字典转成 productPropertyReqs。"""
    reqs: list[dict] = []
    errors: list[str] = []
    warns: list[str] = []
    filled_pids: set[int] = set()

    for k, raw_val in (spec_attrs or {}).items():
        pdef = _match_prop_def(tpl_props, k, sale_only=False)
        if not pdef:
            errors.append("类目模板里找不到属性: %r" % k)
            continue
        if pdef.get("isSale"):
            continue
        tpid = int(pdef["templatePid"])
        pid = int(pdef["pid"])
        ref_pid = int(pdef["refPid"])
        pname = str(pdef["name"])
        filled_pids.add(tpid)

        # 1) 成分类字典：{聚酯纤维(涤纶）: 100}
        if isinstance(raw_val, dict) and "value" not in raw_val:
            total_pct = 0.0
            for sub_name, pct in raw_val.items():
                vdef = _match_prop_val(pdef, sub_name)
                if not vdef:
                    errors.append("属性「%s」找不到可选值: %r" % (pname, sub_name))
                    continue
                pct_f = float(pct)
                total_pct += pct_f
                unit = (pdef.get("valueUnit") or ["%"])[0] or "%"
                reqs.append(
                    {
                        "templatePid": tpid,
                        "pid": pid,
                        "refPid": ref_pid,
                        "propName": pname,
                        "vid": int(vdef["vid"]),
                        "propValue": str(vdef["value"]),
                        "valueUnit": unit,
                        "valueExtendInfo": str(vdef.get("extendInfo") or ""),
                        "numberInputValue": "%.2f" % pct_f,
                    }
                )
            if abs(total_pct - 100.0) > 0.01:
                errors.append("属性「%s」成分比例之和必须=100%%（当前 %.2f%%）" % (pname, total_pct))
            continue

        # 2) 多选列表：["春", "夏", "秋", "冬"] → 先试逗号拼接值，再逐项匹配
        if isinstance(raw_val, list):
            joined = ",".join(str(x).strip() for x in raw_val)
            vdef = _match_prop_val(pdef, joined)
            if vdef:
                reqs.append(
                    {
                        "templatePid": tpid,
                        "pid": pid,
                        "refPid": ref_pid,
                        "propName": pname,
                        "vid": int(vdef["vid"]),
                        "propValue": str(vdef["value"]),
                        "valueUnit": (pdef.get("valueUnit") or [""])[0] or "",
                        "valueExtendInfo": str(vdef.get("extendInfo") or ""),
                        "numberInputValue": "",
                    }
                )
            else:
                for sub in raw_val:
                    v2 = _match_prop_val(pdef, sub)
                    if not v2:
                        errors.append("属性「%s」找不到可选值: %r" % (pname, sub))
                        continue
                    reqs.append(
                        {
                            "templatePid": tpid,
                            "pid": pid,
                            "refPid": ref_pid,
                            "propName": pname,
                            "vid": int(v2["vid"]),
                            "propValue": str(v2["value"]),
                            "valueUnit": (pdef.get("valueUnit") or [""])[0] or "",
                            "valueExtendInfo": str(v2.get("extendInfo") or ""),
                            "numberInputValue": "",
                        }
                    )
            continue

        # 3) 显式字典：{value, number, unit}
        if isinstance(raw_val, dict):
            val_str = str(raw_val.get("value") or "")
            num_str = str(raw_val.get("number") or "")
            unit_str = str(raw_val.get("unit") or ((pdef.get("valueUnit") or [""])[0] or ""))
            vdef = _match_prop_val(pdef, val_str) if (pdef.get("values") and val_str) else None
            if (pdef.get("values") and val_str) and not vdef:
                errors.append("属性「%s」找不到可选值: %r" % (pname, val_str))
                continue
            reqs.append(
                {
                    "templatePid": tpid,
                    "pid": pid,
                    "refPid": ref_pid,
                    "propName": pname,
                    "vid": int(vdef["vid"]) if vdef else 0,
                    "propValue": str(vdef["value"]) if vdef else val_str,
                    "valueUnit": unit_str,
                    "valueExtendInfo": str(vdef.get("extendInfo") or "") if vdef else "",
                    "numberInputValue": num_str,
                }
            )
            continue

        # 4) 标量字符串 / 数字
        sval = str(raw_val).strip()
        vals = pdef.get("values") or []
        if vals:
            vdef = _match_prop_val(pdef, sval)
            if not vdef:
                errors.append("属性「%s」找不到可选值: %r" % (pname, sval))
                continue
            reqs.append(
                {
                    "templatePid": tpid,
                    "pid": pid,
                    "refPid": ref_pid,
                    "propName": pname,
                    "vid": int(vdef["vid"]),
                    "propValue": str(vdef["value"]),
                    "valueUnit": (pdef.get("valueUnit") or [""])[0] or "",
                    "valueExtendInfo": str(vdef.get("extendInfo") or ""),
                    "numberInputValue": "",
                }
            )
        else:
            # 无预设枚举的数值/文本输入属性（如 面料克重1（g/m²)）
            reqs.append(
                {
                    "templatePid": tpid,
                    "pid": pid,
                    "refPid": ref_pid,
                    "propName": pname,
                    "vid": 0,
                    "propValue": sval,
                    "valueUnit": (pdef.get("valueUnit") or [""])[0] or "",
                    "valueExtendInfo": "",
                    "numberInputValue": "",
                }
            )

    # 校验必填项（遵循 showCondition 与 parentTemplatePid/templatePropertyValueParentList 条件显隐）
    filled_by_ref: dict[int, set[int]] = {}
    filled_by_tpid: dict[int, set[int]] = {}
    for rq in reqs:
        filled_by_ref.setdefault(int(rq["refPid"]), set()).add(int(rq["vid"]))
        filled_by_tpid.setdefault(int(rq["templatePid"]), set()).add(int(rq["vid"]))

    def _is_prop_visible(pdef: dict) -> bool:
        conds = pdef.get("showCondition") or []
        for c in conds:
            pr = int(c.get("parentRefPid") or 0)
            pvids = {int(x) for x in (c.get("parentVids") or [])}
            if pr and pvids and not (filled_by_ref.get(pr, set()) & pvids):
                return False
        ptpid = int(pdef.get("parentTemplatePid") or 0)
        tpl_parents = pdef.get("templatePropertyValueParentList") or []
        if ptpid and tpl_parents:
            chosen = filled_by_tpid.get(ptpid, set())
            allowed_parents = {int(x) for row in tpl_parents for x in (row.get("parentVidList") or [])}
            if allowed_parents and not (chosen & allowed_parents):
                return False
        return True

    for pdef in tpl_props:
        if pdef.get("isSale"):
            continue
        if pdef.get("required") and int(pdef["templatePid"]) not in filled_pids:
            if _is_prop_visible(pdef):
                errors.append("缺少类目必填属性: %s (pid=%s)" % (pdef.get("name"), pdef.get("pid")))

    return reqs, errors, warns


def _resolve_sale_spec(
    tpl_props: list[dict],
    spec_title: str,
    val_name: str,
) -> tuple[dict | None, str | None]:
    pdef = _match_prop_def(tpl_props, spec_title, sale_only=True)
    if not pdef:
        return None, "类目模板里找不到销售规格: %r" % spec_title
    vdef = _match_prop_val(pdef, val_name)
    if not vdef:
        return None, "规格「%s」里找不到值: %r" % (pdef.get("name"), val_name)
    grp = vdef.get("group") or {}
    return (
        {
            "parentSpecId": int(pdef["parentSpecId"]),
            "parentSpecName": str(pdef["name"]),
            "specId": int(vdef["specId"]),
            "specName": str(vdef["value"]),
            "groupId": int(grp.get("id") or 0),
            "vid": int(vdef["vid"]),
            "refPid": int(pdef["refPid"]),
            "pid": int(pdef["pid"]),
            "templatePid": int(pdef["templatePid"]),
            "propName": str(pdef["name"]),
            "propValue": str(vdef["value"]),
            "valueUnit": (pdef.get("valueUnit") or [""])[0] or "",
            "valueGroupId": int(grp.get("id") or 0),
            "valueGroupName": str(grp.get("name") or ""),
            "valueExtendInfo": str(vdef.get("extendInfo") or ""),
        },
        None,
    )


def _sku_spec_req(sp: dict) -> dict:
    return {
        "parentSpecId": sp["parentSpecId"],
        "parentSpecName": sp["parentSpecName"],
        "specId": sp["specId"],
        "specName": sp["specName"],
        "groupId": sp["groupId"],
        "vid": sp["vid"],
    }


def _num_str(v: Any) -> str:
    f = float(v)
    return str(int(f)) if f.is_integer() else str(f)


def build_draft_save_payload(
    spec: dict[str, Any],
    schema: dict[str, Any],
    pic_resolver=None,
    base_defaults: dict[str, Any] | None = None,
) -> tuple[dict[str, Any], list[str], list[str]]:
    """将 spec.json 组装成完整的 draft/save payload，返回 (payload, errors, warns)。"""
    errors: list[str] = []
    warns: list[str] = []
    base = dict(base_defaults or {})

    cat_id = int(spec.get("cat_id") or 0)
    if not cat_id:
        errors.append("spec 缺少 cat_id（末级类目 ID）")
        return {}, errors, warns

    tpl = (schema.get("templates") or {}).get(str(cat_id)) or {}
    tpl_props = tpl.get("properties") or []
    if not tpl_props:
        errors.append("schema 里缺少类目 %s 的属性模板（先跑 --stage schema --cat %s）" % (cat_id, cat_id))
        return {}, errors, warns

    # 1) 类目路径 cat1Id..cat10Id
    cat_paths = schema.get("cat_paths") or {}
    if str(cat_id) in cat_paths:
        cat_flat = flatten_cat_path(cat_paths[str(cat_id)])
    elif base.get("cat_ids_flat"):
        cat_flat = dict(base["cat_ids_flat"])
    else:
        errors.append("schema 里缺少类目 %s 的完整路径（先跑 --stage schema --cat %s）" % (cat_id, cat_id))
        cat_flat = {"cat%dId" % i: 0 for i in range(1, 11)}

    # 2) 站点与仓库路由
    site_ids, e_sites = resolve_site_ids(spec.get("sites") or ["美国站"], schema)
    errors.extend(e_sites)

    wh_spec = spec.get("warehouses") or []
    if wh_spec:
        routes, e_wh = resolve_warehouse_routes(wh_spec, site_ids, schema)
        errors.extend(e_wh)
    else:
        errors.append("spec 缺少 warehouses（至少指定 1 个发货仓，如 [\"恒信加州3\"]）")
        routes = []

    warehouse_ids = [r["warehouseId"] for r in routes]

    # 3) 标题 / 多语言
    title = str(spec.get("title") or "").strip()
    title_en = str(spec.get("title_en") or title).strip()
    if not title:
        errors.append("spec 缺少 title（商品中文名）")
    if not title_en:
        errors.append("spec 缺少 title_en（英文名称）")

    # 4) 商品属性
    prop_reqs, e_prop, w_prop = build_properties(spec.get("attributes") or {}, tpl_props)
    errors.extend(e_prop)
    warns.extend(w_prop)

    # 5) 图片解析（支持 URL 直接透传或本地文件经 pic_resolver 上传）
    def _resolve_img(img_ref: str) -> str:
        s = str(img_ref or "").strip()
        if not s:
            return ""
        if s.startswith("http://") or s.startswith("https://"):
            return s
        warns.extend(check_local_image(s))
        if pic_resolver is None:
            warns.append("未启用图片上传（离线 plan）：保留本地路径 %s" % s)
            return s
        return str(pic_resolver(s))

    # 6) SKC / SKU（支持单 SKC 简写或 skcs 数组）
    skc_specs = spec.get("skcs")
    if not skc_specs:
        skc_specs = [
            {
                "ext_code": spec.get("ext_code"),
                "color": spec.get("color"),
                "color_image": spec.get("color_image"),
                "images": spec.get("images") or [],
                "sizes": spec.get("sizes") or {},
            }
        ]

    spec_prop_seen: dict[tuple[int, int], dict] = {}
    skc_reqs: list[dict] = []
    all_carousel: list[str] = []

    for idx, skc_in in enumerate(skc_specs):
        skc_ext = str(skc_in.get("ext_code") or "").strip()
        color_name = str(skc_in.get("color") or "").strip()
        if not skc_ext:
            errors.append("SKC[%d] 缺少 ext_code" % idx)
        if not color_name:
            errors.append("SKC[%d] 缺少 color" % idx)

        c_sp, c_err = _resolve_sale_spec(tpl_props, "颜色", color_name) if color_name else (None, None)
        if c_err:
            errors.append(c_err)
        elif c_sp:
            spec_prop_seen[(c_sp["parentSpecId"], c_sp["specId"])] = c_sp

        skc_imgs = [_resolve_img(x) for x in (skc_in.get("images") or spec.get("images") or []) if str(x).strip()]
        if not skc_imgs:
            warns.append("SKC %s 未配置轮播图 images（草稿允许为空，提交前需补齐）" % (skc_ext or idx))
        for u in skc_imgs:
            if u not in all_carousel:
                all_carousel.append(u)

        color_img = _resolve_img(str(skc_in.get("color_image") or (skc_imgs[0] if skc_imgs else "")))

        sizes_dict = skc_in.get("sizes") or {}
        if not sizes_dict:
            errors.append("SKC %s 缺少 sizes" % (skc_ext or idx))

        sku_reqs: list[dict] = []
        default_vol = spec.get("default_volume_cm") or [30, 25, 4]
        for sz_name, sz_cfg in sizes_dict.items():
            s_sp, s_err = _resolve_sale_spec(tpl_props, "尺码", str(sz_name))
            if s_err:
                errors.append(s_err)
            elif s_sp:
                spec_prop_seen[(s_sp["parentSpecId"], s_sp["specId"])] = s_sp

            sku_ext = str(sz_cfg.get("ext_code") or ("%s-%s" % (skc_ext, sz_name))).strip()
            if "price_cents" in sz_cfg and sz_cfg["price_cents"] is not None:
                cents = int(sz_cfg["price_cents"])
            elif "price_usd" in sz_cfg and sz_cfg["price_usd"] is not None:
                cents = int(round(float(sz_cfg["price_usd"]) * 100))
            else:
                cents = 0
            if cents <= 0:
                errors.append("SKU %s 缺少有效的 price_usd / price_cents" % sku_ext)

            weight_g = sz_cfg.get("weight_g")
            if weight_g is None or float(weight_g) <= 0:
                errors.append("SKU %s 缺少有效的 weight_g" % sku_ext)
                weight_g = 0

            vol = sz_cfg.get("volume_cm") or [
                sz_cfg.get("length_cm", default_vol[0]),
                sz_cfg.get("width_cm", default_vol[1]),
                sz_cfg.get("height_cm", default_vol[2]),
            ]
            l_cm, w_cm, h_cm = float(vol[0]), float(vol[1]), float(vol[2])
            if l_cm <= 0 or w_cm <= 0 or h_cm <= 0:
                errors.append("SKU %s 包装体积长宽高必须 > 0" % sku_ext)

            stock_qty = int(sz_cfg.get("stock") or 0)
            wh_stocks = [{"warehouseId": wid, "targetStockAvailable": stock_qty} for wid in warehouse_ids]

            sku_spec_list = []
            if c_sp:
                sku_spec_list.append(_sku_spec_req(c_sp))
            if s_sp:
                sku_spec_list.append(_sku_spec_req(s_sp))

            thumb = _resolve_img(str(sz_cfg.get("thumb_url") or (skc_imgs[0] if skc_imgs else "")))
            sku_reqs.append(
                {
                    "thumbUrl": thumb,
                    "productSkuThumbUrlI18nReqs": [],
                    "extCode": sku_ext,
                    "siteSupplierPrices": [{"siteId": sid, "supplierPrice": cents} for sid in site_ids],
                    "currencyType": str(spec.get("currency") or "USD"),
                    "productSkuSpecReqs": sku_spec_list,
                    "productSkuId": 0,
                    "productSkuUsSuggestedPriceReq": {},
                    "productSkuWhExtAttrReq": {
                        "productSkuVolumeReq": {
                            "len": int(round(l_cm * 10)),
                            "width": int(round(w_cm * 10)),
                            "height": int(round(h_cm * 10)),
                            "inputLen": _num_str(l_cm),
                            "inputHeight": _num_str(h_cm),
                            "inputWidth": _num_str(w_cm),
                            "inputUnit": "cm",
                        },
                        "productSkuWeightReq": {
                            "value": int(round(float(weight_g) * 1000)),
                            "inputValue": _num_str(weight_g),
                            "inputUnit": "g",
                        },
                        "productSkuBarCodeReqs": [],
                        "productSkuSensitiveAttrReq": {"isSensitive": 0, "sensitiveList": []},
                        "productSkuSensitiveLimitReq": {},
                    },
                    "productSkuStockQuantityReq": {"warehouseStockQuantityReqs": wh_stocks},
                    "productSkuAccessoriesReq": {"productSkuAccessories": []},
                    "productSkuNonAuditExtAttrReq": {},
                }
            )

        skc_reqs.append(
            {
                "previewImgUrls": skc_imgs,
                "productSkcCarouselImageI18nReqs": [],
                "extCode": skc_ext,
                "mainProductSkuSpecReqs": [_sku_spec_req(c_sp)] if c_sp else [],
                "productSkuReqs": sku_reqs,
                "productSkcId": 0,
                "colorImageUrl": color_img or None,
                "isBasePlate": 0,
            }
        )

    # 7) 尺码模板 ID
    size_tpl_ids: list[int] = []
    if spec.get("size_template_id"):
        size_tpl_ids = [int(spec["size_template_id"])]
    elif spec.get("size_template_name"):
        want_st = _norm(spec["size_template_name"])
        for st in (schema.get("size_templates") or {}).get(str(cat_id)) or []:
            if _norm(st.get("name")) == want_st:
                size_tpl_ids = [int(st["businessId"])]
                break
        if not size_tpl_ids:
            errors.append("类目 %s 下找不到尺码表模板 %r" % (cat_id, spec["size_template_name"]))
    elif base.get("sizeTemplateIds"):
        size_tpl_ids = list(base["sizeTemplateIds"])
    else:
        # 若类目有默认尺码模板则提示或自动取第一条需人工注意
        avail = (schema.get("size_templates") or {}).get(str(cat_id)) or []
        if avail:
            warns.append("未指定 size_template_id，留空（可选：%s）" % ", ".join(
                "%s(%s)" % (x.get("name"), x.get("businessId")) for x in avail[:5]
            ))

    # 8) 运费 / 产地 / 扩展属性
    freight_id = str(spec.get("freight_template_id") or "").strip()
    ship_sec = int(spec.get("shipment_limit_hours") or 48) * 3600
    ship_req = {"freightTemplateId": freight_id, "shipmentLimitSecond": ship_sec} if freight_id else {}

    origin_cfg = spec.get("origin") or (base.get("productWhExtAttrReq") or {}).get("productOrigin") or {
        "countryShortName": "CN",
        "region2Id": 43000000000006,
    }
    wh_ext_req = {
        "outerGoodsUrl": str(spec.get("outer_goods_url") or ""),
        "productOrigin": origin_cfg,
    }
    mfg_country = str(origin_cfg.get("countryShortName") or "CN")
    sale_ext_req = base.get("productSaleExtAttrReq") or {
        "bodyShape": None,
        "manufacturingLocation": {"region1ShortNames": [mfg_country]},
    }
    non_audit_req = base.get("productNonAuditExtAttrReq") or {
        "california65WarningInfoReq": {"california65WarningType": None, "california65ChemicalNames": None},
        "cosmeticInfoReq": {},
    }

    mat_img = _resolve_img(str(spec.get("material_image") or ""))
    top_carousel = [_resolve_img(x) for x in (spec.get("carousel_images") or []) if str(x).strip()] or all_carousel

    payload: dict[str, Any] = {
        **cat_flat,
        "materialMultiLanguages": list(spec.get("material_languages") or []),
        "productName": title,
        "productI18nReqs": [{"productName": title_en, "language": "en"}],
        "productPropertyReqs": prop_reqs,
        "productSkcReqs": skc_reqs,
        "productSpecPropertyReqs": list(spec_prop_seen.values()),
        "carouselImageUrls": top_carousel,
        "carouselImageI18nReqs": [],
        "materialImgUrl": mat_img,
        "goodsLayerDecorationReqs": [],
        "goodsLayerDecorationCustomizeI18nReqs": [],
        "sizeTemplateIds": size_tpl_ids,
        "showSizeTemplateIds": size_tpl_ids,
        "goodsModelReqs": list(spec.get("models") or base.get("goodsModelReqs") or []),
        "productWhExtAttrReq": wh_ext_req,
        "productCarouseVideoReqList": [],
        "goodsAdvantageLabelTypes": [],
        "productDetailVideoReqList": [],
        "productOuterPackageImageReqs": [],
        "productOuterPackageReq": {},
        "sensitiveTransNormalFileReqs": [],
        "productGuideFileNewReqList": [],
        "productGuideFileI18nReqs": [],
        "productSaleExtAttrReq": sale_ext_req,
        "productNonAuditExtAttrReq": non_audit_req,
        "productShipmentReq": ship_req,
        "productWarehouseRouteReq": {"targetRouteList": routes},
        "productSemiManagedReq": {
            "bindSiteIds": site_ids,
            "semiLanguageStrategy": int(spec.get("semi_language_strategy") or 2),
        },
        "personalizationSwitch": int(spec.get("personalization_switch") or 0),
        "productOriginCertFileReqs": [],
        "productDraftId": int(spec.get("product_draft_id") or 0),
    }
    return payload, errors, warns


# ---------------- 尺码默认口径（2026-11-26 用户定） ----------------
#
# 上衣：只填 衣长 / 胸围 / 袖长
# 裤子：只填 裤长 / 臀围 / 腰围
#
# 这两个口径是**用户明确规定的默认**，其它量体项（肩宽/袖肥/裤口/前浪…）一律不填。
# 注意"胸围全围"就是"胸围"：用户口径里的"胸围 100"要原样填 100，**不乘 2**
# （2026-11-26 用户明确："这次的胸阔不乘以2"）。
SIZE_ELEM_ID = {
    "胸围全围": 10002,
    "衣长": 10003,
    "袖长": 10004,
    "肩宽": 10001,
    "裤长": 10005,
    "腰围": 10024,
    "臀围": 10026,
}

SIZE_PROFILES: dict[str, dict[str, Any]] = {
    "top": {
        "label": "上衣",
        "elements": ["衣长", "胸围全围", "袖长"],
        # 字段名 → 量体项（用户口径名 → TEMU 量体项名）
        "aliases": {"衣长": "衣长", "胸围": "胸围全围", "胸围全围": "胸围全围", "袖长": "袖长"},
    },
    "pants": {
        "label": "裤子",
        "elements": ["裤长", "臀围", "腰围"],
        "aliases": {"裤长": "裤长", "臀围": "臀围", "腰围": "腰围"},
    },
}

# 够通用、可以安全复用的模板命名前缀；其它名字视作一次性（与尺码数值绑定）
REUSABLE_TEMPLATE_RE = re.compile(r"^(?:default[-_])?(top|pants|上衣|裤子|女上装|男上装|女下装|男下装)$",
                                  re.IGNORECASE)


def guess_profile(cat_name: str = "", spec: dict | None = None) -> str:
    """按类目名/商品名猜是上衣还是裤子。"""
    spec = spec or {}
    txt = " ".join(str(x or "") for x in (
        cat_name, spec.get("title_en"), spec.get("title"),
        " ".join((spec.get("attributes") or {}).get(k) or "" for k in ("品类",)),
    )).lower()
    for kw in ("裤", "pants", "shorts", "trousers", "jogger", "legging"):
        if kw in txt:
            return "pants"
    return "top"


def template_size_rows(vo: dict) -> dict[str, dict[str, str]]:
    """把尺码表压成 {尺码: {量体项名: 值}}，便于比对是否需要新建模板。"""
    content = vo.get("contentVO") or {}
    meta = content.get("meta") or {}
    id2name = {str(e.get("id")): str(e.get("name")) for e in (meta.get("elements") or [])}
    out: dict[str, dict[str, str]] = {}
    for rec in content.get("records") or []:
        vals = rec.get("values") or {}
        size = str(vals.get("1") or "").strip().upper()
        if not size:
            continue
        out[size] = {id2name.get(str(k), str(k)): str(v).strip()
                     for k, v in vals.items() if str(k) != "1"}
    return out


def normalize_size_values(profile: str, raw: dict[str, Any]) -> tuple[dict[str, str], list[str]]:
    """把用户写的尺码数值（胸围/衣长/袖长 或 裤长/臀围/腰围）规整成量体项名 → 值。

    返回 (规整后的值, 错误列表)。多余字段会被报错而不是静默忽略，
    因为填错量体项比不填更危险。
    """
    prof = SIZE_PROFILES.get(profile) or SIZE_PROFILES["top"]
    aliases = prof["aliases"]
    out: dict[str, str] = {}
    errs: list[str] = []
    for k, v in (raw or {}).items():
        key = str(k).strip()
        norm = aliases.get(key) or aliases.get(key.replace("（cm）", "").replace("(cm)", ""))
        if not norm:
            if key.upper() in ("S", "M", "L", "XL", "XXL", "XS", "XXXL"):
                continue
            errs.append("尺码字段 %r 不在%s口径（%s）里"
                        % (key, prof["label"], "/".join(prof["elements"])))
            continue
        out[norm] = str(v).strip()
    return out, errs


def size_spec_matches(vo: dict, profile: str, want: dict[str, dict[str, str]]) -> dict[str, Any]:
    """判断已有模板是否**完全够用**：量体项集合一致 + 每个尺码每个量体项取值一致。

    够用就直接复用该 businessId；不够用则返回哪些地方不一致（供建新模板）。
    """
    prof = SIZE_PROFILES.get(profile) or SIZE_PROFILES["top"]
    content = vo.get("contentVO") or {}
    els = [str(e.get("name")) for e in ((content.get("meta") or {}).get("elements") or [])]
    rows = template_size_rows(vo)
    diffs: list[str] = []

    # 模板比本次用到的码多（S~XXXL 对 S~XXL）不算冲突：draft/save 只写用到的码，
    # 为此重建模板只会堆垃圾，而尺寸表**没有删除接口**。
    want_sizes = {str(k).upper() for k in want}

    if sorted(els) != sorted(prof["elements"]):
        diffs.append("量体项不一致：模板=%s 期望=%s" % (els, prof["elements"]))

    for size, vals in want.items():
        got = rows.get(str(size).upper())
        if got is None:
            diffs.append("模板缺尺码 %s" % size)
            continue
        for name, v in vals.items():
            if str(got.get(name, "")).strip() != str(v).strip():
                diffs.append("%s %s：模板=%s 期望=%s"
                             % (size, name, got.get(name, "（空）"), v))
    return {
        "businessId": vo.get("businessId"),
        "name": vo.get("name"),
        "elements": els,
        "sizes": sorted(rows),
        "extra_sizes": sorted(s for s in rows if s not in want_sizes),
        "diffs": diffs,
        "ok": not diffs,
    }


def build_size_template_vo(
    profile: str,
    want: dict[str, dict[str, str]],
    base_vo: dict | None = None,
    class_id: int | None = None,
    name: str = "",
) -> dict:
    """按「上衣/裤子」默认口径造一个 sizeSpecDataVO（供 build_sizespec_payload 使用）。

    以 base_vo 的 classId / ext / groups 结构为骨架（不同类目 classId 不同，
    上衣 3=男上装 / 5=女上装），只替换 elements 与 records。
    """
    prof = SIZE_PROFILES.get(profile) or SIZE_PROFILES["top"]
    base_vo = base_vo or {}
    base_content = base_vo.get("contentVO") or {}
    base_meta = base_content.get("meta") or {}
    groups = [{"id": g.get("id"), "name": g.get("name"),
               "supplierUnnecessary": g.get("supplierUnnecessary")}
              for g in (base_meta.get("groups") or [{"id": 1, "name": "尺码"}])]

    elements = [{"id": SIZE_ELEM_ID[n], "name": n, "necessary": None,
                 "autoSelected": None, "unit": "cm"} for n in prof["elements"]]

    records = []
    for size, vals in want.items():
        values = {"1": str(size)}
        value_list = [{"id": 1, "value": str(size)}]
        for n in prof["elements"]:
            eid = SIZE_ELEM_ID[n]
            v = str(vals.get(n, "")).strip()
            values[str(eid)] = v
            value_list.append({"id": eid, "value": v})
        records.append({"values": values, "valueList": value_list})

    return {
        "businessId": None,
        "name": name or ("%s-%s" % (profile, "default")),
        "classId": class_id if class_id is not None else base_vo.get("classId"),
        "className": base_vo.get("className"),
        "contentVO": {
            "generalSizeType": base_content.get("generalSizeType", 1),
            "localSizeSource": base_content.get("localSizeSource", 1),
            "meta": {"groups": groups, "elements": elements, "sizeTypeElementsList": None},
            "records": records,
            "bodyMeta": None,
            "bodyRecords": None,
        },
        "ext": copy.deepcopy(base_vo.get("ext") or {}),
    }


def size_spec_is_usable(vo: dict, cat_id: int, sess=None) -> bool:
    """模板能否被商品真正使用：必须是**商品私有**（`reusable=false`）。

    公共模板（`reusable=true`）草稿里挂了也不渲染，后台会一直显示「+ 添加尺码表」。
    判定方式：用 `reusable=false` 再查一次，查得到内容就是私有模板。
    """
    if sess is None or not vo.get("businessId"):
        return False
    try:
        priv = sess.sizespec_detail(int(vo["businessId"]), reusable=False)
    except Exception:
        return False
    return bool((priv or {}).get("contentVO"))


def pick_size_template(
    cat_id: int,
    schema: dict,
    profile: str,
    want: dict[str, dict[str, str]],
    sess=None,
    name_hint: str = "",
    draft_id: int = 0,
) -> dict[str, Any]:
    """选尺码表模板：先查该店该类目下已有哪些模板，逐条比对，**没有一个完全匹配就建新的**。

    返回 {"vo": 选中的模板VO, "reused": bool, "businessId": int|None, "checked": [...], "reason": str}
      · 命中复用：reused=True，vo 就是线上那份
      · 未命中且给了 sess：reused=False，vo 是本地新造的，调用方负责 createSizeSpec
    ⚠ 不新建也能跑（sess=None 时只返回本地造的 vo，由调用方决定）。
    """
    prof = SIZE_PROFILES.get(profile) or SIZE_PROFILES["top"]
    checked: list[dict] = []
    cached = (schema.get("size_templates") or {}).get(str(cat_id))
    if cached is None and sess is not None:
        cached = sess.sizespec_list(cat_id=cat_id, offset=0, limit=50)
    candidates = list(cached or [])

    # 按名称提示（通常就是货号数字，如 202301）优先命中：同名模板先比，命中就不用建新的
    want_name = str(name_hint or "").strip()
    if want_name:
        named = [c for c in candidates if str(c.get("name") or "").strip() == want_name]
        candidates = named + [c for c in candidates if c not in named]
        for item in candidates:
            if str(item.get("name") or "").strip() != want_name:
                break
            if sess is None:
                break
            try:
                vo = sess.sizespec_detail(int(item["businessId"]), reusable=True)
            except Exception:
                continue
            if (vo or {}).get("contentVO") and size_spec_matches(vo, profile, want)["ok"] \
                    and size_spec_is_usable(vo, cat_id, sess):
                return {"vo": vo, "reused": True, "businessId": int(item["businessId"]),
                        "checked": [{"businessId": item["businessId"], "name": vo.get("name"),
                                     "ok": True, "diffs": [], "diffsCount": 0}],
                        "reason": "按名称复用模板 %s(%s)" % (vo.get("name"), item["businessId"]),
                        "classId": vo.get("classId")}

    # 商品私有模板（reusable=false）在 rollPageQueryData 里查不到，只能通过 draft/query
    # 的 sizeTemplateIds 拿到。先看商品自己挂的能不能直接用（用户口径：
    # 「在第一个草稿继续编辑修改」——同一个商品反复改，尺码表不该每次重建）。
    if sess is not None and draft_id:
        try:
            dq = sess.draft_query(int(draft_id))
        except Exception:
            dq = {}
        for did_tpl in dq.get("sizeTemplateIds") or []:
            try:
                dvo = sess.sizespec_detail(int(did_tpl), reusable=False)
            except Exception:
                continue
            if not (dvo or {}).get("contentVO"):
                continue
            res = size_spec_matches(dvo, profile, want)
            checked.append({"businessId": did_tpl, "name": dvo.get("name"),
                            "classId": dvo.get("classId"), "ok": res["ok"], "private": True,
                            "diffs": res["diffs"][:4], "diffsCount": len(res["diffs"])})
            if res["ok"]:
                return {"vo": dvo, "reused": True, "businessId": int(did_tpl),
                        "checked": checked,
                        "reason": "复用草稿已挂的尺码表 %s(%s)" % (dvo.get("name"), did_tpl),
                        "classId": dvo.get("classId")}

    best_vo = None
    for item in candidates:
        bid = item.get("businessId") or item.get("id")
        if not bid:
            continue
        try:
            vo = sess.sizespec_detail(int(bid), reusable=True) if sess is not None else item
        except Exception as e:  # 读不到就跳过，不影响后续比对
            checked.append({"businessId": bid, "name": item.get("name"), "error": str(e)[:120]})
            continue
        if not (vo or {}).get("contentVO"):
            continue
        best_vo = best_vo or vo
        res = size_spec_matches(vo, profile, want)
        private = size_spec_is_usable(vo, cat_id, sess)
        checked.append({"businessId": bid, "name": vo.get("name"),
                        "classId": vo.get("classId"), "ok": res["ok"], "private": private,
                        "diffs": res["diffs"][:4], "diffsCount": len(res["diffs"])})
        if res["ok"] and private:
            return {"vo": vo, "reused": True, "businessId": int(bid),
                    "checked": checked, "reason": "复用已有模板 %s(%s)" % (vo.get("name"), bid),
                    "classId": vo.get("classId")}
        if res["ok"] and not private:
            # 内容对得上但是公共模板：草稿里挂了也不渲染，必须另建私有模板
            checked[-1]["note"] = "内容是公共模板，后台不会渲染"

    basis = best_vo
    class_id = (basis or {}).get("classId")
    if class_id is None and candidates:
        class_id = candidates[0].get("classId")
    new_vo = build_size_template_vo(profile, want, base_vo=basis, class_id=class_id,
                                    name="%s-%s" % (prof["label"], "default"))
    return {"vo": new_vo, "reused": False, "businessId": None, "checked": checked,
            "reason": "没有完全匹配的模板（共查 %d 个），需新建" % len(checked),
            "classId": class_id}


def ensure_size_template(
    cat_id: int,
    schema: dict,
    profile: str,
    want: dict[str, dict[str, str]],
    sess,
    name_hint: str = "",
    draft_id: int = 0,
) -> dict[str, Any]:
    """`pick_size_template` 的可写版：没匹配就真的建一个（createSizeSpec），返回新 businessId。

    必须建**商品私有模板**（`catId=<叶子类目>` + `reusable=false`），
    公共同名模板（`reusable=true`）草稿里挂了也不渲染，后台照样显示「+ 添加尺码表」。
    """
    pick = pick_size_template(cat_id, schema, profile, want, sess=sess,
                              name_hint=name_hint, draft_id=draft_id)
    if pick["reused"] or not pick.get("vo"):
        return pick
    payload = build_sizespec_payload(pick["vo"], new_name=name_hint or pick["vo"].get("name"),
                                     cat_id=cat_id, reusable=False)
    new_id = sess.sizespec_create(payload)
    pick["businessId"] = int(new_id)
    pick["reused"] = False
    pick["reason"] = "新建私有模板 %s(%s)" % (name_hint or pick["vo"].get("name"), new_id)
    return pick


def build_sizespec_payload(
    vo: dict,
    new_name: str = "",
    cat_id: int | None = None,
    reusable: bool | None = None,
) -> dict:
    """把 sizespec 的 sizeSpecDataVO 组装成 createSizeSpec 的请求体。

    ⚠️ **`reusable` 决定模板能不能被商品真正用上**（实测 2026-11-26）：
        · `reusable=true`（catId 可为 null）→ 进店铺公共模板库，
          `rollPageQueryData` 能翻到，但**草稿只把它当"引用"，后台尺码区不会渲染**，
          刷新后依旧显示「+ 添加尺码表」。
        · `reusable=false` + `catId=<叶子类目>` → 商品私有模板，
          后台选中保存后草稿里的 `sizeTemplateIds` 就是它，**刷新后正常显示数值表**。
      所以要"真的挂上尺码表"必须建私有模板。
    """
    content = copy.deepcopy(vo.get("contentVO") or {})
    meta = content.get("meta") or {}
    elements = [{"id": e.get("id"), "name": e.get("name")}
                for e in (meta.get("elements") or [])]
    groups = [{"id": g.get("id"), "name": g.get("name"),
               "supplierUnnecessary": g.get("supplierUnnecessary")}
              for g in (meta.get("groups") or [])]
    records = [{"values": dict(r.get("values") or {})}
               for r in (content.get("records") or [])]

    if reusable is None:
        reusable = cat_id is None

    payload: dict[str, Any] = {
        "content": {
            "localSizeSource": content.get("localSizeSource", 1),
            "generalSizeType": content.get("generalSizeType", 1),
            "meta": {"elements": elements, "groups": groups},
            "records": records,
        },
        "catId": cat_id,
        "classId": vo.get("classId"),
        "name": new_name or (vo.get("name") or ""),
        "reusable": bool(reusable),
        "ext": {
            "manualGroupIdList": [],
            "isDoubleSize": False,
            "unEnbleAutoSelectedList": [10001, 10002, 10003, 10004, 10005, 10024, 10026],
        },
        "productCreateTime": None,
    }
    return payload


def set_size_value(vo: dict, size: str, element_id: int, value: str) -> bool:
    """在 sizeSpecDataVO 的 records 里改某一码某一量体项的值（原地修改）。

    element_id 与 values 的 key 一致（如 10002=胸围全围）。
    返回是否找到对应尺码行。
    """
    want = str(size).strip().upper()
    key = str(element_id)
    for rec in (vo.get("contentVO") or {}).get("records") or []:
        vals = rec.get("values") or {}
        if str(vals.get("1") or "").strip().upper() != want:
            continue
        vals[key] = str(value)
        for vl in rec.get("valueList") or []:
            if str(vl.get("id")) == key:
                vl["value"] = str(value)
                break
        else:
            rec.setdefault("valueList", []).append({"id": int(element_id), "value": str(value)})
        return True
    return False


def summarize_size_spec(vo: dict) -> dict[str, Any]:
    """把 sizespec 的 sizeSpecDataVO 压成 {模板名, 量体项, 逐码数值}，便于核对/展示。

    contentVO.records[].values 形如 {"1":"S","10002":"100","10003":"71","10004":"77.5"}
      · "1" = 尺码列
      · 其余 key 对应 meta.elements[].id（如 10002=胸围全围, 10003=衣长, 10004=袖长）
    """
    content = vo.get("contentVO") or {}
    meta = content.get("meta") or {}
    col_map: dict[str, str] = {"1": "尺码"}
    for el in meta.get("elements") or []:
        eid = str(el.get("id"))
        col_map[eid] = "%s(%s)" % (el.get("name") or eid, el.get("unit") or "")
    rows: list[dict[str, str]] = []
    for rec in content.get("records") or []:
        vals = rec.get("values") or {}
        row = {}
        for k, v in vals.items():
            if k == "1":
                row["尺码"] = v
            else:
                row[col_map.get(str(k), str(k))] = v
        rows.append(row)
    return {
        "businessId": vo.get("businessId"),
        "name": vo.get("name"),
        "className": vo.get("className"),
        "columns": list(col_map.values()),
        "rows": rows,
    }


def check_size_spec_matches(sizes_in_spec: list[str], size_spec_vo: dict) -> dict[str, Any]:
    """校验 spec 里的尺码集合与尺码表模板里的尺码是否一致。"""
    tmpl_sizes = []
    content = size_spec_vo.get("contentVO") or {}
    for rec in content.get("records") or []:
        sv = (rec.get("values") or {}).get("1")
        if sv:
            tmpl_sizes.append(str(sv).strip().upper())
    spec_sizes = [str(s).strip().upper() for s in sizes_in_spec]
    return {
        "template": size_spec_vo.get("name"),
        "template_sizes": tmpl_sizes,
        "spec_sizes": spec_sizes,
        "missing_in_template": [s for s in spec_sizes if s not in tmpl_sizes],
        "unused_in_spec": [s for s in tmpl_sizes if s not in spec_sizes],
        "ok": all(s in tmpl_sizes for s in spec_sizes),
    }


def verify_draft_readback(sent: dict, got: dict) -> dict[str, Any]:
    """比对 draft/save 发送值与 draft/query 回读值。

    ⚠ 回读结构与发送结构**不同层**（实测 2026-11-26）：
        · 运费/站点不在顶层 productShipmentReq / productSemiManagedReq，
          而是嵌在 productSaleExtAttr.productShipment / .productSemiManaged
        · 产地仍在 productWhExtAttr.productOrigin
    """
    sent_skus = [sku for skc in (sent.get("productSkcReqs") or []) for sku in (skc.get("productSkuReqs") or [])]
    got_skus = [sku for skc in (got.get("productSkcList") or []) for sku in (skc.get("productSkuList") or [])]
    got_flat = categories_dict_to_flat(got.get("categories") or {})
    sent_flat = {"cat%dId" % i: int(sent.get("cat%dId" % i) or 0) for i in range(1, 11)}

    sale_ext = got.get("productSaleExtAttr") or {}
    got_freight = (((sale_ext.get("productShipment") or {}).get("freightTemplate") or {})
                   .get("freightTemplateId"))
    sent_freight = (sent.get("productShipmentReq") or {}).get("freightTemplateId")
    got_sites = [b.get("siteId") for b in ((sale_ext.get("productSemiManaged") or {}).get("bindSites") or [])]
    sent_sites = (sent.get("productSemiManagedReq") or {}).get("bindSiteIds") or []
    got_origin = ((got.get("productWhExtAttr") or {}).get("productOrigin") or {}).get("countryShortName")
    sent_origin = (sent.get("productWhExtAttrReq") or {}).get("productOrigin", {}).get("countryShortName")

    checks = {
        "title_match": got.get("productName") == sent.get("productName"),
        "cat_match": got_flat == sent_flat,
        "props_count": (len(sent.get("productPropertyReqs") or []), len(got.get("productPropertyList") or [])),
        "skc_count": (len(sent.get("productSkcReqs") or []), len(got.get("productSkcList") or [])),
        "sku_count": (len(sent_skus), len(got_skus)),
        "carousel_count": (len(sent.get("carouselImageUrls") or []), len(got.get("carouselImageUrls") or [])),
        "freight_match": (sent_freight in (None, "")) or (got_freight == sent_freight),
        "sites_match": sorted(got_sites) == sorted(sent_sites),
        "origin_match": got_origin == sent_origin,
        "size_tpl_match": sorted(got.get("sizeTemplateIds") or []) == sorted(sent.get("sizeTemplateIds") or []),
        "sku_ext_codes": [s.get("extCode") for s in got_skus],
    }
    checks["ok"] = (
        checks["title_match"]
        and checks["cat_match"]
        and checks["skc_count"][0] == checks["skc_count"][1]
        and checks["sku_count"][0] == checks["sku_count"][1]
        and checks["freight_match"]
        and checks["sites_match"]
    )
    return checks
