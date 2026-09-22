import React, { useEffect, useRef } from "react";
import { type Catalog, type Selection, todayNY } from "./data";

export function MarketSelector({ catalog, value, onChange, loading = false, error = "" }: {
  catalog?: Catalog; value: Selection; onChange: (next: Selection) => void;
  loading?: boolean; error?: string;
}) {
  const remembered = useRef(new Map<string, string>());
  const current = catalog?.venue === value.venue ? catalog : undefined;
  const day = value.day || (current?.days.some(d => d.day === todayNY()) ? todayNY()
    : current?.days.at(-1)?.day ?? "");
  const contracts = [...(current?.days.find(d => d.day === day)?.contracts ?? [])]
    .sort((a, b) => (a.lower ?? -Infinity) - (b.lower ?? -Infinity));
  const key = `${value.venue}/${day}`;
  const token = contracts.some(c => c.yes_token_id === value.token) ? value.token
    : contracts.find(c => c.yes_token_id === remembered.current.get(key))?.yes_token_id
      ?? contracts[0]?.yes_token_id ?? "";
  useEffect(() => {
    if (!current || loading) return;
    if (token) remembered.current.set(key, token);
    if (value.day !== day || value.token !== token) onChange({ ...value, day, token });
  }, [current, loading, day, token, key, value.day, value.token]);
  return <section className="mw-selector" aria-label="市场选择">
    <label>平台 <select aria-label="平台" value={value.venue} onChange={e =>
      onChange({ venue: e.target.value, day: value.day, token: "" })}>
      <option value="kalshi">Kalshi</option><option value="poly_us">Poly US</option>
    </select></label>
    <label>市场日 <input aria-label="市场日" type="date" value={day} onChange={e =>
      onChange({ ...value, day: e.target.value, token: "" })} /></label>
    <label>已采集 / 已挂牌 <select aria-label="已挂牌日期" value={day} onChange={e =>
      onChange({ ...value, day: e.target.value, token: "" })}>
      {!current?.days.some(d => d.day === day) && <option value={day}>{day || "暂无目录"}</option>}
      {current?.days.map(d => <option key={d.day} value={d.day}>{d.day} · {d.has_prices ? "有历史行情" : "行情缺失"}</option>)}
    </select></label>
    <label>温度档位 <select aria-label="温度档位" value={token} disabled={!contracts.length}
      onChange={e => { remembered.current.set(key, e.target.value);
        onChange({ ...value, day, token: e.target.value }); }}>
      {!contracts.length && <option value="">无合约</option>}
      {contracts.map(c => <option key={c.yes_token_id} value={c.yes_token_id}>{c.title}</option>)}
    </select></label>
    {loading && <span role="status">读取市场目录…</span>}
    {error && <span role="alert">{error}</span>}
    {current?.discovery?.status === "unavailable" &&
      <span role="status">挂牌目录刷新失败，保留已采集日期。</span>}
    {current && !loading && !contracts.length && <span>所选日期未采集合约，不切换日期。</span>}
  </section>;
}
