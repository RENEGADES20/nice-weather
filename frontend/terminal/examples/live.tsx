// Local-only host. Production integration supplies the existing useCommands.send callback.
import React, { useEffect, useState } from "react";
import { createRoot } from "react-dom/client";
import { LiveAccountPanel, type LiveSnapshot } from "../src/LiveAccountPanel";
import { LiveOrderTicket } from "../src/LiveOrderTicket";

function Example() {
  const [csrf, setCsrf] = useState("");
  const [venue, setVenue] = useState("poly_us");
  const [snapshot, setSnapshot] = useState<LiveSnapshot>();
  const [notice, setNotice] = useState("");
  const [pending, setPending] = useState(false);
  const journal = "nw-unconfirmed-commands-v1";
  async function api(path: string, body?: unknown) {
    const response = await fetch(`/api/${path}`, { method: body ? "POST" : "GET",
      headers: { "Content-Type": "application/json", "X-CSRF-Token": csrf },
      body: body ? JSON.stringify(body) : undefined });
    if (!response.ok) throw new Error(`${response.status}: ${await response.text()}`);
    return response.json();
  }
  useEffect(() => { api("session").then(s => setCsrf(s.csrf)).catch(() => {}); }, []);
  useEffect(() => {
    let cancelled = false;
    setSnapshot(undefined);
    async function poll() {
      if (!csrf) return;
      try {
        const data = await api("snapshot");
        if (cancelled) return;
        setSnapshot(data.accounts.find((a: { account: string }) => a.account === `live-${venue}-knyc`)?.snapshot);
        const saved = JSON.parse(localStorage.getItem(journal) ?? "[]");
        for (const command of saved) {
          const receipt = await api(`requests/${command.request_id}`).catch(() => null);
          if (!receipt || !["accepted", "rejected"].includes(receipt.status)) continue;
          if (receipt.account !== `live-${command.venue}-knyc` || receipt.kind !== command.kind) throw new Error("回执归属错误");
          await navigator.locks.request(journal, () => {
            const current = JSON.parse(localStorage.getItem(journal) ?? "[]");
            localStorage.setItem(journal, JSON.stringify(current.filter((c: { request_id: string }) => c.request_id !== command.request_id)));
          });
          if (!cancelled) setNotice(`${receipt.status}${receipt.error ? ` · ${receipt.error}` : ""}`);
        }
        if (!cancelled) setPending(JSON.parse(localStorage.getItem(journal) ?? "[]").length > 0);
      } catch (e) { if (!cancelled) { setNotice(String(e)); if (String(e).includes("401")) setCsrf(""); } }
    }
    void poll(); const timer = setInterval(poll, 500);
    return () => { cancelled = true; clearInterval(timer); };
  }, [venue, csrf]);
  async function send(kind: string, payload: Record<string, unknown>) {
    const command = { request_id: crypto.randomUUID(), mode: "live", venue, kind, payload };
    try {
      await navigator.locks.request(journal, () => {
        const saved = JSON.parse(localStorage.getItem(journal) ?? "[]");
        if (saved.length && !["cancel", "stop"].includes(kind)) throw new Error("存在未确认请求");
        localStorage.setItem(journal, JSON.stringify([...saved, command]));
      });
      setPending(true);
      await api("commands", command);
      setNotice("已排队，等待执行回执");
    } catch (e) { setNotice(String(e)); }
  }
  return <main><h1>任务 5 组件验收 · 模拟传输</h1><p>本页面仅用于本地开发；所有成交均由测试交易所产生。</p>
    {!csrf && <button onClick={async () => {
      try { setCsrf((await api("login", { password: "local-fixture-only" })).csrf); }
      catch (e) { setNotice(String(e)); }
    }}>登录本地验收</button>}
    <label>平台<select value={venue} onChange={e => setVenue(e.target.value)}><option value="poly_us">Poly US</option><option value="kalshi">Kalshi</option></select></label>
    <p role="status" data-testid="receipt">{notice}</p>
    <LiveAccountPanel snapshot={snapshot} send={send} pending={pending} venue={venue} />
    <LiveOrderTicket snapshot={snapshot} send={send} pending={pending} market="KNYC-TEST" />
  </main>;
}
createRoot(document.getElementById("root")!).render(<Example />);
