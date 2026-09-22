import { useEffect, useState } from "react";
import "./live.css";

export type LiveConfig = {
  revision: number; binding: string | null; manual_enabled: boolean;
  strategies: Record<string, boolean>; whitelist: string[];
  order_limit: string; position_limit: string; daily_loss_limit: string;
};
export type LiveSnapshot = {
  binding: string; authenticated: boolean; reconciled: boolean;
  received_at: number; reconcile_reason?: string; config: LiveConfig;
  funds: Record<string, unknown>; budget: Record<string, string>;
  availability: Record<string, { allowed: boolean; reason?: string }>;
  market_rules: Record<string, { parse_status: string; active: boolean; accepting_orders: boolean;
    fee_known: boolean; tick_size: string; quantity_step: string; minimum_order_size: number }>;
  orders: Array<{ id: string; market: string; owner: string; side: string; outcome: string;
    quantity: string; filled: string; price: string; status: string; terminal: boolean }>;
  fills: Array<{ trade_id: string; order_id: string; quantity: string; yes_price: string; fee: string }>;
  positions: Array<{ market: string; net: string; cost: string; realized_pnl: string }>;
  external_orders?: unknown; external_positions?: unknown;
};
export type LiveSend = (kind: string, payload: Record<string, unknown>) => Promise<void>;
type Props = { snapshot?: LiveSnapshot; send: LiveSend; pending: boolean; venue: string };

export const reasonText = (reason?: string) => ({
  ACCOUNT_NOT_CONNECTED: "账户尚未连接", ACCOUNT_STALE: "账户数据已过期",
  ACCOUNT_NOT_BOUND: "请绑定已认证账户", TRADING_NOT_ENABLED: "尚未启用交易",
  PRIVATE_STREAM_DISCONNECTED: "私有订单流断开，正在恢复",
  FILL_HISTORY_INCOMPLETE: "成交历史不完整，需要对账", UNKNOWN_SUBMISSION: "订单提交结果未知",
  MARKET_NOT_WHITELISTED: "该市场未加入白名单", MARKET_RULES_AMBIGUOUS: "该市场规则尚有歧义",
  INSUFFICIENT_AVAILABLE_FUNDS: "可用资金不足", POSITION_HISTORY_MISMATCH: "持仓与成交历史不一致",
  INSUFFICIENT_SHARD_FUNDS: "所选市场分区可用资金不足", MARKET_SHARD_BALANCE_UNKNOWN: "市场分区资金未知",
}[reason ?? ""] ?? reason ?? "可用");

export function LiveAccountPanel({ snapshot: s, send, pending, venue }: Props) {
  const [draft, setDraft] = useState<LiveConfig | undefined>(s?.config);
  const [editing, setEditing] = useState(false);
  useEffect(() => { setDraft(s?.config); setEditing(false); }, [venue]);
  useEffect(() => { if (!editing) setDraft(s?.config); }, [s?.config, editing]);
  if (!s || !draft) return <section className="nw-live" role="status">Live 账户尚未启动</section>;
  const stale = Date.now() / 1000 - s.received_at > 10;
  const change = (value: Partial<LiveConfig>) => { setEditing(true); setDraft({ ...draft, ...value }); };
  const money = (v: unknown) => v === null || v === undefined ? "未知" : `$${String(v)}`;
  const rawOrders = s.external_orders as { orders?: Record<string, unknown>[] } | undefined;
  const platformOrders = (Array.isArray(rawOrders) ? rawOrders : rawOrders?.orders ?? []) as Record<string, unknown>[];
  const platformPositions = (Array.isArray(s.external_positions) ? s.external_positions : []) as Record<string, unknown>[];
  return <section className="nw-live" aria-label="实盘账户">
    <h2>{venue === "kalshi" ? "Kalshi" : "Poly US"} · Live</h2>
    <p role="status">{s.authenticated ? "已认证" : "读取失败"} · {stale ? "数据过期" : "数据有效"}
      {s.received_at > 0 && ` · ${new Date(s.received_at * 1000).toLocaleString()}`}
      {!s.reconciled && ` · ${reasonText(s.reconcile_reason)}`}</p>
    <dl className="nw-live-funds">
      {[ ["现金", "cash"], ["可用交易资金", "available"], ["平台资金预留", "reserved"],
        ["挂单名义金额", "order_notional"], ["持仓估值", "position_value"] ].map(([label, key]) =>
        <div key={key}><dt>{label}</dt><dd>{money(s.funds[key])}</dd></div>)}
    </dl>
    <p>{String(s.funds.cash_basis ?? "")}；{String(s.funds.available_basis ?? "")}</p>
    <p>{String(s.funds.reservation_basis ?? "")}</p>
    {Boolean(s.funds.partitions) && <p>分区可用资金：{Object.entries(s.funds.partitions as Record<string,string>)
      .map(([id, value]) => `${id}: $${value}`).join(" · ")}。下单使用市场所在分区的资金。</p>}
    <p>累计预算 $5 · 已支出 {money(s.budget.spent)} · 项目预留 {money(s.budget.reserved)}
      · 剩余 {money(s.budget.remaining)}。卖出回款不恢复预算。</p>
    <details><summary>账户绑定与风险配置</summary>
      <form onSubmit={async e => { e.preventDefault(); await send("configure", draft); setEditing(false); }}>
        <label><input type="checkbox" checked={draft.binding === s.binding}
          onChange={e => change({ binding: e.target.checked ? s.binding : null })} />绑定当前已认证账户</label>
        <label><input type="checkbox" checked={draft.manual_enabled}
          onChange={e => change({ manual_enabled: e.target.checked })} />启用人工 Live</label>
        <label>市场白名单（每行一个）<textarea value={draft.whitelist.join("\n")}
          onChange={e => change({ whitelist: e.target.value.split("\n").filter(Boolean) })} /></label>
        {([ ["order_limit", "单笔上限"], ["position_limit", "持仓成本上限"],
          ["daily_loss_limit", "当日已实现亏损上限"] ] as const).map(([key, label]) =>
          <label key={key}>{label}（USD）<input type="number" min="0.01" max="5" step="0.01"
            value={draft[key]} onChange={e => change({ [key]: e.target.value })} required /></label>)}
        <button disabled={pending}>保存配置</button>
      </form>
    </details>
    <div className="nw-live-actions">
      <button disabled={pending} onClick={() => send("reconcile", {})}>刷新并对账</button>
      {Object.entries(s.config.strategies).map(([id, enabled]) => <button key={id} disabled={pending}
        onClick={() => send(enabled ? "stop" : "start", { strategies: [id], revision: s.config.revision })}>
        {enabled ? "停止并撤单" : "启用"} {id}</button>)}
    </div>
    <h3>订单</h3>
    <table><thead><tr><th>市场／归属</th><th>方向</th><th>限价</th><th>成交／数量</th><th>状态</th><th>操作</th></tr></thead>
      <tbody>{s.orders.map(o => <tr key={o.id}><td>{o.market} · {o.owner}</td><td>{o.side} {o.outcome}</td>
        <td>{o.price}</td><td>{o.filled} / {o.quantity}</td><td>{o.status}</td><td>
          {!o.terminal && <button disabled={pending || !s.availability.cancel?.allowed}
            onClick={() => send("cancel", { order_id: o.id })}>撤单</button>}</td></tr>)}</tbody></table>
    {!s.orders.length && <p>暂无本终端订单</p>}
    <h3>持仓</h3>
    {s.positions.map(p => <p key={p.market}>{p.market} · 净 YES {p.net} · 成本 {money(p.cost)}
      · 已实现 PnL {money(p.realized_pnl)}</p>)}
    {!s.positions.length && <p>暂无已对账持仓</p>}
    <h3>成交</h3>
    {s.fills.map(f => <p key={f.trade_id}>{f.trade_id} · {f.quantity} 份 · YES 价格 {f.yes_price}
      · 费用 {money(f.fee)}</p>)}
    <h3>平台账户订单（含外部历史）</h3>
    <table><thead><tr><th>市场</th><th>订单 ID</th><th>状态</th><th>成交数量</th></tr></thead>
      <tbody>{platformOrders.map((o, i) => <tr key={String(o.order_id ?? o.id ?? i)}>
        <td>{String(o.ticker ?? o.marketSlug ?? "未知")}</td><td>{String(o.order_id ?? o.id ?? "未知")}</td>
        <td>{String(o.status ?? o.state ?? "未知")}</td>
        <td>{String(o.fill_count_fp ?? o.cumQuantity ?? "未知")}</td></tr>)}</tbody></table>
    {!platformOrders.length && <p>平台订单列表为空</p>}
    <h3>平台账户持仓</h3>
    {platformPositions.map((p, i) => <p key={i}>{String(p.ticker ?? p.marketSlug ?? "未知市场")}
      · 净数量 {String(p.position_fp ?? p.netPositionDecimal ?? p.netPosition ?? "未知")}</p>)}
    {!platformPositions.length && <p>平台持仓列表为空</p>}
    <details><summary>平台订单与持仓完整字段</summary>
      <pre>{JSON.stringify({ orders: s.external_orders, positions: s.external_positions }, null, 2)}</pre>
    </details>
  </section>;
}
