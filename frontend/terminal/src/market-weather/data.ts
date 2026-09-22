import { differencePoints, minuteChanges, type RawPoint } from "../../../trading-chart/src/difference";

export type Selection = { venue: string; day: string; token: string };
export type Contract = { venue: string; local_day: string; yes_token_id: string;
  title: string; lower: number | null; upper: number | null; active: boolean;
  observation_start: string | null; observation_end: string | null; close_time: string | null };
export type Catalog = { venue: string; discovery?: { status: string }; days: { day: string; contracts: Contract[];
  has_prices: boolean; status: string }[] };
export type WeatherPoint = { time: number; value: number; received_at: number | null;
  version: string; source: string; capture_id?: number; issued_at: number | null };
export type WeatherHistory = { venue: string; day: string; start: number; end: number;
  as_of: number; window_source: string; missing_sources: string[];
  sources: Record<string, WeatherPoint[]>;
  cli: { text: string; issued_at: string; received_at: number; finality: string }[] };
export type Quote = { time: number; received_at: number; bids: number[][]; asks: number[][];
  source?: string; seq?: number };
export type History = Selection & { points: Quote[]; next_before: number | null; reason: string | null };
export const names: Record<string, string> = { metar: "METAR", nws_observations: "NWS 站点观测",
  hourly_temp: "官方小时温度", hrrr: "HRRR", nws_forecast: "NWS 预报" };
export const nyTime = (time: number) => new Intl.DateTimeFormat("zh-CN", {
  timeZone: "America/New_York", month: "2-digit", day: "2-digit", hour: "2-digit",
  minute: "2-digit", hourCycle: "h23", timeZoneName: "short" }).format(new Date(time * 1000));
export const todayNY = () => new Intl.DateTimeFormat("en-CA", {
  timeZone: "America/New_York", year: "numeric", month: "2-digit", day: "2-digit",
}).format(new Date());
export const selectionKey = (s: Selection) => `${s.venue}/${s.day}/${s.token}`;

export function midpoint(q: Quote): number | null {
  const bid = q.bids[0]?.[0], ask = q.asks[0]?.[0];
  return Number.isFinite(bid) && Number.isFinite(ask) && bid >= 0 && ask <= 1 && bid < ask
    ? (bid + ask) * 50 : null; // percentage points, not a traded price
}

function raw(p: WeatherPoint, time: number): RawPoint {
  return { time, value: p.value, object_time: new Date(p.time * 1000).toISOString(),
    received_at: p.received_at == null ? undefined : new Date(p.received_at * 1000).toISOString(),
    captureId: String(p.capture_id ?? p.version), issuedAt: p.issued_at == null ? undefined
      : new Date(p.issued_at * 1000).toISOString(), source: names[p.source] };
}

export function weatherMinutes(data: WeatherHistory, source: string): RawPoint[] {
  const forecast = source === "hrrr" || source === "nws_forecast";
  const known = (data.sources[source] ?? []).filter(p => p.received_at != null)
    .sort((a, b) => a.received_at! - b.received_at!);
  const available: WeatherPoint[] = [];
  const result: RawPoint[] = [];
  let index = 0, previous: WeatherPoint | undefined;
  for (let t = Math.ceil(data.start / 60) * 60; t < Math.min(data.end, data.as_of); t += 60) {
    while (index < known.length && known[index].received_at! <= t) available.push(known[index++]);
    const eligible = available.filter(p => forecast
      ? p.time <= t && t < p.time + 3600 && p.issued_at != null && p.issued_at <= t
        && t - p.issued_at <= 21600
      : p.time <= t && t - p.time <= 5400);
    const p = eligible.sort((a, b) => forecast
      ? (b.issued_at! - a.issued_at!) || (b.received_at! - a.received_at!)
      : (b.time - a.time) || (b.received_at! - a.received_at!))[0];
    const point: RawPoint = p ? raw(p, t) : { time: t, value: null };
    if (p && forecast && previous) {
      const old = available.find(x => x.version === previous!.version && x.time === p.time);
      point.revisionDelta = old ? p.value - old.value : null;
      point.revisionPreviousValue = old?.value;
      point.previousCaptureId = old ? String(old.capture_id ?? old.version) : undefined;
    }
    result.push(point);
    previous = p;
  }
  return result;
}

export function priceMinutes(data: WeatherHistory, quotes: Quote[], token: string): RawPoint[] {
  const sorted = [...quotes].sort((a, b) => a.received_at - b.received_at);
  const result: RawPoint[] = [];
  let i = 0, last: Quote | undefined;
  for (let t = Math.ceil(data.start / 60) * 60; t < Math.min(data.end, data.as_of); t += 60) {
    while (i < sorted.length && sorted[i].received_at <= t) last = sorted[i++];
    result.push({ time: t, value: last && t - last.received_at <= 600 ? midpoint(last) : null,
      priceSource: "public_book_mid", source: "平台公开盘口中间价", binId: token,
      received_at: last ? new Date(last.received_at * 1000).toISOString() : undefined });
  }
  return result;
}

export const views = ["METAR − 预报", "官方小时温度 − 预报", "官方小时温度 − METAR",
  "价格变化 / 预报修订", "价格变化 / METAR 变化", "价格变化 / 官方小时温度变化"];
export function analysis(data: WeatherHistory, quotes: Quote[], token: string,
  forecast: string, view: number): { name: string; unit: string; points: RawPoint[] }[] {
  const metar = weatherMinutes(data, "metar"), hourly = weatherMinutes(data, "hourly_temp");
  const predicted = weatherMinutes(data, forecast);
  if (view < 3) {
    const [left, right] = [[metar, predicted], [hourly, predicted], [hourly, metar]][view];
    return [{ name: views[view], unit: "°F", points: differencePoints(left, right)
      .map(p => ({ ...p.left, time: p.time, value: p.value })) }];
  }
  const source = view === 3 ? "forecast" : view === 4 ? "metar" : "hourly_temp";
  return [{ name: "价格分钟变化", unit: "百分点", points: minuteChanges(
    priceMinutes(data, quotes, token), "price").map(p => ({ ...p.left, time: p.time, value: p.value })) },
  { name: view === 3 ? `${names[forecast]} 同有效时刻修订` : `${names[source]} 分钟变化`, unit: "°F",
    points: minuteChanges(view === 3 ? predicted : view === 4 ? metar : hourly, source)
      .map(p => ({ ...p.left, time: p.time, value: p.value })) }];
}

export function observationLine(points: WeatherPoint[], forecast: boolean): RawPoint[] {
  const byTime = new Map<number, WeatherPoint>();
  for (const p of points) {
    const old = byTime.get(p.time);
    if (!old || (p.received_at ?? 0) >= (old.received_at ?? 0)) byTime.set(p.time, p);
  }
  const sorted = [...byTime.values()].sort((a, b) => a.time - b.time);
  return sorted.flatMap((p, i) => {
    const last = sorted[i - 1];
    return last && p.time - last.time > (forecast ? 3600 : 5400)
      ? [{ time: last.time + (forecast ? 3600 : 5400), value: null }, raw(p, p.time)]
      : [raw(p, p.time)];
  });
}
