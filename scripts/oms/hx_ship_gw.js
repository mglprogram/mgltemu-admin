#!/usr/bin/env node
/**
 * hx_ship_gw.js — 恒信/领星 OMS 真发货：走**内部网关 HTTP API**（紫鸟里已登录的 oms.xlwms.com
 * 页面内 `fetch`），不是 DOM 点点点。每条命令对应一个可单跑的段，整链用 `--stage`。
 *
 * 2026-11-12 实测：BS 30 / BD 46 / BAN 10 单走这条链路全部到「处理中」（=发货成功）。
 *
 * 用的接口（都是页面自己的网关，Track-Key 在页面里现算）：
 *   POST /woms/platform/order/list                 按平台单号定位（status="" 跨全部状态）
 *   GET  /woms/platform/order/orderInfo?orderNo=   订单详情（productInfoList[].sku = 系统SKU）
 *   GET  /woms/order/getAddressInfo?bizNo=         收件地址（脱敏值原样回传即可）
 *   POST /woms/productInfo/batchQuery              系统SKU → 产品名
 *   POST /woms/platform/order/update               分配仓库&物流（USW01 + 上传物流面单 + 承运商）
 *   POST /woms/platform/order/batchApprove         「确认并审核」 {orderNos:[...]}
 *   POST /woms/platform/order/batchToPending       「转待处理」 {optList:[...]}（orderNos 会报错）
 *
 * 段（`--stage`，顺序＝run_hx_fulfill 的 STAGES["ship"]）：
 *   gw_plan      从 split.json 取本批 HX 单号 → gw_plan.json
 *   gw_carriers  从 shein_label_<店>.json 取每单承运商 → gw_carriers.json
 *   gw_assign    逐单 update（写）
 *   gw_approve   分批 batchApprove；「订单不可编辑」视为已审核；仍失败的转待处理重试一轮（写）
 *   gw_verify    轮询回读状态 → 处理中/已发货=成功；获取异常=没买面单；生成出库单失败=无库存
 *
 * 直接跑整段：
 *   node scripts/oms/hx_ship_gw.js --stage all --store BS --split <split.json> --out <dir> --apply
 * 单跑：
 *   node scripts/oms/hx_ship_gw.js --stage gw_verify --out <dir>
 * 排查：
 *   node scripts/oms/hx_ship_gw.js inspect --po GSU1AU38U00N396
 *
 * ★ 硬前提：订单必须先**产品配对**（`productInfoList[].sku` 有值）。没配对会一直卡在
 *   「生成出库单失败」或直接被平台取消（2026-11-12：BS-102503-5SetB-S ×2 / BD-102105-3BGR-XL ×1）。
 * ★ 不要再用公开 OpenAPI 建单：`outboundOrder/create` 只出 status=0 草稿、无审核接口。
 */
'use strict';

const L = require('C:/Users/saing/.agents/skills/lingxing-oms-order-edit-api/scripts/lib.js');
const fs = require('fs');
const path = require('path');

const WH = 'USW01';
const CH_CODE = 'Upload_Shipping_Label';
const CH_NAME = '上传物流面单';

//: 回读判定：这些状态名算发货成功
const OK_STATUS_TEXT = ['处理中', '已发货', '已签收', '已完成'];
//: 失败原因归类（用户 2026-11-12 口径）
const FAIL_LABEL = '获取异常';          // 没买面单
const FAIL_NO_STOCK = '生成出库单失败';  // 无库存

function log() {
  console.log.apply(console, arguments);
}

function readJson(p, dflt) {
  try {
    return JSON.parse(fs.readFileSync(p, 'utf-8').replace(/^\uFEFF/, ''));
  } catch (e) {
    return dflt;
  }
}

function writeJson(p, obj) {
  fs.mkdirSync(path.dirname(path.resolve(p)), { recursive: true });
  fs.writeFileSync(p, JSON.stringify(obj, null, 1), 'utf-8');
}

// ---------------------------------------------------------------- 单步封装

async function findOrder(ws, po) {
  const rows = await L.findByPlatformOrderNo(ws, po);
  return rows[0] || null;
}

async function orderFull(ws, po) {
  const row = await findOrder(ws, po);
  if (!row) return { po, row: null };
  const info = await L.orderInfo(ws, row.orderNo);
  const addr = await L.addressInfo(ws, row.orderNo).catch(() => ({}));
  return { po, row, info, addr };
}

function carrierOf(carriers, po) {
  return (carriers && (carriers[po] || carriers[String(po).toUpperCase()])) || '';
}

// ---------------------------------------------------------------- 段实现

/** gw_plan：split.json → 本批单号清单 */
function stagePlan(opts) {
  const split = readJson(opts.split, null);
  if (!split) throw new Error('读不到 split.json：' + opts.split);
  const rows = ((split.groups || {}).HX) || [];
  const byPo = {};
  for (const r of rows) {
    const po = String(r.ord || '').trim();
    if (!po) continue;
    byPo[po] = byPo[po] || { po, store: opts.store || '', skus: [] };
    byPo[po].skus.push({ sku: String(r.sku || '').trim(), qty: Number(r.qty) || 1 });
  }
  // --pos-file 可用作补充（只有号没有 SKU 也行，OMS 里配过对就能发）
  if (opts.posFile && fs.existsSync(opts.posFile)) {
    for (const ln of fs.readFileSync(opts.posFile, 'utf-8').split(/\r?\n/)) {
      const po = ln.trim();
      if (po && !byPo[po]) byPo[po] = { po, store: opts.store || '', skus: [] };
    }
  }
  const orders = Object.values(byPo);
  writeJson(opts.planOut, { store: opts.store || '', count: orders.length, orders });
  log('[gw_plan] %d 单 → %s', orders.length, opts.planOut);
  return 0;
}

/** gw_carriers：shein_label_<店>.json → {po: carrier} */
function stageCarriers(opts) {
  let files = [];
  if (opts.label) files = [opts.label];
  else if (opts.out) {
    for (const f of fs.readdirSync(opts.out)) {
      if (/^shein_label_.*\.json$/.test(f)) files.push(path.join(opts.out, f));
    }
  }
  const mp = {};
  for (const f of files) {
    const d = readJson(f, {});
    for (const r of (d.rows || [])) {
      const po = String(r.order_no || '').trim();
      const c = String(r.carrier || '').trim();
      if (po && c) mp[po] = c;
    }
  }
  writeJson(opts.carrierOut, mp);
  log('[gw_carriers] %d 单有承运商（来源 %s）→ %s',
    Object.keys(mp).length, files.length ? files.map((f) => path.basename(f)).join(',') : '无', opts.carrierOut);
  return 0;
}

/** gw_assign：逐单 update（写） */
async function stageAssign(ws, opts) {
  const plan = readJson(opts.planOut, { orders: [] }).orders || [];
  const carriers = readJson(opts.carrierOut, {});
  const out = [];
  for (const it of plan) {
    let rec = { po: it.po, store: it.store, ok: false };
    try {
      const { row, info, addr } = await orderFull(ws, it.po);
      if (!row) { rec.why = '平台订单查不到'; out.push(rec); log('✗ %s 平台订单查不到', it.po); continue; }
      const st = Number(info.status);
      if (st !== 0) {           // 已审核/已处理 → 不需要再分配
        rec.ok = true; rec.skip = 'status=' + st; rec.orderNo = row.orderNo;
        out.push(rec); continue;
      }
      const paired = (info.productInfoList || []).filter((p) => p.sku)
        .map((p) => ({ sku: p.sku, qty: Number(p.qty || p.platformQty) || 1, name: p.productName || '' }));
      const missing = (info.productInfoList || []).filter((p) => !p.sku).map((p) => p.platformSku);
      if (missing.length) {
        rec.why = '未做产品配对'; rec.missing = missing;
        out.push(rec); log('✗ %s 未配对 %s', it.po, missing.join(',')); continue;
      }
      let items = paired;
      if (!items.length && it.skus && it.skus.length) items = it.skus;
      if (!items.length) { rec.why = '没有可发产品行'; out.push(rec); continue; }
      const names = await L.productNames(ws, items.map((s) => s.sku)).catch(() => ({}));
      items = items.map((s) => ({ sku: s.sku, qty: Number(s.qty) || 1, name: s.name || names[s.sku] || '' }));
      const payload = L.buildPayload(info, addr, items);
      payload.sendWhCode = WH;
      payload.logisticsChannelCode = CH_CODE;
      payload.logisticsChannelName = CH_NAME;
      const carrier = carrierOf(carriers, it.po);
      if (carrier) payload.logisticsCarrier = carrier;
      if (!opts.apply) {
        rec.ok = true; rec.dry = true; rec.carrier = carrier; out.push(rec); continue;
      }
      const r = await L.updateOrder(ws, payload);
      rec.code = r.code; rec.msg = r.msg || '';
      rec.carrier = carrier;
      rec.orderNo = row.orderNo;
      rec.ok = r.code === 200;
      if (!rec.ok) rec.why = 'update code=' + r.code + ' ' + (r.msg || '');
      out.push(rec);
      log('%s %s %s', rec.ok ? '✅' : '✗', it.po, rec.ok ? ('carrier=' + (carrier || '(空)')) : rec.why);
    } catch (e) {
      rec.why = 'ERR ' + String(e.message || e).slice(0, 120);
      out.push(rec); log('✗ %s %s', it.po, rec.why);
    }
    await L.sleep(400);
  }
  writeJson(opts.assignOut, out);
  const ok = out.filter((x) => x.ok).length;
  log('[gw_assign] %d/%d 分配成功%s', ok, out.length, opts.apply ? '' : '（预览）');
  return 0;
}

/** gw_approve：分批审核；不可编辑→已审核；仍失败→转待处理重试一轮（写） */
async function stageApprove(ws, opts) {
  const plan = readJson(opts.planOut, { orders: [] }).orders || [];
  const assign = readJson(opts.assignOut, []);
  const assignOk = new Set(assign.filter((x) => x.ok).map((x) => x.po));
  const todo = plan.filter((x) => assignOk.has(x.po));
  const carriers = readJson(opts.carrierOut, {});
  const out = [];
  const pending = [];
  for (const it of todo) {
    try {
      const { row, info } = await orderFull(ws, it.po);
      if (!row) { out.push({ po: it.po, ok: false, why: '平台订单查不到' }); continue; }
      if (OK_STATUS_TEXT.indexOf(info.statusName) >= 0 || [1, 2, 3].indexOf(Number(info.status)) >= 0) {
        out.push({ po: it.po, ok: true, skip: 'status=' + info.statusName }); continue;
      }
      if (!opts.apply) { out.push({ po: it.po, ok: true, dry: true }); continue; }
      const r = await L.run(ws, `(async () => {
        const r = await window.__lxCall('POST','/woms/platform/order/batchApprove',
          {orderNos:[${JSON.stringify(row.orderNo)}]});
        return JSON.stringify(r);
      })()`);
      const j = JSON.parse(r);
      const rec = { po: it.po, orderNo: row.orderNo, code: j.code,
                    ok: j.code === 200, data: j.data || null };
      if (!rec.ok) rec.why = 'approve code=' + j.code + ' ' + (j.msg || '');
      out.push(rec);
      log('%s %s %s', rec.ok ? '✅' : '✗', it.po, rec.ok ? '已审核' : rec.why);
    } catch (e) {
      const rec = { po: it.po, ok: false, why: 'ERR ' + String(e.message || e).slice(0, 120) };
      out.push(rec); log('✗ %s %s', it.po, rec.why);
    }
    await L.sleep(400);
  }
  // 审核失败（多为「生成出库单失败：缺承运商 / 无库存」）→ 转待处理后带承运商重来一轮
  const failed = out.filter((x) => !x.ok && !x.dry);
  if (opts.apply && opts.retryPending && failed.length) {
    log('[gw_approve] %d 单审核失败 → 转待处理重试一轮', failed.length);
    const nos = failed.map((x) => x.orderNo).filter(Boolean);
    if (nos.length) {
      const r = await L.run(ws, `(async () => {
        const r = await window.__lxCall('POST','/woms/platform/order/batchToPending',
          {optList:${JSON.stringify(nos)}});
        return JSON.stringify(r);
      })()`);
      log('   转待处理：%s', String(r).slice(0, 160));
      await L.sleep(2000);
      for (const f of failed) {
        try {
          const { row, info, addr } = await orderFull(ws, f.po);
          if (!row) continue;
          const paired = (info.productInfoList || []).filter((p) => p.sku)
            .map((p) => ({ sku: p.sku, qty: Number(p.qty || p.platformQty) || 1, name: p.productName || '' }));
          if (!paired.length) continue;
          const payload = L.buildPayload(info, addr, paired);
          payload.sendWhCode = WH;
          payload.logisticsChannelCode = CH_CODE;
          payload.logisticsChannelName = CH_NAME;
          const c = carrierOf(carriers, f.po);
          if (c) payload.logisticsCarrier = c;
          const u = await L.updateOrder(ws, payload);
          const a = await L.run(ws, `(async () => {
            const r = await window.__lxCall('POST','/woms/platform/order/batchApprove',
              {orderNos:[${JSON.stringify(row.orderNo)}]});
            return JSON.stringify(r);
          })()`);
          const aj = JSON.parse(a);
          f.retried = true; f.retryCode = aj.code; f.ok = aj.code === 200;
          if (f.ok) { f.why = ''; delete f.why; }
          log('   %s %s retry approve code=%s', f.ok ? '✅' : '✗', f.po, aj.code);
          await L.sleep(400);
        } catch (e) {
          log('   ✗ %s 重试异常 %s', f.po, String(e.message || e).slice(0, 100));
        }
      }
    }
  }
  writeJson(opts.approveOut, out);
  log('[gw_approve] %d/%d 审核通过%s', out.filter((x) => x.ok).length, out.length,
    opts.apply ? '' : '（预览）');
  return 0;
}

/** gw_verify：回读状态并落 hx_oms_ship.json（report 用这个文件） */
async function stageVerify(ws, opts) {
  const plan = readJson(opts.planOut, { orders: [] }).orders || [];
  const wait = Number(opts.verifyWait || 90);
  const deadline = Date.now() + wait * 1000;
  let rows = [];
  for (;;) {
    rows = [];
    for (const it of plan) {
      try {
        const { row, info } = await orderFull(ws, it.po);
        if (!row) { rows.push({ po: it.po, store: it.store, status: null, why: 'OMS 查不到该单' }); continue; }
        rows.push({ po: it.po, store: it.store, orderNo: row.orderNo,
                    status: Number(info.status), statusName: info.statusName || '',
                    track: info.logisticsTrackNo || info.logisticsTrackNos || '',
                    cause: String(row.auditCause || row.exceptionReason || '').slice(0, 120) });
      } catch (e) {
        rows.push({ po: it.po, store: it.store, status: null, why: 'ERR ' + String(e.message || e).slice(0, 80) });
      }
    }
    const allSettled = rows.every((x) => OK_STATUS_TEXT.indexOf(x.statusName) >= 0
      || (x.cause || '').indexOf(FAIL_NO_STOCK) >= 0 || (x.cause || '').indexOf(FAIL_LABEL) >= 0);
    if (allSettled || Date.now() > deadline) break;
    log('   等 OMS 生成出库单…（已过 %ds）', Math.round((Date.now() - (deadline - wait * 1000)) / 1000));
    await L.sleep(10000);
  }
  const ok = rows.filter((x) => OK_STATUS_TEXT.indexOf(x.statusName) >= 0);
  const fail = rows.filter((x) => OK_STATUS_TEXT.indexOf(x.statusName) < 0);
  const rep = {
    store: opts.store || 'HX', mode: 'gateway', applied: !!opts.apply,
    ok: ok.map((x) => ({ po: x.po, status: x.status, statusText: x.statusName, track: x.track })),
    fail: fail.map((x) => ({
      po: x.po, status: x.status,
      why: (x.cause || '').indexOf(FAIL_NO_STOCK) >= 0 ? '生成出库单失败（无库存）'
        : (x.cause || '').indexOf(FAIL_LABEL) >= 0 ? '获取异常（没买到面单）'
          : (x.why || x.cause || x.statusName || '未处理'),
    })),
    rows,
  };
  writeJson(opts.reportOut, rep);
  log('[gw_verify] 成功 %d｜失败 %d（%s）→ %s', ok.length, fail.length,
    [...new Set(fail.map((x) => x.why || x.cause || x.statusName))].join(' / ') || '-', opts.reportOut);
  return 0;
}

// ---------------------------------------------------------------- 排查用

async function inspect(ws, po) {
  const { row, info, addr } = await orderFull(ws, po);
  if (!row) return { po, found: false };
  return {
    po, found: true, orderNo: info.orderNo, status: info.status, statusName: info.statusName,
    sendWhCode: info.sendWhCode, channel: info.logisticsChannelCode, carrier: info.logisticsCarrier,
    country: addr.countryRegionCode, province: addr.provinceName, city: addr.cityName,
    products: (info.productInfoList || []).map((p) => ({ platformSku: p.platformSku, sku: p.sku, qty: p.qty })),
  };
}

// ---------------------------------------------------------------- main

function parseArgs(argv) {
  const a = { mode: '', apply: false, retryPending: true };
  for (let i = 0; i < argv.length; i++) {
    const k = argv[i];
    if (!k.startsWith('--')) { if (!a.mode) a.mode = k; continue; }
    const key = k.slice(2);
    if (key === 'apply') { a.apply = true; continue; }
    if (key === 'no-retry-pending') { a.retryPending = false; continue; }
    a[key.replace(/-([a-z])/g, (m, c) => c.toUpperCase())] = argv[i + 1];
    i++;
  }
  return a;
}

async function main() {
  const a = parseArgs(process.argv.slice(2));
  const mode = a.mode || (a.stage ? 'stage' : 'inspect');
  const opts = {
    store: a.store || '', split: a.split || '', posFile: a.posFile || '',
    out: a.out || '', apply: !!a.apply, retryPending: a.retryPending !== false,
    label: a.label || '', verifyWait: a.verifyWait || 90,
  };
  if (opts.out) {
    opts.planOut = a.plan || path.join(opts.out, 'gw_plan.json');
    opts.carrierOut = a.carriers || path.join(opts.out, 'gw_carriers.json');
    opts.assignOut = path.join(opts.out, 'gw_assign.json');
    opts.approveOut = path.join(opts.out, 'gw_approve.json');
    opts.reportOut = a.report || path.join(opts.out, 'hx_oms_ship.json');
  }
  const stage = a.stage || '';
  const needsBrowser = !['gw_plan', 'gw_carriers'].includes(stage) && mode !== 'plan';

  let ws = null;
  if (needsBrowser) {
    const att = await L.attach();
    if (!att) throw new Error('连不上 OMS 页面（紫鸟窗口里要开着 oms.xlwms.com 且已登录）');
    log('[attach] port=%s url=%s', att.port, att.url);
    await L.prepare(att.ws);
    ws = att.ws;
  }

  if (mode === 'inspect') return log(JSON.stringify(await inspect(ws, a.po), null, 1)) || 0;

  if (stage === 'gw_plan') return stagePlan(opts);
  if (stage === 'gw_carriers') return stageCarriers(opts);
  if (stage === 'gw_assign') return stageAssign(ws, opts);
  if (stage === 'gw_approve') return stageApprove(ws, opts);
  if (stage === 'gw_verify') return stageVerify(ws, opts);
  if (stage === 'all') {
    stagePlan(opts); stageCarriers(opts);
    await stageAssign(ws, opts); await stageApprove(ws, opts); await stageVerify(ws, opts);
    return 0;
  }
  throw new Error('未知 stage：' + stage);
}

main().then((c) => process.exit(c || 0)).catch((e) => {
  console.error('[FATAL]', e && e.message ? e.message : e);
  process.exit(1);
});
