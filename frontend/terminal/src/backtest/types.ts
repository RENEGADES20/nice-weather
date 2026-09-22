export type Venue = "kalshi" | "poly_us";
export type StrategyEvent = {
  id: string; group_id?: string; time: number; time_basis?: string;
  venue?: string | null; day?: string | null; token?: string | null;
  strategy?: string | null; stage: string; reason?: string | null;
  price?: number | null; quantity?: number | null; probability?: number | null;
  p_end?: number | null; fee?: number | null; cost?: number | null; proceeds?: number | null;
  order_id?: string; side?: string;
};
export type Point = { time: number; value: number | null };
export type AccountPoint = {
  time: number; cash?: number | null; market_value?: number | null;
  equity?: number | null; total_pnl?: number | null; fees?: number | null;
  settlement?: string;
};
export type RunConfig = {
  venue: Venue; start: number; end: number; start_day?: string; end_day?: string;
  strategy?: string; strategy_id?: string; cash?: number;
  simulation?: { estimated_fee_rate: number; slippage_pp: number };
  execution?: string;
};
export type Run = {
  run_id: string; status: string; error?: string | null; updated: number;
  config: RunConfig;
};
export type RunDetail = Run & {
  snapshot: Partial<AccountPoint> & { ts?: number; fills?: unknown[]; prediction_events?: number;
    replay_audit?: { inputs: Record<string, { count: number; first_received: number;
      last_received: number; max_gap_seconds: number }>;
      decisions: Record<string, Record<string, number>>; scan_complete: boolean } };
  curve: AccountPoint[]; events: StrategyEvent[]; history_complete: boolean;
  settlement?: string | null;
};
export type Market = {
  venue: Venue; day: string; contracts: { yes_token_id: string; title: string }[];
  observation_start?: string | null; observation_end?: string | null; reason?: string | null;
};
export type History = { venue: Venue; day: string; token: string;
  points: { seq: number; time: number; received_at?: number; bids: number[][];
    asks: number[][]; valid?: boolean; complete?: boolean; gap?: boolean }[];
  next_before?: number | null; reason?: string | null;
};
export const stages: Record<string, string> = {
  warning: "天气预警", trigger: "天气触发", candidate: "交易候选", order: "下单",
  fill: "成交", rejection: "执行拒绝", diagnostic: "未触发诊断",
};
export const statusText: Record<string, string> = {
  queued: "排队中", starting: "启动中", running: "运行中", completed: "已完成",
  failed: "失败", rejected: "请求被拒绝", "no-data": "数据不足",
};
export const money = (n?: number | null) => n == null ? "未记录" :
  new Intl.NumberFormat("zh-CN", { style: "currency", currency: "USD" }).format(n);
export const numeric = (n?: number | null) => n == null ? "未记录" : String(n);
export const percent = (n?: number | null) => n == null ? "未记录" : `${(n * 100).toFixed(2)}%`;
export const nyTime = (n: number) => new Date(n * 1000).toLocaleString("zh-CN", {
  timeZone: "America/New_York", hour12: false,
});
// Market date is a contract key. This fallback only labels legacy fixed-EST KNYC runs.
export const climateDay = (n: number) => new Date((n - 5 * 3600) * 1000).toISOString().slice(0, 10);
export function daysBetween(start: string, end: string) {
  const result: string[] = [];
  for (let n = Date.parse(start); n <= Date.parse(end); n += 86400000)
    result.push(new Date(n).toISOString().slice(0, 10));
  return result;
}
export async function read<T>(base: string, path: string, signal?: AbortSignal): Promise<T> {
  const response = await fetch(`${base}/${path}`, { credentials: "same-origin", signal });
  if (!response.ok) throw new Error(`读取失败 (${response.status})`);
  return response.json();
}
