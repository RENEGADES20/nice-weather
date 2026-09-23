import { useEffect, useLayoutEffect, useRef, useState } from "react";

export type PaperMarket = {
  venue: string; local_day: string; yes_token_id: string; no_token_id: string;
  label: string; tick_size: number | string; quantity_step?: string;
  minimum_order_size: number;
};
export type Simulation = { estimated_fee_rate: number; slippage_pp: number };
export type PaperCommand = {
  request_id: string; venue: string; mode: "sandbox"; kind: string;
  payload: Record<string, unknown>;
};
type Preview = {
  available: boolean; reason?: string; estimated_fee?: number; fee_estimated?: boolean;
  expected_status?: string; selected_price?: {
    price: number; price_source: string; age_seconds: number; received_at: number | null;
  };
};
type Props = {
  market: PaperMarket | null;
  accountRevision: string;
  simulation: Simulation;
  blocked?: boolean;
  // Resolve confirmed accepted/rejected receipts; throw only while the outcome is unknown.
  send: (command: PaperCommand) => Promise<string>;
  preview: (command: PaperCommand, signal: AbortSignal) => Promise<Preview>;
  onAccountUpdate: () => void;
};

export function PaperTicket({ market, accountRevision, simulation, send, preview,
  onAccountUpdate, blocked = false }: Props) {
  const [side, setSide] = useState("BUY"), [outcome, setOutcome] = useState("YES");
  const [quantity, setQuantity] = useState("1"), [limit, setLimit] = useState("");
  const [tif, setTif] = useState("IOC"), [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState(""), [check, setCheck] = useState<Preview | null>(null);
  const [options, setOptions] = useState(simulation);
  const [unresolved, setUnresolved] = useState<PaperCommand | null>(null);
  const generation = useRef(0), submitting = useRef(false);
  const identity = market ? `${market.venue}/${market.local_day}/${market.yes_token_id}` : "";
  const activeIdentity = useRef(identity);
  activeIdentity.current = identity;
  useLayoutEffect(() => { setLimit(""); setNotice(""); generation.current++; }, [identity, outcome]);
  useEffect(() => { setOptions(simulation); }, [simulation.estimated_fee_rate, simulation.slippage_pp]);
  const token = outcome === "YES" ? market?.yes_token_id : market?.no_token_id;
  const selection = `${identity}/${outcome}/${side}/${quantity}/${limit}/${tif}/${accountRevision}`;
  const [checkedSelection, setCheckedSelection] = useState("");
  const [poll, setPoll] = useState(0);
  const command = (kind: string, payload: Record<string, unknown>): PaperCommand => ({
    request_id: crypto.randomUUID(), venue: market!.venue, mode: "sandbox", kind, payload,
  });
  useEffect(() => {
    const controller = new AbortController();
    const current = ++generation.current;
    let refresh: ReturnType<typeof setTimeout> | undefined;
    if (checkedSelection !== selection) setCheck(null);
    if (!market || !token || Number(quantity) <= 0 || Number(limit) <= 0) return;
    const timer = setTimeout(() => {
      preview(command("order", { token, side, quantity: Number(quantity), price: Number(limit), tif }),
        controller.signal).then(result => {
        if (current === generation.current) { setCheck(result); setCheckedSelection(selection); }
      }).catch(error => {
        if (!controller.signal.aborted && current === generation.current)
          setCheck({ available: false, reason: String(error) });
      }).finally(() => {
        if (!controller.signal.aborted) refresh = setTimeout(() => setPoll(value => value + 1), 2000);
      });
    }, 150);
    return () => { clearTimeout(timer); clearTimeout(refresh); controller.abort(); };
  }, [selection, preview, poll]);
  async function execute(kind: string, payload: Record<string, unknown>, retry?: PaperCommand) {
    if (submitting.current || !market) return;
    if (!retry && (blocked || unresolved)) return;
    submitting.current = true; setBusy(true); setNotice("");
    const current = identity;
    const request = retry ?? command(kind, payload);
    setUnresolved(request);
    try {
      const receipt = await send(request);
      setUnresolved(null);
      if (current === activeIdentity.current) setNotice(receipt);
      onAccountUpdate();
    } catch (error) {
      if (current === activeIdentity.current) setNotice(String(error));
    } finally { submitting.current = false; setBusy(false); }
  }
  const dirty = options.estimated_fee_rate !== simulation.estimated_fee_rate ||
    options.slippage_pp !== simulation.slippage_pp;
  return <section aria-label="模拟交易票据" className="ticket panel">
    <h2>模拟下单</h2>
    <p>{market ? `${market.venue} · ${market.local_day} · ${market.label}` : "请选择合约"}</p>
    <p>市场价格近似成交，不模拟盘口容量及排队。</p>
    <form onSubmit={e => { e.preventDefault(); void execute("order", {
      token, side, quantity: Number(quantity), price: Number(limit), tif,
    }); }}>
      <label>方向<select aria-label="方向" value={side} onChange={e => setSide(e.target.value)}>
        <option value="BUY">买入</option><option value="SELL">卖出</option>
      </select></label>
      <label>合约<select aria-label="合约" value={outcome} onChange={e => setOutcome(e.target.value)}>
        <option>YES</option><option>NO</option>
      </select></label>
      <label>数量<input aria-label="数量" type="number" value={quantity} required
        min={market?.minimum_order_size ?? .01} step={market?.quantity_step ?? .01}
        onChange={e => setQuantity(e.target.value)} /></label>
      <label>限价（美元）<input aria-label="限价" type="number" value={limit} required disabled={!market}
        min={market?.tick_size ?? .01} max={.999999} step={market?.tick_size ?? .01}
        onChange={e => setLimit(e.target.value)} /></label>
      <label>有效方式<select aria-label="有效方式" value={tif} onChange={e => setTif(e.target.value)}>
        <option value="IOC">IOC · 立即成交或取消</option>
        <option value="GTC">GTC · 等待满足限价</option>
        <option value="FOK">FOK · 全部成交或取消</option>
      </select></label>
      {check?.selected_price && <p>参考成交价 ${check.selected_price.price.toFixed(4)} ·
        {check.selected_price.price_source} · 报价距今 {Math.floor(check.selected_price.age_seconds)} 秒；
        预计费用 ${check.estimated_fee?.toFixed(2)}{check.fee_estimated ? "（估算）" : "（平台规则）"}；
        预计状态 {check.expected_status}</p>}
      <button type="submit" disabled={!market || busy || blocked || !!unresolved || dirty || !check?.available ||
        checkedSelection !== selection}>{busy ? "提交中…" : side === "BUY" ? "模拟买入" : "模拟卖出"}</button>
    </form>
    <details><summary>模拟费用与滑点</summary>
      <p>设置对后续订单生效；已有挂单请先取消再更改。已知平台费用始终按平台规则计算。</p>
      <label>未知费用（%）<input aria-label="未知费用" type="number" min="0" max="100" step=".01"
        value={options.estimated_fee_rate * 100} onChange={e => setOptions({ ...options,
          estimated_fee_rate: Number(e.target.value) / 100 })} /></label>
      <label>滑点（百分点）<input aria-label="滑点" type="number" min="0" max="100" step=".01"
        value={options.slippage_pp} onChange={e => setOptions({ ...options,
          slippage_pp: Number(e.target.value) })} /></label>
      <button disabled={!market || busy || blocked || !!unresolved || !dirty}
        onClick={() => void execute("simulation_settings", options)}>
        保存模拟设置</button>
    </details>
    {unresolved && !busy && <button onClick={() =>
      void execute(unresolved.kind, unresolved.payload, unresolved)}>查询或重试原请求</button>}
    <p role="status">{[notice, check?.reason || (!limit ? "请输入限价" : !check ? "正在读取下单条件…" :
      "成交与资金以服务端订单回执为准")].filter(Boolean).join("；")}</p>
  </section>;
}
