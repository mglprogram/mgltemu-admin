/**
 * 产品手册「成本」列加密器（本地明文 → 面板密文）。
 *
 * 为什么存在：用户 2026-11-07 要求成本「只有我能看到（加访问控制）」。
 *   面板是纯静态站（Cloudflare Pages，没有后端 / 没有账号体系），做不到服务端 ACL。
 *   所以走**客户端加密**：
 *     · 明文成本只存在本机 scripts/goods/_local/（gitignore，绝不上传 / 不入 GitHub）
 *     · 构建时读明文 → PBKDF2(口令, 60 万次) 派生 AES-256-GCM 密钥 → 加密
 *     · 线上只放密文 public/data/product-costs.json
 *     · 页面上出现「解锁成本」输入框，用户输口令，浏览器用 WebCrypto 本地解密
 *   也就是说：**拿到面板地址 / 下载到 JSON 也看不到成本**，没有口令解不开。
 *
 * 口令不写进任何构建产物、不进环境变量、不进 GitHub；只在本机明文源里。
 * 忘记口令：改 _local/product_manual_costs.json 的 costPassword 再重跑本脚本。
 *
 * 用法：node scripts/goods/encrypt_product_costs.mjs
 */
import { pbkdf2Sync, randomBytes, createCipheriv } from "node:crypto";
import { readFileSync, writeFileSync, mkdirSync, existsSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const ROOT = resolve(dirname(fileURLToPath(import.meta.url)), "../..");
const SRC = resolve(ROOT, "scripts/goods/_local/product_manual_costs.json");
const OUT = resolve(ROOT, "temu-admin/public/data/product-costs.json");

const ITERATIONS = 600_000; // OWASP 2023 PBKDF2-HMAC-SHA256 建议值
const SALT_LEN = 16;
const IV_LEN = 12;

if (!existsSync(SRC)) {
  console.error(`[err] 找不到本地明文源：${SRC}`);
  console.error("      成本是本地-only 数据，clone 下来的仓库里没有；请在本机补上。");
  process.exit(2);
}

const cfg = JSON.parse(readFileSync(SRC, "utf-8"));
if (!cfg.costPassword || !cfg.prices || typeof cfg.prices !== "object") {
  console.error("[err] 明文源缺 costPassword 或 prices");
  process.exit(2);
}

const salt = randomBytes(SALT_LEN);
const iv = randomBytes(IV_LEN);
const key = pbkdf2Sync(cfg.costPassword, salt, ITERATIONS, 32, "sha256");
const cipher = createCipheriv("aes-256-gcm", key, iv);
const plaintext = Buffer.from(JSON.stringify(cfg.prices), "utf-8");
const body = Buffer.concat([cipher.update(plaintext), cipher.final()]);
const tag = cipher.getAuthTag();

const payload = {
  v: 1,
  alg: "AES-256-GCM",
  kdf: "PBKDF2-SHA256",
  iterations: ITERATIONS,
  salt: salt.toString("base64"),
  iv: iv.toString("base64"),
  tag: tag.toString("base64"),
  data: body.toString("base64"),
  /** 只暴露「哪些款号有成本」，用于未解锁时显示锁标记；金额一个字都不漏 */
  styles: Object.keys(cfg.prices).sort(),
  // ★ 2026-11-09：改成本地时区 "YYYY-MM-DD HH:mm:ss"。原先 toISOString() 是 UTC，
  //   面板显示的生成时间会差 8 小时（如北京 14:07 显示成 06:07）。
  generatedAt: (() => {
    const d = new Date();
    const p = (n) => String(n).padStart(2, "0");
    return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())} ` +
           `${p(d.getHours())}:${p(d.getMinutes())}:${p(d.getSeconds())}`;
  })(),
};

mkdirSync(dirname(OUT), { recursive: true });
writeFileSync(OUT, JSON.stringify(payload, null, 1), "utf-8");
console.log(
  `[ok] ${OUT} — ${payload.styles.length} 个款号，${Buffer.byteLength(JSON.stringify(payload))} 字节密文`
);
