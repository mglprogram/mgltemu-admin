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

//: ★ 2026-11-20 恒信仓订单走「Temu自动分仓」。
//:   真实编码从 OMS 前端源码 platform.cd4931e2.js 挖出：TEMU_WAREHOUSE_CODE = 'Temu Selected Warehouse'
//:   （whCode 与 logisticsChannelCode 同值；选仓后渠道自动跟着变）。
//:
//: ★★ 2026-11-21 用户令（改回**按店分流**）：用户原话
//:   「今天 BS 发货为什么使用 temu 的发货仓（temu自动分仓）；**shein 就是之前那种方式去选择**」。
//:   11-20 那条是**全局**改的（所有店都成 Temu自动分仓）⇒ SHEIN 店（BS/BD/BAN）被打错仓，
//:   帕沃尔/领星那边根本没有「Temu Selected Warehouse」这个仓。
//:   现在口径按**平台**分：
//:     · TEMU 店（BHY / BEL / KinglamanSport）→ `Temu Selected Warehouse` + `Temu自动分配渠道`
//:     · SHEIN 店（BS / BD / BAN / BE）      → 旧口径 `ZOO WAREHOUSE ⑥美西`(USW01) + `上传物流面单`(Upload_Shipping_Label)
//:   环境变量 `HX_SEND_WH` / `HX_CHANNEL` / `HX_CHANNEL_NAME` 仍然最高优先级（临时排障用）。
const TEMU_STORES = ['BHY', 'BEL', 'KINGLAMANSPORT'];
const WH_TEMU = process.env.HX_SEND_WH || 'Temu Selected Warehouse';
const CH_TEMU = process.env.HX_CHANNEL || 'Temu Selected Warehouse';
const CH_TEMU_NAME = process.env.HX_CHANNEL_NAME || 'Temu自动分配渠道';
//: SHEIN 店沿用 2026-11-12 定稿的老口径（UI 图：普通发货 / ZOO WAREHOUSE ⑥美西 USW01 / 上传物流面单）
//:
//: ★★ 2026-11-23 修（今天 BS/BD/BAN 全挂在「待处理」的根因）：
//:   `sendWhCode` 要的是**仓编码 `USW01`**，不是仓库显示名 `ZOO WAREHOUSE ⑥美西`。
//:   11-21 我照 UI 截图把显示名写进去 ⇒ 审核报
//:     「当前仓库[ZOO WAREHOUSE ⑥美西]不可用，请修改;仓库不可使用选定物流渠道[Upload_Shipping_Label]」
//:   而最初跑通那版（5ea62e7，BS31/BD45/BAN10 全过审）用的是 `USW01`。
//:   `ZOO WAREHOUSE ⑥美西` 只作为**注释里的显示名**保留，不要再填进 payload。
const WH_SHEIN = process.env.HX_SEND_WH_SHEIN || 'USW01';
const CH_SHEIN = process.env.HX_CHANNEL_SHEIN || 'Upload_Shipping_Label';
const CH_SHEIN_NAME = process.env.HX_CHANNEL_NAME_SHEIN || '上传物流面单';

/** 这家店走哪套仓/渠道（纯函数：store 大小写不敏感）。
 *  ★ 2026-11-22：传进来的不只是店后缀，也可能是领星里的 `storeName`
 *    （例如 KLS 半托管店的领星 storeName 是 `Temu-BK`）⇒ 大小写不敏感 + 前缀匹配。
 */
function whOf(store) {
  const s = String(store || '').trim().toUpperCase();
  const isTemu = TEMU_STORES.some((k) => s === k || s.startsWith(k) || k.startsWith(s))
    || s.startsWith('TEMU');
  // ★★ 2026-11-25 用户令：「不要用 Temu Selected Warehouse，**改成跟 shein 一样的仓库分配**」
  //   ⇒ TEMU 店（BHY/BEL/KLS）与 SHEIN 店**统一**走
  //     `USW01`（ZOO WAREHOUSE ⑥美西）+ `Upload_Shipping_Label`（上传物流面单）。
  //   历史：11-20 全局改成 Temu Selected Warehouse → 11-21 按店分流（SHEIN 回 USW01）
  //        → 11-25 TEMU 也回到 USW01（用户口径：两边一致）。
  //   环境变量 HX_SEND_WH / HX_SEND_WH_SHEIN 仍最高优先级（临时排障用）。
  return {
    wh: isTemu ? (process.env.HX_SEND_WH || WH_SHEIN) : WH_SHEIN,
    chCode: isTemu ? (process.env.HX_CHANNEL || CH_SHEIN) : CH_SHEIN,
    chName: isTemu ? (process.env.HX_CHANNEL_NAME || CH_SHEIN_NAME) : CH_SHEIN_NAME,
    platform: isTemu ? 'TEMU' : 'SHEIN',
  };
}

//: 兼容旧引用（gw_assign 里真正用的是 `whOf(opts.store)`，见下）
const WH = WH_TEMU;
const CH_CODE = CH_TEMU;
const CH_NAME = CH_TEMU_NAME;

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

/**
 * ★ 2026-11-23 用户令：「order/list 这步在 plan 阶段已经拿过 orderNo，可以缓存起来」
 *   ⚠ 2026-11-23 实测：该接口**不支持多单号**（空格拼接返回 0 条）⇒ 分批内逐单查，
 *   结果落 `opts.orderNoCacheOut`，后续 assign/approve/verify 都先查缓存，命中就不再往返。
 */
function _cachePath(opts) {
  return (opts && opts.orderNoCacheOut) || (opts && opts.out ? path.join(opts.out, 'order_nos.json') : '');
}

function loadOrderNoCache(opts) {
  const p = _cachePath(opts);
  const d = p ? readJson(p, {}) : {};
  return (d && typeof d === 'object') ? d : {};
}

function saveOrderNoCache(opts, cache) {
  const p = _cachePath(opts);
  if (p) writeJson(p, cache || {});
}

/** 批量把「平台单号 → orderNo/status」查出来（按 BATCH_MAX 分批），返回 map。 */
async function lookupOrderNos(ws, pos, log) {
  const map = {};
  const list = (pos || []).filter(Boolean);
  // ⚠ 2026-11-23 实测：`platform/order/list` 的 `platformOrderNo` **只认单个单号**，
  //   空格/逗号拼多个都返回 0 条 ⇒ 只能逐个查。用 6 路并发把 200 单压到约 20~30 秒，
  //   `size` 固定 20（大 size + 空条件会拖慢/超时）。
  const CONC = ASSIGN_CONCURRENCY;
  let idx = 0;
  let done = 0;
  async function worker() {
    for (;;) {
      const k = idx++;
      if (k >= list.length) return;
      const po = list[k];
      try {
        const raw = await L.run(ws, `(async () => {
          const body = {cancelWay:"",countKind:"orderCount",current:1,deliveryOptionTypes:"",logisticsChannels:"",
            markShipmentStatus:"",orderLabelCodes:[],pickupStatusList:[],platformCodes:"",
            platformOrderNo:${JSON.stringify(po)},platformWarehouses:"",printingStatus:"",receiptCountries:"",
            sendWarehouses:"",siteCodes:"",size:20,status:"",storeCodes:"",transitStatusList:[]};
          const r = await window.__lxCall('POST','/woms/platform/order/list', body);
          const rec = (r.data && (r.data.records || r.data.list)) || [];
          const hit = rec.filter(function(x){ return x.platformOrderNo === ${JSON.stringify(po)}; })[0];
          return JSON.stringify({code:r.code, row: hit ? {po:hit.platformOrderNo, orderNo:hit.orderNo,
            store:hit.storeName, status:hit.status, statusName:hit.statusName} : null});
        })()`, 120000);
        const d = JSON.parse(raw);
        if (d.row) map[d.row.po] = d.row;
      } catch (e) {
        if (log) log('   [查单] %s 异常 %s', po, String(e.message || e).slice(0, 80));
      }
      done++;
      if (log && done % 50 === 0) log('   [查单] %d/%d', done, list.length);
    }
  }
  await Promise.all(Array.from({ length: Math.min(CONC, list.length) }, worker));
  if (log) log('   [查单] 完成 %d/%d（命中 %d）', done, list.length, Object.keys(map).length);
  return map;
}

async function orderFullByNo(ws, orderNo) {
  if (!orderNo) return { info: null, addr: {} };
  const info = await L.orderInfo(ws, orderNo);
  const addr = await L.addressInfo(ws, orderNo).catch(() => ({}));
  return { info, addr };
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

/**
 * gw_plan：本批单号清单
 *   来源 A：--split <split.json> 的 groups.HX
 *   来源 B（2026-11-21 用户令）：`--stage gw_plan --query` 直接去 OMS 筛单，
 *     口径 = 状态待处理 + 发货仓 USW01（恒信仓系统仓）+ 指定平台/店铺。
 *     用户原话：「你直接去筛选恒信仓的订单…直接去批量搜索平台订单，直接选temu自动分仓就行了」
 *   ★ 注意 OMS /platform/order/list 的筛选数组必须是**字符串**（"BHY,BEL"），
 *     传数组直接 400「请求体格式错误」；storeCodes 只能精确命中、不支持前缀。
 */
/**
 * 自动打开领星 OMS 店铺窗口（无人值守自愈用）。
 * 窗口/店铺：TK-Savonire-YJF，storeId 27663658415319（LM739）。
 * 返回 true=命令发出去了（是否连上由调用方重试判定）。
 */
function autoOpenOms() {
  try {
    const { execFileSync } = require('child_process');
    const out = execFileSync(process.env.COMSPEC || 'cmd.exe',
      ['/c', 'ziniao-cli', 'store', 'open', '--id', '27663658415319',
        '--url', 'https://oms.xlwms.com/platform/order/list'],
      { timeout: 180000, encoding: 'utf-8', stdio: ['ignore', 'pipe', 'pipe'] });
    log('[attach] ziniao-cli 已执行：%s', String(out).replace(/\s+/g, ' ').slice(0, 160));
    return true;
  } catch (e) {
    log('[attach] 打开 OMS 窗口失败：%s', String(e.message || e).slice(0, 160));
    return false;
  }
}

/**
 * 强制重启卡死的 OMS 窗口（2026-11-25 真机自愈补丁）。
 *
 * 为什么要它：当天 BD 的 hx_ship 连炸 3 轮，报「连不上 OMS 页面」，但窗口其实开着
 * （端口在、URL 也对、标题「销售订单 - OMS」）。逐层探到根因：
 *   ① CDP 端口能发现、WebSocket 能 connect；
 *   ② 但该页面对 **任何** Runtime.evaluate 都不响应 —— 连 `1+1` 都超时；
 *   ③ 同窗口里的扩展页 `1+1` 正常返回 2 ⇒ **不是 CDP 通道问题，是这一个渲染进程卡死**；
 *   ④ `location.reload()` 也发不进去（同一个死掉的渲染进程）；
 *   ⑤ 走 HTTP 端点 `/json/close/<id>` 能关掉它，但窗口随后只剩扩展页，
 *      `ziniao-cli store open` 报 `reused:true` 却**不再开新页** ⇒ 必须 close → open 整窗重启。
 * ⇒ 「窗口开着」不等于「页面活着」。只重试 attach 永远不会恢复，必须重启窗口。
 *
 * 判据（只做最轻的探测）：对所有 CDP 端口扫 `xlwms` 页，连上后 3 秒内能 eval 出 `1+1` 即视为活。
 */
async function reviveOmsWindow() {
  const { execFileSync } = require('child_process');
  const http = require('http');

  const httpGet = (port, p) => new Promise((resolve) => {
    const req = http.get({ host: '127.0.0.1', port, path: p, timeout: 8000 }, (res) => {
      let b = '';
      res.on('data', (d) => { b += d; });
      res.on('end', () => resolve(b));
    });
    req.on('error', () => resolve(''));
    req.on('timeout', () => { req.destroy(); resolve(''); });
  });

  // ① 找出所有 oms 页，逐个探活
  let ports = [];
  try { ports = await L.findCdpPorts(); } catch (e) { ports = []; }
  const dead = [];
  for (const port of ports) {
    const raw = await httpGet(port, '/json/list');
    if (!raw) continue;
    let list = [];
    try { list = JSON.parse(raw); } catch (e) { continue; }
    for (const t of list) {
      if (t.type !== 'page' || (t.url || '').indexOf('oms.xlwms.com') < 0) continue;
      let alive = false;
      try {
        const ws = await L.connect(t.webSocketDebuggerUrl);
        alive = await Promise.race([
          L.evaluate(ws, '1+1').then((v) => v === 2),
          new Promise((ok) => setTimeout(() => ok(false), 3000)),
        ]);
        try { ws.close(); } catch (e) { /* ignore */ }
      } catch (e) { alive = false; }
      if (!alive) dead.push({ port, id: t.id, url: t.url });
    }
  }
  if (!dead.length) return false;   // 没有卡死页 ⇒ 不需要重启

  log('[attach] 检测到 %d 个 OMS 页无响应（渲染进程卡死）→ 强制关 target 并重启窗口', dead.length);
  for (const d of dead) {
    const r = await httpGet(d.port, '/json/close/' + d.id);
    log('[attach] 关闭卡死页 %s → %s', d.id.slice(0, 12), JSON.stringify(String(r).slice(0, 40)));
  }
  await L.sleep(3000);
  try {
    execFileSync(process.env.COMSPEC || 'cmd.exe',
      ['/c', 'ziniao-cli', 'store', 'close', '--id', '27663658415319'],
      { timeout: 120000, encoding: 'utf-8', stdio: ['ignore', 'pipe', 'pipe'] });
    log('[attach] 窗口已关闭，准备重开');
  } catch (e) {
    log('[attach] 关闭窗口失败（继续试重开）：%s', String(e.message || e).slice(0, 120));
  }
  await L.sleep(6000);
  return true;
}

async function stagePlan(ws, opts) {
  // B：直接从 OMS 筛（需要浏览器）
  if (opts.query) {
    const codes = String(opts.queryStoreCodes || opts.store || '').split(',').map((s) => s.trim()).filter(Boolean);
    const body = {
      current: 1, size: Number(opts.querySize || 500), status: opts.queryStatus === undefined ? '0' : String(opts.queryStatus),
      platformCodes: String(opts.queryPlatform || 'TEMU'),
      sendWarehouses: String(opts.queryWh || 'USW01'),
      // ★ storeCodes 只能精确命中且多值会被 AND 掉（实测 "BHY" 直接 0 单）→ 一律不传给接口，取回后本地过滤
      storeCodes: '',
    };
    if (!ws) throw new Error('--query 需要连 OMS（紫鸟窗口里要开着 oms.xlwms.com 且已登录）');
    const raw = await L.run(ws, `(async () => {
      // ★ 必须翻页：接口 size 上限 500（更多会 400），待处理 USW01 实测 747 单分 2 页
      const b = ${JSON.stringify(body)};
      let rec = [], total = 0, pages = 1;
      for (let cur = 1; cur <= 20; cur++) {
        const r = await window.__lxCall('POST','/woms/platform/order/list', Object.assign({}, b, {current: cur}));
        if (r.code !== 200) return JSON.stringify({code:r.code, msg:r.msg, total:0, rows:[]});
        const d0 = r.data || {};
        const chunk = d0.records || d0.list || [];
        total = d0.total || chunk.length; pages = d0.pages || pages;
        rec = rec.concat(chunk);
        if (chunk.length < (b.size || 500) || cur >= pages) break;
      }
      return JSON.stringify({code:200, total: total, rows: rec.map(function(x){
        return {po:x.platformOrderNo, orderNo:x.orderNo, store:x.storeName, status:x.status, statusName:x.statusName,
                wh:x.sendWhCode, whName:x.sendWhName, carrier:x.logisticsCarrier, channelName:x.logisticsChannelName};})});
    })()`, 300000);
    const d = JSON.parse(raw);
    if (d.code !== 200) throw new Error('OMS 筛单 code=' + d.code + ' ' + d.msg);
    const keep = codes.length ? d.rows.filter((x) => codes.includes(x.store)) : d.rows;
    const orders = keep.map((x) => ({ po: x.po, orderNo: x.orderNo, store: x.store, fromQuery: true,
      skus: [], statusName: x.statusName, whName: x.whName, carrier: x.carrier }));
    writeJson(opts.planOut, { store: codes.join(',') || 'ALL', source: 'oms-query', query: body,
      count: orders.length, orders });
    // ★ 2026-11-23：plan 已经拿到 orderNo → 落缓存，后面 assign/approve/verify 不再重查
    const c0 = loadOrderNoCache(opts);
    orders.forEach((o) => { if (o.po && o.orderNo) c0[o.po] = { po: o.po, orderNo: o.orderNo, store: o.store }; });
    saveOrderNoCache(opts, c0);
    log('[gw_plan] OMS 筛单 %d 单（平台 %s｜仓 %s｜状态 %s｜店 %s）→ %s',
      orders.length, body.platformCodes, body.sendWarehouses, body.status, body.storeCodes || '全部', opts.planOut);
    return 0;
  }

  const split = readJson(opts.split, null);
  const posFileRows = (opts.posFile && fs.existsSync(opts.posFile))
    ? fs.readFileSync(opts.posFile, 'utf-8').split(/\r?\n/).map((s) => s.trim()).filter(Boolean)
    : [];
  if (!split && !posFileRows.length && !(opts.pos || '').trim()) {
    throw new Error('没有单号来源：给 --split / --pos-file / --pos，或加 --query 直接从 OMS 筛单');
  }
  const rows = ((split && split.groups || {}).HX) || [];
  const byPo = {};
  for (const r of rows) {
    const po = String(r.ord || '').trim();
    if (!po) continue;
    byPo[po] = byPo[po] || { po, store: opts.store || '', skus: [] };
    byPo[po].skus.push({ sku: String(r.sku || '').trim(), qty: Number(r.qty) || 1 });
  }
  // --pos-file / --pos 可用作补充或唯一来源（只有号没有 SKU 也行，OMS 里配过对就能发）
  const extraPos = posFileRows.concat(String(opts.pos || '').split(/[,，\s]+/).map((s) => s.trim()).filter(Boolean));
  for (const po of extraPos) {
    if (po && !byPo[po]) byPo[po] = { po, store: opts.store || '', skus: [] };
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

  // ★ 2026-11-23 用户令：orderNo 走缓存（plan/assign 已经查过的就不再往返），
  //   没缓存的先**批量**查一次（多平台单号空格拼接，BATCH_MAX 一批）。
  const cache = loadOrderNoCache(opts);
  const miss = plan.filter((it) => !(cache[it.po] && cache[it.po].orderNo)).map((it) => it.po);
  if (miss.length) {
    log('[gw_assign] 批量查单 %d 个（缓存命中 %d）', miss.length, plan.length - miss.length);
    const found = await lookupOrderNos(ws, miss, log);
    Object.assign(cache, found);
    saveOrderNoCache(opts, cache);
  }

  async function doOne(it) {
    const rec = { po: it.po, store: it.store, ok: false };
    try {
      const hit = cache[it.po];
      if (!hit || !hit.orderNo) {
        rec.why = '平台订单查不到'; log('✗ %s 平台订单查不到', it.po); return rec;
      }
      rec.orderNo = hit.orderNo;
      const { info, addr } = await orderFullByNo(ws, hit.orderNo);
      if (!info) { rec.why = '平台订单查不到'; log('✗ %s orderInfo 为空', it.po); return rec; }
      const st = Number(info.status);
      if (st !== 0) {
        rec.ok = true; rec.skip = 'status=' + st; return rec;
      }
      const missing = (info.productInfoList || []).filter((p) => !p.sku).map((p) => p.platformSku);
      const paired = (info.productInfoList || []).filter((p) => p.sku)
        .map((p) => ({ sku: p.sku, qty: Number(p.qty || p.platformQty) || 1, name: p.productName || '' }));

      if (opts.noSkuChange) {
        // ★ 2026-11-21：原样带回 platformSku / platformProductName / platformQty，
        //   否则 update 会把平台SKU清空 → 领星判「已修改过sku，无法使用定仓定配渠道」→ 取面单失败
        const cur = (info.productInfoList || []).filter((p) => p.sku)
          .map((p) => ({
            sku: p.sku, qty: Number(p.qty || p.platformQty) || 1, name: p.productName || '',
            platformSku: p.platformSku || '', platformProductName: p.platformProductName || '',
            platformQty: p.platformQty,
          }));
        if (missing.length) {
          rec.why = '平台SKU未配对（不改SKU，跳过）'; rec.missing = missing;
          log('✗ %s 未配对 %s（不改SKU，跳过）', it.po, missing.join(','));
          return rec;
        }
        if (!cur.length) { rec.why = '没有可发产品行'; return rec; }
        const payload0 = L.buildPayload(info, addr, cur);
        const w0 = whOf(it.store);
        payload0.sendWhCode = w0.wh;
        payload0.logisticsChannelCode = w0.chCode;
        payload0.logisticsChannelName = w0.chName;
        const carrier0 = carrierOf(carriers, it.po);
        if (carrier0) payload0.logisticsCarrier = carrier0;
        rec.carrier = carrier0; rec.skuUnchanged = true;
        if (!opts.apply) { rec.ok = true; rec.dry = true; return rec; }
        const r0 = await L.updateOrder(ws, payload0);
        rec.code = r0.code; rec.msg = r0.msg || '';
        rec.ok = r0.code === 200;
        if (!rec.ok) rec.why = 'update code=' + r0.code + ' ' + (r0.msg || '');
        log('%s %s %s', rec.ok ? '✅' : '✗', it.po, rec.ok ? ('carrier=' + (carrier0 || '(空)')) : rec.why);
        return rec;
      }

      // 老行为（--allow-sku-change）：整块换成系统SKU
      let items = paired;
      if (!items.length && it.skus && it.skus.length) items = it.skus;
      if (!items.length) { rec.why = '没有可发产品行'; return rec; }
      const names = await L.productNames(ws, items.map((s) => s.sku)).catch(() => ({}));
      items = items.map((s) => ({ sku: s.sku, qty: Number(s.qty) || 1, name: s.name || names[s.sku] || '' }));
      const payload = L.buildPayload(info, addr, items);
      const w1 = whOf(it.store);
      payload.sendWhCode = w1.wh;
      payload.logisticsChannelCode = w1.chCode;
      payload.logisticsChannelName = w1.chName;
      const carrier = carrierOf(carriers, it.po);
      if (carrier) payload.logisticsCarrier = carrier;
      rec.carrier = carrier;
      if (!opts.apply) { rec.ok = true; rec.dry = true; return rec; }
      const r = await L.updateOrder(ws, payload);
      rec.code = r.code; rec.msg = r.msg || '';
      rec.ok = r.code === 200;
      if (!rec.ok) rec.why = 'update code=' + r.code + ' ' + (r.msg || '');
      log('%s %s %s', rec.ok ? '✅' : '✗', it.po, rec.ok ? ('carrier=' + (carrier || '(空)')) : rec.why);
      return rec;
    } catch (e) {
      rec.why = 'ERR ' + String(e.message || e).slice(0, 120);
      log('✗ %s %s', it.po, rec.why);
      return rec;
    }
  }

  // ★ 2026-11-23 用户令：并发按能跑的上限来。每单 3 次 CDP 往返，实测窗口能扛 6~8 路；
  //   用 HX_ASSIGN_CONCURRENCY 可调（默认 6，硬上限 8）。
  log('[gw_assign] 并发 %d，共 %d 单', ASSIGN_CONCURRENCY, plan.length);
  let idx = 0;
  async function worker() {
    for (;;) {
      const k = idx++;
      if (k >= plan.length) return;
      const rec = await doOne(plan[k]);
      out[k] = rec;
    }
  }
  await Promise.all(Array.from({ length: Math.min(ASSIGN_CONCURRENCY, plan.length) }, worker));
  for (let k = 0; k < plan.length; k++) if (!out[k]) out[k] = { po: plan[k].po, store: plan[k].store, ok: false, why: '未执行' };

  writeJson(opts.assignOut, out);
  const ok = out.filter((x) => x.ok).length;
  log('[gw_assign] %d/%d 分配成功%s', ok, out.length, opts.apply ? '' : '（预览）');
  return 0;
}

//: ★ 2026-11-23 用户令：批量上限按 200 走（实测 batchApprove / batchToPending /
//:   platform/order/list 传 300 条都只是「单据不存在」，没有大小上限报错）。
const BATCH_MAX = 200;
//: gw_assign 并发度（每单 3 次 CDP 往返，串行 195 单要 18 分钟 ⇒ 并发 6 压到 3~4 分钟）。
//: 上限 8：再高页面/网关就会被同一扇 OMS 窗口的 `fetch` 打挤，失败率反而升。
const ASSIGN_CONCURRENCY = Math.max(1, Math.min(8, Number(process.env.HX_ASSIGN_CONCURRENCY || 6)));

/** 把「一单一批」的 batchApprove 打分批评审；返回 Map(orderNo → {ok, msg, succ})。 */
async function approveInBatches(ws, orderNos, log) {
  const res = new Map();
  const list = (orderNos || []).filter(Boolean);
  for (let i = 0; i < list.length; i += BATCH_MAX) {
    const chunk = list.slice(i, i + BATCH_MAX);
    let j = {};
    try {
      const raw = await L.run(ws, `(async () => {
        const r = await window.__lxCall('POST','/woms/platform/order/batchApprove',
          {orderNos:${JSON.stringify(chunk)}});
        return JSON.stringify(r);
      })()`, 300000);
      j = JSON.parse(raw);
    } catch (e) {
      chunk.forEach((n) => res.set(n, { ok: false, msg: 'ERR ' + String(e.message || e).slice(0, 100) }));
      continue;
    }
    const dd = j.data || {};
    const failMap = {};
    (dd.failList || []).forEach((f) => {
      const k = f.orderNo || f.bizNo || f.platformOrderNo || '';
      if (k) failMap[k] = f.errorMsg || '';
    });
    for (const no of chunk) {
      const bad = failMap[no];
      // 没进 failList 且接口 200 ⇒ 这批里算通过（顶层 code 只是受理，见 gw_approve 注释）
      const ok = j.code === 200 && !bad;
      res.set(no, { ok, msg: bad || (j.code === 200 ? '' : (j.msg || '')), succ: dd.successQuantity });
    }
    log('   [approve] 批 %d-%d（%d 单）：code=%s successQty=%s failQty=%s',
      i + 1, i + chunk.length, chunk.length, j.code, dd.successQuantity, dd.failQuantity);
  }
  return res;
}

/** gw_approve：批批评审（200/批）；仍失败→转待处理重试一轮（写） */
async function stageApprove(ws, opts) {
  const plan = readJson(opts.planOut, { orders: [] }).orders || [];
  const assign = readJson(opts.assignOut, []);
  const assignOk = new Set(assign.filter((x) => x.ok).map((x) => x.po));
  const orderNoOf = {};
  assign.forEach((x) => { if (x.po && x.orderNo) orderNoOf[x.po] = x.orderNo; });
  const todo = plan.filter((x) => assignOk.has(x.po));
  const carriers = readJson(opts.carrierOut, {});
  const out = [];

  if (!opts.apply) {
    todo.forEach((it) => out.push({ po: it.po, ok: true, dry: true }));
    writeJson(opts.approveOut, out);
    log('[gw_approve] %d/%d 审核通过（预览）', out.length, out.length);
    return 0;
  }

  // ① 取 orderNo：优先用 gw_assign 已经查到的（省掉一次 order/list）
  const need = todo.filter((it) => !orderNoOf[it.po]);
  if (need.length) {
    log('[gw_approve] %d 单在 assign 阶段没拿到 orderNo → 补查', need.length);
    const found = await lookupOrderNos(ws, need.map((x) => x.po));
    Object.assign(orderNoOf, found);
  }
  const targets = [];
  for (const it of todo) {
    const no = orderNoOf[it.po];
    if (!no) { out.push({ po: it.po, ok: false, why: '平台订单查不到' }); continue; }
    targets.push({ po: it.po, orderNo: no, store: it.store });
  }

  // ② 批批评审（BATCH_MAX=200）
  log('[gw_approve] 批量审核 %d 单（每批 %d）', targets.length, BATCH_MAX);
  const res = await approveInBatches(ws, targets.map((t) => t.orderNo), log);
  for (const t of targets) {
    const r = res.get(t.orderNo) || { ok: false, msg: '未返回' };
    out.push({ po: t.po, orderNo: t.orderNo, ok: !!r.ok, successQty: r.succ,
      why: r.ok ? undefined : ('approve 未通过：' + String(r.msg || '').slice(0, 200)) });
  }
  log('[gw_approve] 首轮通过 %d/%d', out.filter((x) => x.ok).length, out.length);

  // ③ 失败 → 转待处理 → 带承运商重来一轮（仍是批量）
  const failed = out.filter((x) => !x.ok);
  if (opts.retryPending && failed.length) {
    log('[gw_approve] %d 单审核失败 → 转待处理重试一轮', failed.length);
    for (let i = 0; i < failed.length; i += BATCH_MAX) {
      const chunk = failed.slice(i, i + BATCH_MAX);
      try {
        const raw = await L.run(ws, `(async () => {
          const r = await window.__lxCall('POST','/woms/platform/order/batchToPending',
            {optList:${JSON.stringify(chunk.map((f) => f.orderNo))}});
          return JSON.stringify(r);
        })()`, 300000);
        log('   转待处理批 %d：%s', chunk.length, String(raw).slice(0, 120));
      } catch (e) {
        log('   转待处理异常：%s', String(e.message || e).slice(0, 100));
      }
    }
    await L.sleep(2000);
    const retryOk = [];
    for (const f of failed) {
      try {
        const { info, addr } = await orderFullByNo(ws, f.orderNo);
        if (!info) continue;
        const prods = (info.productInfoList || []).filter((p) => p.sku).map((p) => ({
          sku: p.sku, qty: Number(p.qty || p.platformQty) || 1, name: p.productName || '',
          platformSku: p.platformSku || '', platformProductName: p.platformProductName || '',
          platformQty: p.platformQty,
        }));
        if (!prods.length) continue;
        const payload = L.buildPayload(info, addr, prods);
        const wf = whOf(f.store);
        payload.sendWhCode = wf.wh;
        payload.logisticsChannelCode = wf.chCode;
        payload.logisticsChannelName = wf.chName;
        const c = carrierOf(carriers, f.po);
        if (c) payload.logisticsCarrier = c;
        await L.updateOrder(ws, payload);
        retryOk.push(f);
      } catch (e) {
        log('   ✗ %s 重试改单异常 %s', f.po, String(e.message || e).slice(0, 100));
      }
    }
    const res2 = await approveInBatches(ws, retryOk.map((f) => f.orderNo), log);
    for (const f of retryOk) {
      const r = res2.get(f.orderNo) || { ok: false, msg: '未返回' };
      f.retried = true; f.ok = !!r.ok; f.retrySuccessQty = r.succ;
      if (f.ok) { delete f.why; } else { f.why = 'approve 重试未通过：' + String(r.msg || '').slice(0, 200); }
    }
  }
  writeJson(opts.approveOut, out);
  log('[gw_approve] %d/%d 审核通过', out.filter((x) => x.ok).length, out.length);
  return 0;
}

/** gw_verify：回读状态并落 hx_oms_ship.json（report 用这个文件） */
async function stageVerify(ws, opts) {
  const plan = readJson(opts.planOut, { orders: [] }).orders || [];
  const wait = Number(opts.verifyWait || 90);
  const deadline = Date.now() + wait * 1000;
  let rows = [];
  // ★ 2026-11-23：orderNo 走缓存；没缓存的批量查一次
  const cache = loadOrderNoCache(opts);
  const miss = plan.filter((it) => !(cache[it.po] && cache[it.po].orderNo)).map((it) => it.po);
  if (miss.length) {
    log('[gw_verify] 批量查单 %d 个（缓存命中 %d）', miss.length, plan.length - miss.length);
    Object.assign(cache, await lookupOrderNos(ws, miss, log));
    saveOrderNoCache(opts, cache);
  }

  async function readOne(it) {
    try {
      const hit = cache[it.po];
      if (!hit || !hit.orderNo) return { po: it.po, store: it.store, status: null, why: 'OMS 查不到该单' };
      const info = await L.orderInfo(ws, hit.orderNo);
      if (!info) return { po: it.po, store: it.store, status: null, why: 'OMS 查不到该单' };
      return { po: it.po, store: it.store, orderNo: hit.orderNo,
               status: Number(info.status), statusName: info.statusName || '',
               track: info.logisticsTrackNo || info.logisticsTrackNos || '',
               cause: String(info.auditCause || info.exceptionCause || '').slice(0, 120) };
    } catch (e) {
      return { po: it.po, store: it.store, status: null, why: 'ERR ' + String(e.message || e).slice(0, 80) };
    }
  }

  for (;;) {
    // 并发 6 路回读（同一扇页，别把网关打挤）
    rows = new Array(plan.length);
    let vi = 0;
    async function vworker() {
      for (;;) {
        const k = vi++;
        if (k >= plan.length) return;
        rows[k] = await readOne(plan[k]);
      }
    }
    await Promise.all(Array.from({ length: Math.min(ASSIGN_CONCURRENCY, plan.length) }, vworker));
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
          : (Number(x.status) === 1 || x.statusName === '待获取平台面单')
            ? '待获取平台面单（等待中，OMS 约 40s 自动取回；隔一会儿重跑 --stage gw_verify 即可）'
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
  const a = { mode: '', apply: false, retryPending: true, noSkuChange: true };
  for (let i = 0; i < argv.length; i++) {
    const k = argv[i];
    if (!k.startsWith('--')) { if (!a.mode) a.mode = k; continue; }
    const key = k.slice(2);
    if (key === 'apply') { a.apply = true; continue; }
    if (key === 'no-retry-pending') { a.retryPending = false; continue; }
    if (key === 'no-sku-change') { a.noSkuChange = true; continue; }
    if (key === 'allow-sku-change') { a.noSkuChange = false; continue; }
    // 布尔开关：--query / --dry-query 之类不带值时不要吞掉下一个参数
    if (['query', 'q'].includes(key)) { a.query = 'true'; continue; }
    a[key.replace(/-([a-z])/g, (m, c) => c.toUpperCase())] = argv[i + 1];
    i++;
  }
  return a;
}

async function main() {
  const a = parseArgs(process.argv.slice(2));
  const mode = a.mode || (a.stage ? 'stage' : 'inspect');
  const opts = {
    store: a.store || '', split: a.split || '', posFile: a.posFile || '', pos: a.pos || '',
    out: a.out || '', apply: !!a.apply, retryPending: a.retryPending !== false,
    label: a.label || '', verifyWait: a.verifyWait || 90,
    noSkuChange: a.noSkuChange !== false,
    query: String(a.query || '') === 'true' || String(a.query || '') === '1',
    queryStoreCodes: a.queryStoreCodes || a.queryStores || '', queryPlatform: a.queryPlatform || 'TEMU',
    queryWh: a.queryWh || 'USW01', queryStatus: a.queryStatus, querySize: a.querySize,
  };
  if (opts.out) {
    opts.planOut = a.plan || path.join(opts.out, 'gw_plan.json');
    opts.carrierOut = a.carriers || path.join(opts.out, 'gw_carriers.json');
    opts.assignOut = path.join(opts.out, 'gw_assign.json');
    opts.approveOut = path.join(opts.out, 'gw_approve.json');
    opts.reportOut = a.report || path.join(opts.out, 'hx_oms_ship.json');
  }
  const stage = a.stage || '';
  const needsBrowser = !['gw_carriers'].includes(stage) && mode !== 'plan'
    && !(stage === 'gw_plan' && !opts.query);

  let ws = null;
  if (needsBrowser) {
    let att = await L.attach();
    if (!att) {
      // ★ 2026-11-24 自愈：定时任务无人值守时 OMS 窗口常常是关的（昨晚关窗后，今早
      //   三店全部 `FATAL 连不上 OMS 页面`）。这里自己把 OMS 店铺窗口打开再重试，
      //   而不是直接失败。窗口：TK-Savonire-YJF / storeId 27663658415319（LM739）。
      log('[attach] 连不上 → 自动打开 OMS 店铺窗口（TK-Savonire-YJF）并重试');
      const opened = autoOpenOms();
      if (opened) {
        for (let n = 0; n < 8 && !att; n++) {
          await L.sleep(6000);
          att = await L.attach({ tries: 1 });
          if (att) log('[attach] 第 %d 次重试连上了（port=%s）', n + 1, att.port);
        }
      }
      // ★ 2026-11-25 补第 2 级自愈：窗口「开着」但页面渲染进程卡死时，上面那条
      //   `store open` 只会回 `reused:true`、不会开新页 ⇒ 重试 8 次全部白等。
      //   实测 BD 当天连炸 3 轮就是这种形态（页面对任何 evaluate 都不响应）。
      //   这里先探活：有死页 → 关 target + 关窗口 + 重开整窗，再重试 attach。
      if (!att) {
        try {
          const revived = await reviveOmsWindow();
          if (revived) {
            log('[attach] 已重启 OMS 窗口 → 重开并重试');
            autoOpenOms();
            for (let n = 0; n < 10 && !att; n++) {
              await L.sleep(8000);
              att = await L.attach({ tries: 1 });
              if (att) log('[attach] 重启后第 %d 次连上了（port=%s）', n + 1, att.port);
            }
          }
        } catch (e) {
          log('[attach] 重启 OMS 窗口失败：%s', String(e && e.message || e).slice(0, 160));
        }
      }
    }
    if (!att) throw new Error('连不上 OMS 页面（紫鸟窗口里要开着 oms.xlwms.com 且已登录）');
    log('[attach] port=%s url=%s', att.port, att.url);
    await L.prepare(att.ws);
    ws = att.ws;
  }

  if (mode === 'inspect') return log(JSON.stringify(await inspect(ws, a.po), null, 1)) || 0;

  if (stage === 'gw_plan') return stagePlan(ws, opts);
  if (stage === 'gw_carriers') return stageCarriers(opts);
  if (stage === 'gw_assign') return stageAssign(ws, opts);
  if (stage === 'gw_approve') return stageApprove(ws, opts);
  if (stage === 'gw_verify') return stageVerify(ws, opts);
  if (stage === 'all') {
    await stagePlan(ws, opts); stageCarriers(opts);
    await stageAssign(ws, opts); await stageApprove(ws, opts); await stageVerify(ws, opts);
    return 0;
  }
  throw new Error('未知 stage：' + stage);
}

main().then((c) => process.exit(c || 0)).catch((e) => {
  console.error('[FATAL]', e && e.message ? e.message : e);
  process.exit(1);
});
