# -*- coding: utf-8 -*-
"""TEMU 上链接本地构造与校验（纯离线逻辑，不碰网络）。

把人可读的 `spec.json` 翻译成 `POST /visage-agent-seller/product/draft/save`
所需的 41 字段完整 payload，并在发请求前做本地硬校验。
"""
from __future__ import annotations

import copy
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
