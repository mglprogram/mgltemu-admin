/**
 * 美国各州「当天天气」（地图 tooltip 用）
 *
 * 用户 2026-10-14 要求：鼠标悬浮到某个州时，顺便显示该州当天的天气。
 *
 * 数据源：**Open-Meteo**（https://open-meteo.com）—— 免费、**不需要 API Key**、
 * 响应头带 `access-control-allow-origin: *`，所以纯静态前端可以**浏览器直连**
 * （本项目 standalone 模式没有后端，这一步是硬约束）。
 *
 * 关键做法：
 *  1. **一次请求拿全部 52 个州**：Open-Meteo 支持 `latitude=a,b,c&longitude=x,y,z`
 *     逗号批量，返回一个数组（实测 52 点 35KB / ~0.3s）。这样 tooltip 的 formatter
 *     就能**同步**取数，hover 不卡、不用等网络。
 *  2. `timezone=auto` 是**按坐标逐个解析**的（实测 Houston→America/Chicago、
 *     NYC→America/New_York、Honolulu→Pacific/Honolulu），所以「今日最高/最低」
 *     用的是各州当地时间，不会串。
 *  3. 单位用**摄氏度**（`temperature_unit=celsius`）+ km/h —— 用户 2026-10-14 要求
 *     「改成国际通用温度」，虽然站点是美国本土店，但看板的人在国内。
 *  4. 每 15 分钟静默刷新一次；失败**不影响地图**，只是 tooltip 少一行。
 *
 * ⚠ 每个州取的是**该州代表城市**（大城市）的天气，不是整个州的平均 ——
 *   tooltip 上会把城市名带出来，避免误读。
 */
import { ref } from "vue";

export interface StateWeather {
  /** 代表城市名 */
  city: string;
  /** 当前气温 °C */
  temp: number;
  /** WMO 天气码 */
  code: number;
  /** 今日最高 / 最低 °C */
  max: number;
  min: number;
  /** 中文描述 */
  text: string;
  /** emoji 图标 */
  icon: string;
  /** 取数时间戳（ms） */
  at: number;
}

/**
 * 州全名 → [代表城市, 纬度, 经度]。
 * 城市选的是该州**最知名的大城市**（不一定是首府），跟 tooltip 里显示的城市名一致。
 */
const STATE_CITY: Record<string, [string, number, number]> = {
  Alabama: ["Birmingham", 33.5186, -86.8104],
  Alaska: ["Anchorage", 61.2181, -149.9003],
  Arizona: ["Phoenix", 33.4484, -112.074],
  Arkansas: ["Little Rock", 34.7465, -92.2896],
  California: ["Los Angeles", 34.0522, -118.2437],
  Colorado: ["Denver", 39.7392, -104.9903],
  Connecticut: ["Hartford", 41.7658, -72.6734],
  Delaware: ["Wilmington", 39.7459, -75.5466],
  "District of Columbia": ["Washington", 38.9072, -77.0369],
  Florida: ["Miami", 25.7617, -80.1918],
  Georgia: ["Atlanta", 33.749, -84.388],
  Hawaii: ["Honolulu", 21.3069, -157.8583],
  Idaho: ["Boise", 43.615, -116.2023],
  Illinois: ["Chicago", 41.8781, -87.6298],
  Indiana: ["Indianapolis", 39.7684, -86.1581],
  Iowa: ["Des Moines", 41.5868, -93.625],
  Kansas: ["Wichita", 37.6872, -97.3301],
  Kentucky: ["Louisville", 38.2527, -85.7585],
  Louisiana: ["New Orleans", 29.9511, -90.0715],
  Maine: ["Portland", 43.6591, -70.2568],
  Maryland: ["Baltimore", 39.2904, -76.6122],
  Massachusetts: ["Boston", 42.3601, -71.0589],
  Michigan: ["Detroit", 42.3314, -83.0458],
  Minnesota: ["Minneapolis", 44.9778, -93.265],
  Mississippi: ["Jackson", 32.2988, -90.1848],
  Missouri: ["Kansas City", 39.0997, -94.5786],
  Montana: ["Billings", 45.7833, -108.5007],
  Nebraska: ["Omaha", 41.2565, -95.9345],
  Nevada: ["Las Vegas", 36.1699, -115.1398],
  "New Hampshire": ["Manchester", 42.9956, -71.4548],
  "New Jersey": ["Newark", 40.7357, -74.1724],
  "New Mexico": ["Albuquerque", 35.0844, -106.6504],
  "New York": ["New York", 40.7128, -74.006],
  "North Carolina": ["Charlotte", 35.2271, -80.8431],
  "North Dakota": ["Fargo", 46.8772, -96.7898],
  Ohio: ["Columbus", 39.9612, -82.9988],
  Oklahoma: ["Oklahoma City", 35.4676, -97.5164],
  Oregon: ["Portland", 45.5152, -122.6784],
  Pennsylvania: ["Philadelphia", 39.9526, -75.1652],
  "Puerto Rico": ["San Juan", 18.4655, -66.1057],
  "Rhode Island": ["Providence", 41.824, -71.4128],
  "South Carolina": ["Columbia", 34.0007, -81.0348],
  "South Dakota": ["Sioux Falls", 43.546, -96.7313],
  Tennessee: ["Nashville", 36.1627, -86.7816],
  Texas: ["Houston", 29.7604, -95.3698],
  Utah: ["Salt Lake City", 40.7608, -111.891],
  Vermont: ["Burlington", 44.4759, -73.2121],
  Virginia: ["Virginia Beach", 36.8529, -75.978],
  Washington: ["Seattle", 47.6062, -122.3321],
  "West Virginia": ["Charleston", 38.3498, -81.6326],
  Wisconsin: ["Milwaukee", 43.0389, -87.9065],
  Wyoming: ["Cheyenne", 41.14, -104.8202],
};

/** WMO weather code → [中文, emoji] */
const WMO: Record<number, [string, string]> = {
  0: ["晴", "☀️"],
  1: ["大致晴朗", "🌤"],
  2: ["局部多云", "⛅"],
  3: ["阴", "☁️"],
  45: ["有雾", "🌫"],
  48: ["雾凇", "🌫"],
  51: ["毛毛雨", "🌦"],
  53: ["毛毛雨", "🌦"],
  55: ["毛毛雨", "🌦"],
  56: ["冻毛毛雨", "🌧"],
  57: ["冻毛毛雨", "🌧"],
  61: ["小雨", "🌧"],
  63: ["中雨", "🌧"],
  65: ["大雨", "🌧"],
  66: ["冻雨", "🌧"],
  67: ["冻雨", "🌧"],
  71: ["小雪", "🌨"],
  73: ["中雪", "🌨"],
  75: ["大雪", "❄️"],
  77: ["米雪", "🌨"],
  80: ["阵雨", "🌦"],
  81: ["阵雨", "🌧"],
  82: ["强阵雨", "⛈"],
  85: ["阵雪", "🌨"],
  86: ["强阵雪", "❄️"],
  95: ["雷阵雨", "⛈"],
  96: ["雷阵雨伴冰雹", "⛈"],
  99: ["强雷暴伴冰雹", "⛈"],
};

const API = "https://api.open-meteo.com/v1/forecast";
/** 刷新间隔（天气没必要更勤） */
const REFRESH_MS = 15 * 60 * 1000;
/** 拉取超时：超时就当失败，绝不让它拖住页面 */
const TIMEOUT_MS = 12000;

const weather = ref<Record<string, StateWeather>>({});
const failed = ref(false);
const loading = ref(false);
let started = false;
let timer: ReturnType<typeof setInterval> | undefined;
const listeners = new Set<() => void>();

/** 天气数据变化时回调（图表需要重设 option 才能让 tooltip 用上新数据） */
export function onWeatherUpdate(fn: () => void): () => void {
  listeners.add(fn);
  return () => listeners.delete(fn);
}

async function fetchAll(): Promise<void> {
  const names = Object.keys(STATE_CITY);
  if (!names.length) return;
  loading.value = true;
  const lat = names.map((n) => STATE_CITY[n][1].toFixed(3)).join(",");
  const lon = names.map((n) => STATE_CITY[n][2].toFixed(3)).join(",");
  const url =
    `${API}?latitude=${lat}&longitude=${lon}` +
    "&current=temperature_2m,weather_code" +
    "&daily=temperature_2m_max,temperature_2m_min" +
    "&temperature_unit=celsius&wind_speed_unit=kmh&timezone=auto&forecast_days=1";
  const ctl = new AbortController();
  const t = setTimeout(() => ctl.abort(), TIMEOUT_MS);
  try {
    const res = await fetch(url, { signal: ctl.signal, cache: "no-store" });
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const arr = (await res.json()) as Array<{
      current?: { temperature_2m?: number; weather_code?: number };
      daily?: { temperature_2m_max?: number[]; temperature_2m_min?: number[] };
    }>;
    if (!Array.isArray(arr) || arr.length !== names.length) {
      throw new Error(`返回条数不符：${Array.isArray(arr) ? arr.length : typeof arr} ≠ ${names.length}`);
    }
    const next: Record<string, StateWeather> = {};
    arr.forEach((x, i) => {
      const name = names[i];
      const code = Number(x.current?.weather_code ?? -1);
      const [text, icon] = WMO[code] || ["—", "🌡"];
      next[name] = {
        city: STATE_CITY[name][0],
        temp: Math.round(Number(x.current?.temperature_2m ?? 0)),
        code,
        max: Math.round(Number(x.daily?.temperature_2m_max?.[0] ?? 0)),
        min: Math.round(Number(x.daily?.temperature_2m_min?.[0] ?? 0)),
        text,
        icon,
        at: Date.now(),
      };
    });
    weather.value = next;
    failed.value = false;
  } catch (e) {
    // 失败只标记，不抛：地图和榜单不能因为天气挂了就白屏
    failed.value = true;
    console.warn("[us-weather] 取天气失败（不影响地图）：", e);
  } finally {
    clearTimeout(t);
    loading.value = false;
    listeners.forEach((fn) => fn());
  }
}

/** 幂等启动：只在第一次调用时真的发请求 + 起定时刷新 */
export function startUsWeather(): void {
  if (started) return;
  started = true;
  void fetchAll();
  timer = setInterval(() => void fetchAll(), REFRESH_MS);
}

export function stopUsWeather(): void {
  if (timer) clearInterval(timer);
  timer = undefined;
  started = false;
}

/** 取某州天气（同步；没数据返回 undefined） */
export function weatherOf(state: string): StateWeather | undefined {
  return weather.value[state];
}

/** 天气是否已就绪 / 是否拉取失败（失败时 tooltip 会明说「暂不可用」） */
export function weatherState(): { ready: boolean; failed: boolean; loading: boolean } {
  return {
    ready: Object.keys(weather.value).length > 0,
    failed: failed.value,
    loading: loading.value,
  };
}

/** tooltip 里那一行：`🌤 Houston 26°C 晴 · 今日 23~32°C`（摄氏度，国际通用） */
export function weatherLine(state: string): string {
  const w = weatherOf(state);
  if (w) {
    return `${w.icon} ${w.city} ${w.temp}°C ${w.text} · 今日 ${w.min}~${w.max}°C`;
  }
  const st = weatherState();
  if (st.failed) return "天气：暂不可用";
  if (st.loading || !st.ready) return "天气：加载中…";
  return "";
}
