import React, { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import { createRoot } from "react-dom/client";
import { MarketSelector } from "./market-weather/MarketSelector";
import { WeatherAnalysis } from "./market-weather/WeatherAnalysis";
import { useMarketCatalog, useMarketWeather } from "./market-weather/useMarketWeather";
import { type Selection, midpoint, todayNY } from "./market-weather/data";
import { PaperTicket, type PaperCommand, type Simulation } from "./PaperTicket";
import { LiveAccountPanel, type LiveSnapshot } from "./LiveAccountPanel";
import { LiveOrderTicket } from "./LiveOrderTicket";
import { BacktestWorkspace } from "./backtest/BacktestWorkspace";
import { BacktestChart, EventDetails } from "./backtest/BacktestChart";
import { type StrategyEvent, type Venue } from "./backtest/types";
import "./backtest/backtest.css";
import "./style.css";

type Contract = {
  venue: string;
  station_id: string;
  local_day: string;
  yes_token_id: string;
  title: string;
  no_token_id: string;
  condition_id: string;
  active: boolean;
  quantity_step?: string;
  settlement_source: string;
  parse_status: string;
  minimum_order_size: number;
  tick_size: string;
  fee_rate: number;
  fee_known: boolean;
};
type Book = {
  bids: number[][];
  asks: number[][];
  received_at: number;
  complete: boolean;
};
type Account = {
  account: string;
  mode: string;
  status: string;
  updated: number;
  snapshot: Snapshot;
  run_id: string;
};
type Snapshot = {
  simulation?: Simulation;
  market_value?: number;
  fees?: number;
  settlement_status?: string;
  cash?: number;
  equity?: number;
  available?: number;
  total_pnl?: number;
  strategy_enabled?: boolean;
  orders?: Record<string, any>[];
  positions?: Record<string, any>[];
  fills?: Record<string, any>[];
  signals?: Record<string, any>;
  replay_audit?: {
    requested_start: number;
    requested_end: number;
    scan_complete: boolean;
    inputs: Record<string, { count: number; first_received: number; last_received: number;
      max_gap_seconds: number; input_reasons?: Record<string, number> }>;
    decisions: Record<string, Record<string, number>>;
  };
};
type State = {
  cursor: number;
  contracts: Contract[];
  books: Record<string, Book>;
  weather: Record<string, any>;
  health: Record<string, any>;
  accounts: Account[];
  live: { enabled: boolean; reason: string };
};
const empty: State = {
  cursor: 0,
  contracts: [],
  books: {},
  weather: {},
  health: {},
  accounts: [],
  live: { enabled: false, reason: "实盘接入与对账验收未完成" },
};
const money = (n?: number | null) =>
  n == null
    ? "—"
    : new Intl.NumberFormat("en-US", {
        style: "currency",
        currency: "USD",
        maximumFractionDigits: 2,
      }).format(n);
const price = (n?: number) => (n == null ? "—" : `${(n * 100).toFixed(1)}¢`);
const clock = (n?: number) =>
  n ? new Date(n * 1000).toLocaleTimeString("zh-CN", { timeZone: "America/New_York", hour12: false }) : "—";

async function api(path: string, body?: unknown, csrf = "") {
  const response = await fetch("/api/" + path, {
    method: body ? "POST" : "GET",
    credentials: "same-origin",
    headers: body
      ? { "Content-Type": "application/json", "X-CSRF-Token": csrf }
      : {},
    body: body ? JSON.stringify(body) : undefined,
    signal: AbortSignal.timeout(10000),
  });
  if (!response.ok) {
    let message = `请求失败 (${response.status})`;
    try {
      message = (await response.json()).detail ?? message;
    } catch {}
    throw Object.assign(new Error(message), { status: response.status });
  }
  return response.json();
}

type SavedCommand = {
  request_id: string;
  venue: string;
  mode: string;
  kind: string;
  payload: Record<string, unknown>;
};
const commandStorage = "nw-unconfirmed-commands-v1";
function readCommands(): SavedCommand[] {
  const commands = JSON.parse(localStorage.getItem(commandStorage) ?? "[]");
  if (
    !Array.isArray(commands) ||
    commands.some(
      (c) => !c?.request_id || !c.venue || !c.kind || !c.mode || !c.payload,
    )
  )
    throw new Error("本地请求记录损坏，交易已暂停");
  return commands;
}

function useCommands(
  csrf: string,
  session: boolean,
  notify: (text: string) => void,
) {
  const [blocked, setBlocked] = useState(false);
  const [saved, setSaved] = useState<SavedCommand[]>([]);
  const journal = useRef<SavedCommand[]>([]);
  const sending = useRef(new Set<string>());
  const [pending, setPending] = useState(false);
  const update = (commands: SavedCommand[]) => {
    journal.current = commands;
    setSaved(commands);
  };
  const persist = async (
    change: (current: SavedCommand[]) => SavedCommand[],
  ) => {
    // Browser-native lock also protects two terminal tabs submitting at the same time.
    try {
      await navigator.locks.request(commandStorage, () => {
        const commands = change(readCommands());
        localStorage.setItem(commandStorage, JSON.stringify(commands));
        update(commands);
      });
    } catch (e) {
      setBlocked(true);
      throw e;
    }
  };
  const forget = (id: string) =>
    persist((current) => current.filter((c) => c.request_id !== id));
  const reconcile = async (command: SavedCommand) => {
    const row = await api(`requests/${encodeURIComponent(command.request_id)}`);
    if (
      row.account !== `${command.mode}-${command.venue}-knyc` ||
      row.kind !== command.kind
    )
      throw new Error("回执账户或指令不匹配，保留待核验状态");
    if (["accepted", "rejected"].includes(row.status)) {
      await forget(command.request_id);
      notify(
        `${command.venue} · ${command.kind} · ${row.status}${row.error ? ` · ${row.error}` : ""}`,
      );
    }
    return row;
  };
  useEffect(() => {
    const refresh = () => {
      try {
        if (!navigator.locks) throw new Error("Browser locks unavailable");
        update(readCommands());
      } catch {
        setBlocked(true);
        notify("无法读取或保存本地请求记录，交易已暂停");
      }
    };
    refresh();
    window.addEventListener("storage", refresh);
    return () => window.removeEventListener("storage", refresh);
  }, []);
  useEffect(() => {
    if (!session) return;
    const poll = () => {
      for (const command of journal.current) reconcile(command).catch(() => {}); // Missing/failed lookup cannot authorize a new order.
    };
    poll();
    const timer = setInterval(poll, 1000);
    return () => clearInterval(timer);
  }, [session]);
  async function confirmed(command: SavedCommand) {
    for (let i = 0; i < 40; i++) {
      const row = await reconcile(command);
      if (["accepted", "rejected"].includes(row.status))
        return `${row.status}${row.error ? ` · ${row.error}` : ""}`;
      await new Promise(resolve => setTimeout(resolve, 250));
    }
    throw new Error("执行结果尚未确认，保留原请求 ID");
  }
  async function send(command: SavedCommand, retry = false): Promise<string> {
    if (blocked || sending.current.has(command.request_id)) throw new Error("请求处理中或本地记录不可用");
    if (
      !retry &&
      !["cancel", "stop"].includes(command.kind) &&
      journal.current.length
    ) {
      throw new Error("有未确认请求；先查询回执或用原请求 ID 重试");
    }
    sending.current.add(command.request_id);
    performance.clearMarks("command-start");
    performance.mark("command-start");
    setPending(true);
    notify("提交中…");
    try {
      if (retry) {
        try {
          await reconcile(command);
          return await confirmed(command); // Existing receipts never authorize another POST.
        } catch (e) {
          if ((e as { status?: number }).status !== 404) throw e;
        }
      } else {
        let added = false;
        await persist((current) => {
          if (!["cancel", "stop"].includes(command.kind) && current.length)
            return current;
          added = true;
          return [...current, command];
        });
        if (!added) {
          throw new Error("有未确认请求；先查询回执或用原请求 ID 重试");
        }
      }
      const row = await api("commands", command, csrf);
      if (row.request_id !== command.request_id)
        throw new Error("请求回执 ID 不匹配");
      notify(`已排队 · ${command.request_id.slice(0, 8)}，等待执行确认`);
      return await confirmed(command);
    } catch (e) {
      const status = (e as { status?: number }).status;
      // Only a first submission rejected before enqueue is conclusive. Retries retain ambiguity.
      if (!retry && status && [400, 401, 403, 409, 422].includes(status)) {
        await forget(command.request_id);
        notify(`请求被拒绝 · ${(e as Error).message}`);
        return `请求被拒绝 · ${(e as Error).message}`;
      } else {
        notify(`请求未确认 · ${(e as Error).message}；先核验回执`);
        throw e;
      }
    } finally {
      sending.current.delete(command.request_id);
      setPending(sending.current.size > 0);
    }
  }
  return { saved, blocked, pending, send };
}


function App() {
  const [session, setSession] = useState(false), [csrf, setCsrf] = useState(""),
    [password, setPassword] = useState(""), [authMode, setAuthMode] = useState("loading");
  const [state, setState] = useState<State>(empty);
  const [selection, setSelection] = useState<Selection>(() => {
    const q = new URLSearchParams(location.search);
    return { venue: q.get("venue") === "poly_us" ? "poly_us" : "kalshi",
      day: q.get("day") ?? todayNY(), token: q.get("token") ?? "" };
  });
  const { venue, day, token } = selection;
  const [connected, setConnected] = useState(false), [now, setNow] = useState(Date.now() / 1000),
    [notice, setNotice] = useState(""), [connectionError, setConnectionError] = useState("");
  const [mode, setMode] = useState("sandbox"), [tab, setTab] = useState("交易");
  const [receipts, setReceipts] = useState<Record<string, any>[]>([]);
  const commands = useCommands(csrf, session, setNotice);
  const catalog = useMarketCatalog(session ? venue : "");
  const markets = (catalog.data?.days.find(d => d.day === day)?.contracts ?? []) as unknown as Contract[];
  const contract = markets.find(c => c.yes_token_id === token);
  const weather = useMarketWeather(selection, !!contract?.active, session);
  const account = state.accounts.find(a => a.account === `${mode}-${venue}-knyc`);
  const paperAccount = state.accounts.find(a => a.account === `sandbox-${venue}-knyc`);
  const snapshot = paperAccount?.snapshot ?? {};
  const liveSnapshot = mode === "live" ? account?.snapshot as unknown as LiveSnapshot : undefined;
  const [events, setEvents] = useState<{ key: string; rows: StrategyEvent[] }>({ key: "", rows: [] });
  const [eventError, setEventError] = useState("");
  const [focus, setFocus] = useState<StrategyEvent | null>(null);
  const eventKey = `${venue}/${day}`;
  const [curve, setCurve] = useState<{ run: string; points: { time: number; value: number | null }[] }>({ run: "", points: [] });
  const [curveError, setCurveError] = useState("");
  const preview = useCallback(async (command: PaperCommand, signal: AbortSignal) => {
    const response = await fetch("/api/paper/preview", { method: "POST", credentials: "same-origin",
      headers: { "Content-Type": "application/json", "X-CSRF-Token": csrf },
      body: JSON.stringify(command), signal });
    if (!response.ok) throw new Error((await response.json()).detail ?? `预览失败 (${response.status})`);
    return response.json();
  }, [csrf]);
  const refreshAccount = useCallback(() => {
    api("snapshot").then(result => setState(old => ({ ...old, accounts: result.accounts })))
      .catch(e => setNotice(String(e)));
  }, []);
  const submit = async (kind: string, payload: Record<string, unknown> = {}, targetMode = mode) => {
    try { await commands.send({ request_id: crypto.randomUUID(), venue, mode: targetMode, kind, payload }); }
    catch (e) { setNotice(String(e)); }
  };
  useLayoutEffect(() => {
    const url = new URL(location.href);
    Object.entries(selection).forEach(([key, value]) => url.searchParams.set(key, value));
    history.replaceState(null, "", url);
  }, [venue, day, token]);
  useEffect(() => { setFocus(null); }, [venue, day]);
  useEffect(() => {
    const book = state.books[token];
    if (book && contract) weather.acceptBook({ key: token, received: book.received_at, data: book });
  }, [state.books[token], token]);
  useEffect(() => {
    if (!session || !day) return;
    const abort = new AbortController(); let cursor = 0;
    let timer: ReturnType<typeof setTimeout>;
    const collected = new Map<string, StrategyEvent>();
    setEventError("");
    async function poll() {
      try {
        let more = true;
        while (more && !abort.signal.aborted) {
          const response = await fetch(`/api/account-events?${new URLSearchParams({venue, day, after: String(cursor)})}`, {signal: abort.signal});
          if (!response.ok) throw new Error(`信号记录读取失败 (${response.status})`);
          const result = await response.json();
          if (abort.signal.aborted) return;
          result.events.forEach((e: StrategyEvent) => collected.set(e.id, e));
          cursor = result.next; more = result.more;
        }
        setEvents({key: eventKey, rows: [...collected.values()].sort((a,b) => a.time-b.time)});
        setEventError("");
      } catch (e) { if (!abort.signal.aborted) setEventError(String(e)); }
      if (!abort.signal.aborted) timer = setTimeout(poll, 3000);
    }
    void poll(); return () => { abort.abort(); clearTimeout(timer); };
  }, [session, venue, day]);
  useEffect(() => {
    const run = paperAccount?.run_id;
    if (!session || !run) return;
    const abort = new AbortController(); let after = -1;
    let timer: ReturnType<typeof setTimeout>;
    const points = new Map<number, { time: number; value: number | null }>();
    async function poll() {
      try {
        let more = true;
        while (more && !abort.signal.aborted) {
          const response = await fetch(`/api/paper/equity?run_id=${encodeURIComponent(run!)}&after=${after}`, { signal: abort.signal });
          if (!response.ok) throw new Error(`权益读取失败 (${response.status})`);
          const result = await response.json();
          if (abort.signal.aborted) return;
          for (const p of result.points) { points.set(p.ts, {time: p.ts/1e9, value: p.equity}); after = p.ts; }
          more = result.next != null;
        }
        setCurve({run: run!, points: [...points.values()]}); setCurveError("");
      } catch(e) { if (!abort.signal.aborted) setCurveError(String(e)); }
      if (!abort.signal.aborted) timer = setTimeout(poll, 5000);
    }
    void poll(); return () => {abort.abort(); clearTimeout(timer);};
  }, [session, paperAccount?.run_id]);
  const chartEvents = useMemo(() => events.key === eventKey ? events.rows.filter(e => e.token === token) : [], [events, eventKey, token]);
  const chartSelection = useMemo(() => ({venue: venue as Venue, day, token}), [venue, day, token]);
  const prices = useMemo(() => {
    const sorted = [...new Map(weather.quotes.map(q => [q.time, q])).values()].sort((a,b) => a.time-b.time);
    return sorted.flatMap((q,i) => {
      const value = midpoint(q);
      const point = { time: q.time, value: value == null ? null : value/100 };
      return i && q.time - sorted[i-1].time > 600 ? [{time: sorted[i-1].time+600, value:null}, point] : [point];
    });
  }, [weather.quotes]);
  const book = weather.quotes.at(-1);
  const blocked = commands.blocked || commands.pending || commands.saved.length > 0;
  const locate = (e: StrategyEvent) => {
    if (e.token && markets.some(c => c.yes_token_id === e.token)) setSelection({...selection, token:e.token});
    setFocus(e); document.getElementById("market-chart")?.scrollIntoView({block:"center", behavior:"smooth"});
  };
  useEffect(() => {
    api("auth").then((r) => setAuthMode(r.mode)).catch(() => setAuthMode("unavailable"));
    api("session")
      .then((r) => {
        setCsrf(r.csrf);
        setSession(true);
      })
      .catch(() => {});
    const t = setInterval(() => setNow(Date.now() / 1000), 1000);
    return () => clearInterval(t);
  }, []);
  useEffect(() => {
    if (!session) return;
    let stopped = false;
    let socket: WebSocket | undefined;
    let timer: ReturnType<typeof setTimeout>;
    const connect = async () => {
      try {
        // A server restart rotates CSRF even when Cloudflare identity remains valid.
        const [initial, currentSession] = await Promise.all([
          api("snapshot"), api("session"),
        ]);
        if (stopped) return;
        setCsrf(currentSession.csrf);
        setState(initial);
        socket = new WebSocket(
          `${location.protocol === "https:" ? "wss" : "ws"}://${location.host}/api/events?cursor=${initial.cursor}`,
        );
        socket.onopen = () => {
          setConnectionError("");
          setConnected(true);
        };
        socket.onmessage = (e) => {
          const message = JSON.parse(e.data);
          if (message.events?.some((event: { kind: string }) => event.kind === "book")) {
            performance.clearMarks("market-event-received");
            performance.mark("market-event-received");
          }
          setState((old) => {
            const next = {
              ...old,
              books: { ...old.books },
              weather: { ...old.weather },
              health: { ...old.health },
            };
            if (message.accounts) next.accounts = message.accounts;
            for (const event of message.events ?? []) {
              if (event.kind === "book") next.books[event.key] = event.data;
              else if (event.kind === "contracts")
                next.contracts = [
                  ...next.contracts.filter((c) => c.venue !== event.key),
                  ...event.data,
                ];
              else if (event.kind === "weather")
                next.weather[event.key] = event.data;
              else if (event.kind === "health")
                next.health[event.key] = event.data;
              next.cursor = event.seq;
            }
            return next;
          });
        };
        socket.onclose = () => {
          setConnected(false);
          if (!stopped) timer = setTimeout(connect, 1500);
        };
      } catch (e) {
        if (stopped) return;
        setConnected(false);
        setConnectionError(String(e));
        if (!stopped) timer = setTimeout(connect, 3000);
      }
    };
    connect();
    return () => {
      stopped = true;
      clearTimeout(timer);
      socket?.close();
    };
  }, [session]);
  useEffect(() => {
    if (!session) return;
    const poll = () =>
      api("requests")
        .then(setReceipts)
        .catch(() => {});
    poll();
    const t = setInterval(poll, 2000);
    return () => clearInterval(t);
  }, [session]);

  if (!session)
    return (
      <main className="login">
        <div className="brand">
          NW <span>NICE WEATHER</span>
        </div>
        <h1>交易终端</h1>
        <p>KNYC · Kalshi / Polymarket US</p>
        {authMode === "password" ? <form
          onSubmit={async (e) => {
            e.preventDefault();
            try {
              const r = await api("login", { password });
              setPassword("");
              setCsrf(r.csrf);
              setSession(true);
              setNotice("");
            } catch (e) {
              setNotice((e as Error).message);
            }
          }}
        >
          <label>
            终端访问密码
            <input
              type="password"
              autoComplete="current-password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              required
            />
          </label>
          <button className="primary">登录</button>
        </form> : <p>{authMode === "loading" ? "正在验证登录状态…" :
          authMode === "cloudflare" ? "使用网站现有登录。若会话失效，请重新加载页面完成登录。" :
          "终端认证接入尚未就绪，请稍后重试。"}</p>}
        <p role="alert">{notice}</p>
      </main>
    );

  return <>
    <header><div className="brand">NW <span>NICE WEATHER</span></div>
      <nav>{["交易", "回测"].map(t => <button key={t} className={tab === t ? "active" : ""} onClick={() => setTab(t)}>{t}</button>)}</nav>
      <div className="connection"><i className={connected ? "online" : ""}/>{connected ? "终端已连接" : "重连中"}<span>{clock(now)} 纽约</span></div>
    </header>
    {tab === "交易" && <div className="toolbar">
      <MarketSelector catalog={catalog.data} value={selection} onChange={setSelection} loading={catalog.loading} error={catalog.error}/>
      <div className="mode"><button className={mode === "sandbox" ? "active" : ""} onClick={() => setMode("sandbox")}>模拟盘</button>
        <button className={mode === "live" ? "active" : ""} onClick={() => setMode("live")}>实盘</button></div>
    </div>}
    {commands.saved.map(c => <div className="banner" key={c.request_id}>待核验 · {c.venue} / {c.mode} / {c.kind} · {c.request_id.slice(0,8)}
      <button disabled={commands.pending} onClick={() => void commands.send(c,true).catch(e => setNotice(String(e)))}>查询回执 / 原 ID 重试</button></div>)}
    <p className="notice" role="status">{connectionError || notice}</p>
    {tab === "回测" ? <BacktestWorkspace csrf={csrf} initialVenue={venue as Venue}/> : <>
      {mode === "sandbox" && <div className="metrics">{[["现金",snapshot.cash],["可用资金",snapshot.available],["持仓估值",snapshot.market_value],["账户权益",snapshot.equity],["总收益",snapshot.total_pnl],["累计费用",snapshot.fees]].map(([label,value]) =>
        <div key={String(label)}><span>{label}</span><strong>{money(value as number)}</strong></div>)}</div>}
      <main className="workspace integrated">
        <section className="market panel" id="market-chart"><div className="section-title"><h2>KNYC 每日最高温</h2><span>{day} · {contract?.title ?? "所选日期无合约"} · YES 赔率</span></div>
          <div className="bins">{markets.map(c => <button key={c.yes_token_id} aria-pressed={token === c.yes_token_id} className={token === c.yes_token_id ? "selected" : ""}
            onClick={() => setSelection({...selection, token:c.yes_token_id})}>{c.title}</button>)}</div>
          <BacktestChart points={prices} label="市场赔率与策略标记" selectionKey={`${eventKey}/${token}`} selection={chartSelection} events={chartEvents} focus={focus} onLocate={locate}/>
          <p className="notice">{weather.loading ? "行情历史加载中…" : weather.priceReason || (!prices.length ? "没有价格历史" : "")}{eventError}</p>
        </section>
        <section className="orderbook panel"><div className="section-title"><h2>公开报价 · YES</h2><span>{clock(book?.received_at)}</span></div>
          <div className="book-head"><span>价格</span><span>份数</span></div>
          {(book?.asks ?? []).slice(0,8).reverse().map(([p,q],i) => <div className="level ask" key={i}><span>{price(p)}</span><span>{q}</span></div>)}
          <div className="spread">{book ? "市场价格" : "报价缺失"}</div>
          {(book?.bids ?? []).slice(0,8).map(([p,q],i) => <div className="level bid" key={i}><span>{price(p)}</span><span>{q}</span></div>)}
        </section>
        {mode === "sandbox" ? <PaperTicket market={contract ? {...contract, label:contract.title} : null}
          accountRevision={JSON.stringify([snapshot.cash, snapshot.available,
            snapshot.positions?.map(p => [p.token, p.quantity]),
            snapshot.orders?.map(o => [o.order_id, o.status, o.filled]), snapshot.simulation])} simulation={snapshot.simulation ?? {estimated_fee_rate:.01,slippage_pp:0}}
          blocked={blocked} preview={preview} send={c => commands.send(c, commands.saved.some(s => s.request_id === c.request_id))} onAccountUpdate={refreshAccount}/>
          : <LiveOrderTicket key={`${venue}/${day}/${token}`} snapshot={liveSnapshot} market={contract?.condition_id ?? ""} pending={blocked} send={(kind,payload) => submit(kind,payload,"live")}/>}
        <section className="weather-panel"><WeatherAnalysis data={weather.weather} quotes={weather.quotes} token={token} loading={weather.loading} error={weather.error} priceReason={weather.priceReason}/></section>
        <section className="strategies panel"><div className="section-title"><h2>天气策略信号</h2>
          <button disabled={commands.blocked || !paperAccount || (!snapshot.strategy_enabled && blocked)} onClick={() => submit(snapshot.strategy_enabled ? "stop" : "start", {strategy_id:"S1_S2_S3"}, "sandbox")}>{snapshot.strategy_enabled ? "停止模拟策略" : "启动模拟三策略"}</button></div>
          <div className="strategy-grid">{[["S1","相邻两档"],["S2","新高跨档"],["S3","结束后单档"]].map(([id,name]) => {
            const signal = snapshot.signals?.[id];
            const current = signal?.day === day && signal?.venue === venue ? signal : undefined;
            const last = events.key === eventKey ? events.rows.filter(e => e.strategy === id).at(-1) : undefined;
            return <article key={id}><b>{id} <span>{name}</span></b><p>{current?.candidate_reason ?? current?.reason ?? last?.reason ?? "所选市场日尚无信号记录"}</p>
              {current?.p_end != null && <p>结束概率 {(current.p_end*100).toFixed(1)}%</p>}
              {current?.execution_reason && <p>执行状态：{current.execution_reason}</p>}
              {current?.model_validation && <p>模型验证：{typeof current.model_validation === "object" ? (current.model_validation.kind ?? "验证状态未记录") : String(current.model_validation)}</p>}
            </article>;
          })}</div>
          <p className="notice">模拟策略{snapshot.strategy_enabled ? "已启动" : "已停止"}；天气信号持续更新。Live 策略开关在实盘账户中配置。</p>
          <details><summary>所选市场日信号与交易记录</summary><div className="event-list">
            {(events.key === eventKey ? events.rows : []).filter(e => e.stage !== "diagnostic").map(e => <button key={e.id} onClick={() => locate(e)}><EventDetails event={e}/></button>)}
          </div></details>
        </section>
        {mode === "live" ? <div className="positions"><LiveAccountPanel key={venue} snapshot={liveSnapshot} pending={blocked} venue={venue} send={(kind,payload) => submit(kind,payload,"live")}/></div>
          : <section className="positions panel"><div className="section-title"><h2>模拟持仓与订单</h2><span>{paperAccount?.account ?? "账户尚未启动"} · {snapshot.settlement_status ?? "未记录结算状态"}</span></div>
            <div className="table-wrap"><table><thead><tr><th>合约</th><th>方向</th><th>数量</th><th>价格</th><th>状态 / 来源</th><th>操作</th></tr></thead><tbody>
              {(snapshot.orders ?? []).map(o => <tr key={o.order_id}><td>{o.token}</td><td>{o.side}</td><td>{o.filled} / {o.quantity}</td><td>{price(o.price)}</td><td>{o.status} · {o.owner}</td><td><button disabled={commands.blocked || o.remaining <= 0} onClick={() => submit("cancel",{order_id:o.order_id},"sandbox")}>撤单</button></td></tr>)}
              {!snapshot.orders?.length && <tr><td colSpan={6}>尚无订单</td></tr>}
            </tbody></table></div><div className="position-grid">{(snapshot.positions ?? []).map(p => <div key={p.token}><b>{p.bin} · {p.outcome}</b><span>{p.quantity} 份</span><span>成本 {money(p.cost)}</span><span>浮盈亏 {money(p.unrealized_pnl)}</span></div>)}</div>
            <div className="receipts">{receipts.filter(r => r.account === paperAccount?.account).slice(0,5).map(r => <div key={r.request_id}>{clock(r.created)} · {r.kind} · {r.status} {r.error}</div>)}</div>
            <details><summary>模拟账户权益曲线</summary><BacktestChart points={curve.run === paperAccount?.run_id ? curve.points : []} label="模拟账户权益" selectionKey={paperAccount?.run_id ?? ""}/><p>{curveError}</p></details>
          </section>}
      </main></>}
    <footer><span>NICE WEATHER / KNYC</span><span>纽约时间显示 · 结算窗口按合约 · 模拟成交为近似</span></footer>
  </>;
}
createRoot(document.getElementById("root")!).render(<App />);
