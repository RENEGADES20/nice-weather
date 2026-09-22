import { useState } from "react";
import { reasonText, type LiveSend, type LiveSnapshot } from "./LiveAccountPanel";

export function LiveOrderTicket({ snapshot, market, send, pending }: {
  snapshot?: LiveSnapshot; market: string; send: LiveSend; pending: boolean;
}) {
  const [outcome, setOutcome] = useState("YES");
  const [side, setSide] = useState("BUY");
  const [price, setPrice] = useState("0.40");
  const [quantity, setQuantity] = useState("1");
  const [tif, setTif] = useState("IOC");
  const ready = snapshot?.availability.order;
  const stale = !snapshot || Date.now() / 1000 - snapshot.received_at > 10;
  const listed = snapshot?.config.whitelist.includes(market);
  const rules = snapshot?.market_rules?.[market];
  const marketReady = rules?.parse_status === "parsed" && rules.active && rules.accepting_orders && rules.fee_known;
  return <section className="nw-live" aria-label="实盘票据"><h2>人工 Live 订单</h2><p>{market}</p>
    <form onSubmit={e => { e.preventDefault(); void send("order", { market, outcome, side, price, quantity, tif }); }}>
      <label>结果<select value={outcome} onChange={e => setOutcome(e.target.value)}><option>YES</option><option>NO</option></select></label>
      <label>方向<select value={side} onChange={e => setSide(e.target.value)}><option value="BUY">买入</option><option value="SELL">卖出</option></select></label>
      <label>限价（USD）<input type="number" min={rules?.tick_size ?? "0.0001"} max="1" step={rules?.tick_size ?? "any"} required value={price} onChange={e => setPrice(e.target.value)} /></label>
      <label>数量<input type="number" min={rules?.minimum_order_size ?? "0.01"} step={rules?.quantity_step ?? "any"} required value={quantity} onChange={e => setQuantity(e.target.value)} /></label>
      <label>有效方式<select value={tif} onChange={e => setTif(e.target.value)}><option>IOC</option><option>GTC</option><option>FOK</option></select></label>
      <p role="status">{stale ? "账户数据过期" : !listed ? "该市场未加入白名单" : !marketReady ? "市场未开放或规则／费用尚未核实" : reasonText(ready?.reason)}</p>
      <button disabled={pending || stale || !listed || !marketReady || !ready?.allowed}>提交 Live 订单</button>
    </form>
  </section>;
}
