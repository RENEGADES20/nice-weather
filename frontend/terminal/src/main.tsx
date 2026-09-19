import React, { useEffect, useLayoutEffect, useRef, useState } from "react";
import { createRoot } from "react-dom/client";
import {
  ColorType,
  createChart,
  LineSeries,
  type Time,
} from "lightweight-charts";
import "./style.css";

type Contract = {
  venue: string;
  station_id: string;
  local_day: string;
  yes_token_id: string;
  title: string;
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
  cash?: number;
  equity?: number;
  available?: number;
  total_pnl?: number;
  strategy_enabled?: boolean;
  orders?: Record<string, any>[];
  positions?: Record<string, any>[];
  fills?: Record<string, any>[];
  signals?: Record<string, any>;
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
  n ? new Date(n * 1000).toLocaleTimeString() : "—";

async function api(path: string, body?: unknown, csrf = "") {
  const response = await fetch("/api/" + path, {
    method: body ? "POST" : "GET",
    credentials: "same-origin",
    headers: body
      ? { "Content-Type": "application/json", "X-CSRF-Token": csrf }
      : {},
    body: body ? JSON.stringify(body) : undefined,
  });
  if (!response.ok) {
    let message = `请求失败 (${response.status})`;
    try {
      message = (await response.json()).detail ?? message;
    } catch {}
    throw new Error(message);
  }
  return response.json();
}

function Chart({ token, book }: { token: string; book?: Book }) {
  const element = useRef<HTMLDivElement>(null);
  const chartRef = useRef<ReturnType<typeof createChart> | null>(null);
  const lineRef = useRef<ReturnType<
    ReturnType<typeof createChart>["addSeries"]
  > | null>(null);
  const cache = useRef(new Map<string, Map<number, number>>());
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const earliest = useRef(new Map<string, number>());
  const activeToken = useRef(token);
  activeToken.current = token;
  const [olderLoading, setOlderLoading] = useState(false);
  useEffect(() => {
    const chart = createChart(element.current!, {
      autoSize: true,
      layout: {
        background: { type: ColorType.Solid, color: "#101719" },
        textColor: "#97a8aa",
        attributionLogo: true,
      },
      grid: {
        vertLines: { color: "#1b2629" },
        horzLines: { color: "#1b2629" },
      },
      rightPriceScale: { borderColor: "#283437" },
      localization: {
        timeFormatter: (t: Time) => new Date(Number(t) * 1000).toLocaleString(),
      },
      timeScale: {
        timeVisible: true,
        borderColor: "#283437",
        tickMarkFormatter: (t: Time) =>
          new Date(Number(t) * 1000).toLocaleTimeString([], {
            hour: "2-digit",
            minute: "2-digit",
          }),
      },
      crosshair: {
        vertLine: { color: "#6c9893" },
        horzLine: { color: "#6c9893" },
      },
    });
    chartRef.current = chart;
    lineRef.current = chart.addSeries(LineSeries, {
      color: "#79c5ae",
      lineWidth: 2,
      priceFormat: {
        type: "custom",
        formatter: (v: number) => `${(v * 100).toFixed(1)}¢`,
      },
    });
    return () => {
      chart.remove();
    };
  }, []);
  const draw = (points: Map<number, number>) =>
    lineRef.current?.setData(
      [...points]
        .sort((a, b) => a[0] - b[0])
        .map(([time, value]) => ({ time: time as Time, value })),
    );
  useLayoutEffect(() => {
    setError("");
    const saved = cache.current.get(token);
    if (saved) {
      draw(saved);
      setLoading(false);
    } else {
      lineRef.current?.setData([]);
      setLoading(Boolean(token));
    }
    if (!token) return;
    let active = true;
    if (!saved)
      api(`history?token=${encodeURIComponent(token)}`)
        .then((rows) => {
          if (!active) return;
          const points = cache.current.get(token) ?? new Map<number, number>();
          if (rows.length) earliest.current.set(token, rows[0].seq);
          for (const r of rows) {
            if (r.bids?.length && r.asks?.length)
              points.set(Math.floor(r.time), (r.bids[0][0] + r.asks[0][0]) / 2);
          }
          cache.current.set(token, points);
          if (cache.current.size > 48)
            cache.current.delete(cache.current.keys().next().value!);
          draw(points);
          chartRef.current?.timeScale().fitContent();
          setLoading(false);
        })
        .catch((e) => {
          if (active) {
            setError(e.message);
            setLoading(false);
          }
        });
    return () => {
      active = false;
    };
  }, [token]);
  useEffect(() => {
    if (!token || !book?.bids.length || !book.asks.length) return;
    const points = cache.current.get(token) ?? new Map<number, number>();
    const t = Math.floor(book.received_at);
    points.set(t, (book.bids[0][0] + book.asks[0][0]) / 2);
    if (points.size > 12000) points.delete(points.keys().next().value!);
    cache.current.set(token, points);
    lineRef.current?.update({
      time: t as Time,
      value: (book.bids[0][0] + book.asks[0][0]) / 2,
    });
  }, [token, book]);
  async function older() {
    const before = earliest.current.get(token);
    if (!before || olderLoading) return;
    setOlderLoading(true);
    setError("");
    try {
      const rows = await api(
        `history?token=${encodeURIComponent(token)}&before=${before}`,
      );
      if (!rows.length) {
        earliest.current.delete(token);
        return;
      }
      earliest.current.set(token, rows[0].seq);
      const points = cache.current.get(token) ?? new Map<number, number>();
      for (const r of rows)
        if (r.bids?.length && r.asks?.length)
          points.set(Math.floor(r.time), (r.bids[0][0] + r.asks[0][0]) / 2);
      // ponytail: explicit history pages capped at 12k points per selected bin.
      const bounded = new Map(
        [...points].sort((a, b) => a[0] - b[0]).slice(-12000),
      );
      cache.current.set(token, bounded);
      if (activeToken.current === token) draw(bounded);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setOlderLoading(false);
    }
  }
  return (
    <div className="chart">
      <button
        className="history-more"
        disabled={olderLoading || !earliest.current.has(token)}
        onClick={older}
      >
        {olderLoading ? "加载中…" : "加载更早行情"}
      </button>
      <div ref={element} className="chart-canvas" />
      {(loading || error || !token) && (
        <div className="chart-status">
          {error || (loading ? "历史加载中，其他操作可继续" : "等待合约数据")}
        </div>
      )}
    </div>
  );
}

function Weather({ weather, day }: { weather: State["weather"]; day: string }) {
  const observation = Array.isArray(weather.metar?.data)
    ? weather.metar.data
    : [];
  const withinDay = (t: number) =>
    new Date((t - 5 * 3600) * 1000).toISOString().slice(0, 10) === day;
  const observed = observation
    .filter(
      (p: any) =>
        p.icaoId === "KNYC" && Number.isFinite(p.temp) && withinDay(p.obsTime),
    )
    .map((p: any) => [p.obsTime, p.temp * 1.8 + 32])
    .sort((a: number[], b: number[]) => a[0] - b[0]);
  const forecast = (weather.hrrr?.points ?? [])
    .filter(
      (p: any) => Number.isFinite(p.temperature_f) && withinDay(p.valid_at),
    )
    .map((p: any) => [p.valid_at, p.temperature_f]);
  const all = [...observed, ...forecast];
  const minT = Math.min(...all.map((p) => p[1])) - 1,
    maxT = Math.max(...all.map((p) => p[1])) + 1;
  const start = new Date(`${day}T00:00:00-05:00`).getTime() / 1000;
  const line = (rows: number[][]) =>
    rows
      .map(
        ([t, v]) =>
          `${40 + ((t - start) / 86400) * 720},${140 - ((v - minT) / (maxT - minT)) * 120}`,
      )
      .join(" ");
  return (
    <section className="weather-panel panel">
      <div className="section-title">
        <h2>天气 · KNYC</h2>
        <span>气候日 UTC−5 · °F</span>
      </div>
      <div className="weather-values">
        <span>
          最新实况{" "}
          <b>{observed.length ? `${observed.at(-1)![1].toFixed(1)}°F` : "—"}</b>
        </span>
        <span>HRRR 起报 {clock(weather.hrrr?.cycle)}</span>
        <span>绿色：METAR · 蓝色：HRRR</span>
      </div>
      {all.length ? (
        <svg
          viewBox="0 0 800 180"
          role="img"
          aria-label="KNYC 实况与 HRRR 温度预报"
        >
          <text x="0" y="25" fill="#97a8aa">
            {maxT.toFixed(0)}°
          </text>
          <text x="0" y="145" fill="#97a8aa">
            {minT.toFixed(0)}°
          </text>
          <polyline
            points={line(observed)}
            fill="none"
            stroke="#79c5ae"
            strokeWidth="2"
          />
          <polyline
            points={line(forecast)}
            fill="none"
            stroke="#70b6e9"
            strokeWidth="2"
            strokeDasharray="5 3"
          />
          {[0, 6, 12, 18, 24].map((h) => (
            <text
              key={h}
              x={40 + (h / 24) * 720}
              y="172"
              textAnchor="middle"
              fill="#97a8aa"
            >
              {String(h).padStart(2, "0")}:00
            </text>
          ))}
        </svg>
      ) : (
        <p className="empty">
          所选气候日暂无已采集天气；小时观测与 CLI 结算值分别保存。
        </p>
      )}
      <details>
        <summary>
          CLI 原文 · {weather.cli?.issued_at ?? "尚未取得"} · 最终性待核验
        </summary>
        <pre>{weather.cli?.text ?? "等待报告"}</pre>
      </details>
    </section>
  );
}

function App() {
  const [session, setSession] = useState(false),
    [csrf, setCsrf] = useState(""),
    [password, setPassword] = useState("");
  const [state, setState] = useState<State>(empty),
    [venue, setVenue] = useState("kalshi"),
    [day, setDay] = useState(""),
    [selected, setSelected] = useState("");
  const [connected, setConnected] = useState(false),
    [now, setNow] = useState(Date.now() / 1000),
    [notice, setNotice] = useState("");
  const [mode, setMode] = useState("sandbox"),
    [tab, setTab] = useState("交易"),
    [side, setSide] = useState("BUY"),
    [outcome, setOutcome] = useState("YES");
  const [qty, setQty] = useState("1"),
    [limit, setLimit] = useState("0.50"),
    [pending, setPending] = useState(false),
    [tif, setTif] = useState("IOC");
  const [replayDay, setReplayDay] = useState(""),
    [replayStrategy, setReplayStrategy] = useState("S1_S2_S3");
  const [receipts, setReceipts] = useState<Record<string, any>[]>([]);
  useLayoutEffect(() => {
    const started = performance
      .getEntriesByName("market-event-received")
      .at(-1)?.startTime;
    if (started == null) return;
    const frame = requestAnimationFrame(() => {
      performance.clearMeasures("market-event-display");
      performance.measure("market-event-display", {
        start: started,
        end: performance.now(),
      });
    });
    return () => cancelAnimationFrame(frame);
  }, [state.cursor]);
  useEffect(() => {
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
        const initial = await api("snapshot");
        if (stopped) return;
        setState(initial);
        socket = new WebSocket(
          `${location.protocol === "https:" ? "wss" : "ws"}://${location.host}/api/events?cursor=${initial.cursor}`,
        );
        socket.onopen = () => setConnected(true);
        socket.onmessage = (e) => {
          performance.clearMarks("market-event-received");
          performance.mark("market-event-received");
          const message = JSON.parse(e.data);
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
        setNotice(String(e));
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
  const days = [
    ...new Set(
      state.contracts.filter((c) => c.venue === venue).map((c) => c.local_day),
    ),
  ].sort();
  const chosenDay = days.includes(day) ? day : (days[days.length - 1] ?? "");
  const markets = state.contracts.filter(
    (c) => c.venue === venue && c.local_day === chosenDay,
  );
  const contract =
    markets.find((c) => c.yes_token_id === selected) ?? markets[0];
  const token = contract?.yes_token_id ?? "";
  const book = state.books[token];
  const ticketBook =
    book && outcome === "NO"
      ? {
          ...book,
          bids: book.asks.map(([p, q]) => [1 - p, q]),
          asks: book.bids.map(([p, q]) => [1 - p, q]),
        }
      : book;
  const account = state.accounts.find(
    (a) => a.account === `sandbox-${venue}-knyc`,
  );
  const snapshot: Snapshot =
    mode === "sandbox" ? (account?.snapshot ?? {}) : {};
  const fresh = Boolean(
    connected &&
      book?.complete &&
      Date.now() / 1000 - book.received_at >= 0 &&
      Date.now() / 1000 - book.received_at <= 30 &&
      account?.status === "running" &&
      Date.now() / 1000 - account.updated < 10,
  );
  const canTrade =
    fresh &&
    mode === "sandbox" &&
    contract?.parse_status === "parsed" &&
    contract.fee_known;
  const health = state.health[venue];
  async function submit(kind: string, payload: Record<string, unknown> = {}) {
    if (pending) return;
    setPending(true);
    setNotice("提交中…");
    try {
      const receipt = await api(
        "commands",
        { request_id: crypto.randomUUID(), venue, mode, kind, payload },
        csrf,
      );
      setNotice(`已排队 · ${receipt.request_id.slice(0, 8)}，等待执行确认`);
      api("requests").then(setReceipts);
    } catch (e) {
      setNotice((e as Error).message);
    } finally {
      setPending(false);
    }
  }
  if (!session)
    return (
      <main className="login">
        <div className="brand">
          NW <span>NICE WEATHER</span>
        </div>
        <h1>交易终端</h1>
        <p>KNYC · Kalshi / Polymarket US</p>
        <form
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
        </form>
        <p role="alert">{notice}</p>
      </main>
    );
  return (
    <>
      <header>
        <div className="brand">
          NW <span>NICE WEATHER</span>
        </div>
        <nav>
          {["交易", "回测"].map((t) => (
            <button
              key={t}
              className={tab === t ? "active" : ""}
              onClick={() => setTab(t)}
            >
              {t}
            </button>
          ))}
        </nav>
        <div className="connection">
          <i className={connected ? "online" : ""} />
          {connected ? "终端已连接" : "重连中"} <span>{clock(now)}</span>
        </div>
      </header>
      <div className="toolbar">
        <label>
          平台
          <select
            value={venue}
            onChange={(e) => {
              setVenue(e.target.value);
              setSelected("");
            }}
          >
            <option value="kalshi">Kalshi</option>
            <option value="poly_us">Polymarket US</option>
          </select>
        </label>
        <label>
          站点
          <select aria-label="站点">
            <option>KNYC · Central Park</option>
          </select>
        </label>
        <label>
          市场日
          <select value={chosenDay} onChange={(e) => setDay(e.target.value)}>
            {days.map((d) => (
              <option key={d}>{d}</option>
            ))}
          </select>
        </label>
        <div className="mode">
          <button
            className={mode === "sandbox" ? "active" : ""}
            onClick={() => setMode("sandbox")}
          >
            模拟盘
          </button>
          <button
            className={mode === "live" ? "active" : ""}
            onClick={() => setMode("live")}
          >
            实盘
          </button>
        </div>
        <span className="feed-status">
          {health?.transport ?? "等待行情"}
          {health?.interval_seconds
            ? ` · ${health.interval_seconds}s`
            : ""} · {health?.status ?? "未连接"}
        </span>
      </div>
      {mode === "live" && (
        <div className="banner">实盘尚未启用 · {state.live.reason}</div>
      )}
      <div className="metrics">
        {[
          ["账户权益", money(snapshot.equity)],
          ["可用资金", money(snapshot.available)],
          ["总收益", money(snapshot.total_pnl)],
          [
            "结算来源",
            contract?.settlement_source === "weather_company"
              ? "The Weather Company"
              : "NWS Daily CLI",
          ],
        ].map(([label, value]) => (
          <div key={label}>
            <span>{label}</span>
            <strong>{value}</strong>
          </div>
        ))}
      </div>
      {tab === "交易" ? (
        <main className="workspace">
          <section className="market panel">
            <div className="section-title">
              <h2>KNYC 每日最高温</h2>
              <span>{chosenDay} · °F</span>
            </div>
            <div className="bins">
              {markets.map((c) => (
                <button
                  key={c.yes_token_id}
                  aria-pressed={token === c.yes_token_id}
                  className={token === c.yes_token_id ? "selected" : ""}
                  onClick={() => {
                    performance.mark("bin-select");
                    setSelected(c.yes_token_id);
                  }}
                >
                  <span>{c.title}°F</span>
                  <strong>
                    {price(state.books[c.yes_token_id]?.asks[0]?.[0])}
                  </strong>
                </button>
              ))}
            </div>
            <div className="section-title">
              <span>{contract?.title ?? "等待市场"} · YES 中间价</span>
              <span>行情收到 {clock(book?.received_at)}</span>
            </div>
            <Chart token={token} book={book} />
          </section>
          <section className="orderbook panel">
            <div className="section-title">
              <h2>订单簿 · {outcome}</h2>
              <span className={fresh ? "" : "warn"}>
                {fresh ? "有效" : "过期 / 未连接"}
              </span>
            </div>
            <div className="book-head">
              <span>价格</span>
              <span>份数</span>
            </div>
            {(ticketBook?.asks ?? [])
              .slice(0, 8)
              .reverse()
              .map(([p, q]) => (
                <button
                  className="level ask"
                  key={p}
                  onClick={() => setLimit(String(p))}
                >
                  <span>{price(p)}</span>
                  <span>{q.toFixed(2)}</span>
                </button>
              ))}
            <div className="spread">
              价差{" "}
              {ticketBook?.asks.length && ticketBook.bids.length
                ? price(ticketBook.asks[0][0] - ticketBook.bids[0][0])
                : "—"}
            </div>
            {(ticketBook?.bids ?? []).slice(0, 8).map(([p, q]) => (
              <button
                className="level bid"
                key={p}
                onClick={() => setLimit(String(p))}
              >
                <span>{price(p)}</span>
                <span>{q.toFixed(2)}</span>
              </button>
            ))}
          </section>
          <section className="ticket panel">
            <div className="section-title">
              <h2>下单</h2>
              <span>{mode === "sandbox" ? "模拟资金" : "实盘关闭"}</span>
            </div>
            <h3>{contract?.title ?? "选择合约"}°F</h3>
            <form
              onSubmit={(e) => {
                e.preventDefault();
                submit("order", {
                  token: token + (outcome === "NO" ? ":NO" : ""),
                  side,
                  quantity: Number(qty),
                  price: Number(limit),
                  tif,
                });
              }}
            >
              <div className="split">
                <label>
                  方向
                  <select
                    value={side}
                    onChange={(e) => setSide(e.target.value)}
                  >
                    <option value="BUY">买入</option>
                    <option value="SELL">卖出</option>
                  </select>
                </label>
                <label>
                  合约
                  <select
                    value={outcome}
                    onChange={(e) => {
                      setOutcome(e.target.value);
                      setLimit("");
                    }}
                  >
                    <option>YES</option>
                    <option>NO</option>
                  </select>
                </label>
              </div>
              <label>
                限价（美元）
                <input
                  type="number"
                  min="0.01"
                  max="0.99"
                  step="0.01"
                  value={limit}
                  onChange={(e) => setLimit(e.target.value)}
                  required
                />
              </label>
              <label>
                数量（份）
                <input
                  type="number"
                  min={contract?.minimum_order_size ?? 0.01}
                  step="0.01"
                  value={qty}
                  onChange={(e) => setQty(e.target.value)}
                  required
                />
              </label>
              <label>
                有效方式
                <select value={tif} onChange={(e) => setTif(e.target.value)}>
                  <option value="IOC">IOC · 即时成交，余量撤销</option>
                  <option value="GTC">GTC · 撤销前有效</option>
                  <option value="FOK">FOK · 全部成交或取消</option>
                </select>
              </label>
              <div className="estimate">
                <span>限价名义金额 · 未含费</span>
                <strong>{money(Number(qty) * Number(limit))}</strong>
              </div>
              <button className="primary" disabled={!canTrade || pending}>
                {pending
                  ? "提交中…"
                  : `${side === "BUY" ? "买入" : "卖出"} ${outcome}`}
              </button>
            </form>
            <p className="notice" role="status">
              {notice ||
                (!canTrade
                  ? "等待有效行情、账户与规则校验"
                  : "成交以订单回执为准")}
            </p>
          </section>
          <Weather weather={state.weather} day={chosenDay} />
          <section className="strategies panel">
            <div className="section-title">
              <h2>天气策略</h2>
              <div>
                <button
                  disabled={!fresh || mode !== "sandbox" || pending}
                  onClick={() =>
                    submit(snapshot.strategy_enabled ? "stop" : "start", {
                      strategy_id: "S1_S2_S3",
                    })
                  }
                >
                  {snapshot.strategy_enabled ? "停止策略" : "启动三策略"}
                </button>
              </div>
            </div>
            <div className="strategy-grid">
              {[
                ["S1", "相邻两档"],
                ["S2", "新高跨档"],
                ["S3", "结束后单档"],
              ].map(([id, name]) => {
                const signal = snapshot.signals?.[id];
                return (
                  <article key={id}>
                    <b>
                      {id} <span>{name}</span>
                    </b>
                    <p>{signal?.reason ?? "等待 KNYC 专属模型与有效输入"}</p>
                    {signal?.execution_reason && (
                      <p className="warn">
                        组合后续下单已停止；已成交部分保留。
                      </p>
                    )}
                    {signal?.executions?.map((leg: Record<string, any>) => (
                      <p key={leg.token}>
                        {markets.find((c) => c.yes_token_id === leg.token)
                          ?.title ?? leg.token}
                        {" · "}
                        {leg.filled} / {leg.requested} 份
                      </p>
                    ))}
                    <small>
                      {signal?.net_edge != null
                        ? `净优势 ${(signal.net_edge * 100).toFixed(2)}¢`
                        : "未产生可执行信号"}
                    </small>
                  </article>
                );
              })}
            </div>
            <div className="weather-line">
              实况 {clock(state.weather.metar?.received_at)} · CLI{" "}
              {state.weather.cli?.issued_at ?? "等待报告"} ·
              策略每日首次触发后不重试
            </div>
          </section>
          <section className="positions panel">
            <div className="section-title">
              <h2>持仓与挂单</h2>
              <span>{account?.account ?? "账户尚未启动"}</span>
            </div>
            <div className="table-wrap">
              <table>
                <thead>
                  <tr>
                    <th>合约</th>
                    <th>方向</th>
                    <th>数量</th>
                    <th>价格</th>
                    <th>状态 / 来源</th>
                    <th>操作</th>
                  </tr>
                </thead>
                <tbody>
                  {(snapshot.orders ?? []).map((o) => (
                    <tr key={o.order_id}>
                      <td title={o.token}>
                        {markets.find((c) => o.token.startsWith(c.yes_token_id))
                          ?.title ?? o.token}
                      </td>
                      <td>{o.side}</td>
                      <td>
                        {o.filled} / {o.quantity}
                      </td>
                      <td>{price(o.price)}</td>
                      <td>
                        {o.status} · {o.owner}
                      </td>
                      <td>
                        <button
                          disabled={
                            !fresh || o.remaining <= 0 || mode !== "sandbox"
                          }
                          onClick={() =>
                            submit("cancel", { order_id: o.order_id })
                          }
                        >
                          撤单
                        </button>
                      </td>
                    </tr>
                  ))}
                  {!snapshot.orders?.length && (
                    <tr>
                      <td colSpan={6} className="empty">
                        尚无订单
                      </td>
                    </tr>
                  )}
                </tbody>
              </table>
            </div>
            <div className="position-grid">
              {(snapshot.positions ?? []).map((p) => (
                <div key={p.token}>
                  <b>
                    {p.bin} · {p.outcome}
                  </b>
                  <span>{p.quantity} 份</span>
                  <span>浮动收益 {money(p.unrealized_pnl)}</span>
                </div>
              ))}
            </div>
            <div className="receipts">
              {receipts
                .filter((r) => r.account === account?.account)
                .slice(0, 3)
                .map((r) => (
                  <div key={r.request_id}>
                    {clock(r.created)} · {r.kind} · {r.status}
                    {r.error ? ` · ${r.error}` : ""}
                  </div>
                ))}
            </div>
          </section>
        </main>
      ) : (
        <main className="panel replay">
          <h2>历史回测与重放</h2>
          <p>使用实际接收时间和冻结策略规则；缺失盘口或模型时保留 no-trade。</p>
          <form
            className="replay-form"
            onSubmit={async (e) => {
              e.preventDefault();
              setNotice("回测排队中…");
              try {
                const start =
                  new Date(`${replayDay}T00:00:00-05:00`).getTime() / 1000;
                await api(
                  "commands",
                  {
                    request_id: crypto.randomUUID(),
                    venue,
                    mode: "backtest",
                    kind: "backtest",
                    payload: {
                      start,
                      end: Math.min(start + 86400, Date.now() / 1000),
                      strategy: replayStrategy,
                    },
                  },
                  csrf,
                );
                setNotice("回测任务已提交，在后台运行");
              } catch (e) {
                setNotice((e as Error).message);
              }
            }}
          >
            <label>
              气候日（UTC−5）
              <input
                type="date"
                value={replayDay}
                onChange={(e) => setReplayDay(e.target.value)}
                required
              />
            </label>
            <label>
              策略
              <select
                value={replayStrategy}
                onChange={(e) => setReplayStrategy(e.target.value)}
              >
                <option value="S1_S2_S3">S1 / S2 / S3</option>
                <option>S1</option>
                <option>S2</option>
                <option>S3</option>
              </select>
            </label>
            <button className="primary">运行回测</button>
          </form>
          <p role="status">{notice}</p>
          {state.accounts
            .filter((a) => a.mode === "backtest")
            .map((a) => (
              <article key={a.run_id}>
                <b>{a.run_id.slice(0, 12)}</b> · {a.status} · 权益{" "}
                {money(a.snapshot.equity)} · 成交{" "}
                {a.snapshot.fills?.length ?? 0}
              </article>
            ))}
          {!state.accounts.some((a) => a.mode === "backtest") && (
            <p className="empty">
              暂无已完成回测。KNYC 数据正在积累，历史接收时间不会回填。
            </p>
          )}
        </main>
      )}
      <footer>
        <span>NICE WEATHER / KNYC</span>
        <span>研究信号 · 收益尚未独立验证</span>
      </footer>
    </>
  );
}
createRoot(document.getElementById("root")!).render(<App />);
