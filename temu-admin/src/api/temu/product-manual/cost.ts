/**
 * 产品手册「成本」密文读取 + 浏览器本地解密。
 *
 * 只做客户端解密：线上 JSON 里只有 AES-256-GCM 密文 + PBKDF2 参数 + 有成本的款号列表，
 * 没有任何金额明文。用户输口令后由 WebCrypto 在浏览器里派生密钥并解密，
 * 口令和明文都**不出浏览器**。没有口令 / 口令错 → 解密抛错，页面不渲染金额。
 *
 * 密文由 scripts/goods/encrypt_product_costs.mjs 生成（Node crypto，同参数）。
 */
export interface ProductCostPayload {
  v: number;
  alg: string;
  kdf: string;
  iterations: number;
  salt: string;
  iv: string;
  tag: string;
  data: string;
  /** 有成本的款号列表（仅用于未解锁时标锁，不含金额） */
  styles: string[];
  generatedAt: string;
}

/** 款号 → { bw: 黑白价, color: 彩色价 }，缺的颜色为 undefined */
export type ProductCosts = Record<string, { bw?: number; color?: number }>;

const DATA_BASE = `${import.meta.env.BASE_URL}data`;

function b64(s: string): Uint8Array<ArrayBuffer> {
  const bin = atob(s);
  const out = new Uint8Array(bin.length);
  for (let i = 0; i < bin.length; i += 1) out[i] = bin.charCodeAt(i);
  return out;
}

export async function fetchCostPayload(): Promise<ProductCostPayload> {
  const res = await fetch(`${DATA_BASE}/product-costs.json`, { cache: "no-cache" });
  if (!res.ok) throw new Error(`加载成本数据失败：HTTP ${res.status}`);
  return (await res.json()) as ProductCostPayload;
}

/**
 * 用口令解密成本密文。口令错误 / 数据被篡改 → GCM 校验失败抛错。
 */
export async function decryptCosts(payload: ProductCostPayload, password: string): Promise<ProductCosts> {
  if (payload.alg !== "AES-256-GCM" || payload.kdf !== "PBKDF2-SHA256") {
    throw new Error("成本数据格式不支持");
  }
  const enc = new TextEncoder();
  const keyMaterial = await crypto.subtle.importKey(
    "raw", enc.encode(password) as BufferSource, "PBKDF2", false, ["deriveKey"],
  );
  const key = await crypto.subtle.deriveKey(
    { name: "PBKDF2", salt: b64(payload.salt), iterations: payload.iterations, hash: "SHA-256" },
    keyMaterial,
    { name: "AES-GCM", length: 256 },
    false,
    ["decrypt"],
  );
  // WebCrypto 的 AES-GCM 要求密文与 16 字节 tag 拼在一起（tag 在末尾）
  const body = b64(payload.data);
  const tag = b64(payload.tag);
  const ct = new Uint8Array(new ArrayBuffer(body.length + tag.length));
  ct.set(body, 0);
  ct.set(tag, body.length);
  const plain = await crypto.subtle.decrypt(
    { name: "AES-GCM", iv: b64(payload.iv) }, key, ct,
  );
  return JSON.parse(new TextDecoder().decode(plain)) as ProductCosts;
}
