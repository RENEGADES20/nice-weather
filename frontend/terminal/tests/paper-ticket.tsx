import { useCallback, useEffect, useState } from "react";
import { createRoot } from "react-dom/client";
import { PaperTicket, type PaperCommand } from "../src/PaperTicket";

function Harness() {
  const [csrf, setCsrf] = useState(""), [state, setState] = useState<any>(null);
  const [venue, setVenue] = useState("kalshi"), [error, setError] = useState("");
  const refresh = useCallback(async () => {
    const response = await fetch("/api/snapshot");
    if (!response.ok) throw new Error(String(response.status));
    setState(await response.json());
  }, []);
  useEffect(() => {
    fetch("/api/login", { method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ password: "local-paper-test" }) }).then(r => r.json()).then(r => {
      setCsrf(r.csrf); return refresh();
    }).catch(e => setError(String(e)));
    const timer = setInterval(() => { void refresh().catch(e => setError(String(e))); }, 1000);
    return () => clearInterval(timer);
  }, [refresh]);
  const post = useCallback(async (path: string, command: PaperCommand, signal?: AbortSignal) => {
    const response = await fetch(path, { method: "POST", signal,
      headers: { "Content-Type": "application/json", "X-CSRF-Token": csrf },
      body: JSON.stringify(command) });
    const value = await response.json();
    if (!response.ok) throw new Error(value.detail ?? response.status);
    return value;
  }, [csrf]);
  const preview = useCallback((command: PaperCommand, signal: AbortSignal) =>
    post("/api/paper/preview", command, signal), [post]);
  const send = useCallback(async (command: PaperCommand) => {
    await post("/api/commands", command);
    for (let attempt = 0; attempt < 100; attempt++) {
      const receipt = await fetch(`/api/requests/${command.request_id}`).then(r => r.json());
      if (receipt.status === "rejected") return `拒绝：${receipt.error}`;
      if (receipt.status === "accepted") return `已处理 ${command.kind}`;
      await new Promise(resolve => setTimeout(resolve, 100));
    }
    throw new Error("回执仍待确认，请查询原请求，勿重复提交");
  }, [post]);
  if (!state || !csrf) return <p>{error || "加载中"}</p>;
  const account = state.accounts.find((a: any) => a.account === `sandbox-${venue}-knyc`);
  const snapshot = account?.snapshot ?? {};
  const market = state.contracts.find((c: any) => c.venue === venue);
  return <main><h1>任务 3 本地验收 · 开发样例市场 / 真实模拟后端</h1>
    <label>平台<select aria-label="平台" value={venue} onChange={e => setVenue(e.target.value)}>
      <option value="kalshi">Kalshi</option><option value="poly_us">Poly US</option></select></label>
    <PaperTicket market={market} accountRevision={snapshot.account_revision ?? ""}
      simulation={snapshot.simulation ?? { estimated_fee_rate: .01, slippage_pp: 0 }}
      send={send} preview={preview} onAccountUpdate={() => void refresh()} />
    <p data-testid="account">现金 {snapshot.cash?.toFixed(2)} · 持仓估值 {snapshot.market_value?.toFixed(2)} ·
      权益 {snapshot.equity?.toFixed(2)} · 费用 {snapshot.fees?.toFixed(2)} · PnL {snapshot.total_pnl?.toFixed(2)}</p>
    <p data-testid="positions">持仓 {(snapshot.positions ?? []).map((p: any) => `${p.token}: ${p.quantity}`).join(", ") || "无"}</p>
    <p data-testid="fills">成交数 {snapshot.fills?.length ?? 0}</p>
    <ul>{(snapshot.orders ?? []).map((o: any) => <li key={o.order_id}>{o.side} {o.quantity} · {o.status}</li>)}</ul>
  </main>;
}
createRoot(document.getElementById("root")!).render(<Harness />);
