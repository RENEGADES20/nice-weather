import { useEffect, useMemo, useRef, useState } from "react";
import { BacktestChart, EventDetails } from "./BacktestChart";
import { scopedEvents } from "./StrategyMarkers";
import { climateDay, daysBetween, money, nyTime, read, stages, statusText,
  type History, type Market, type Point, type Run, type RunDetail, type StrategyEvent, type Venue } from "./types";
import "./backtest.css";

const emptyEvents: StrategyEvent[] = [];
const busy = new Set(["queued", "starting", "running"]);
const metricLabels = { equity: "账户权益", total_pnl: "PnL", cash: "现金", market_value: "持仓估值" };

/** Mount inside the terminal's backtest tab. Authentication remains owned by the host. */
export function BacktestWorkspace({ csrf, apiBase = "/api", initialVenue = "kalshi" }: {
  csrf: string; apiBase?: string; initialVenue?: Venue;
}) {
  const [venue, setVenue] = useState<Venue>(initialVenue);
  const [startDay, setStartDay] = useState(climateDay(Date.now() / 1000 - 86400));
  const [endDay, setEndDay] = useState(startDay);
  const [strategy, setStrategy] = useState("S1_S2_S3");
  const [cash, setCash] = useState("100"), [fee, setFee] = useState("1"), [slip, setSlip] = useState("0");
  const [runs, setRuns] = useState<Run[]>([]), [runId, setRunId] = useState("");
  const [detail, setDetail] = useState<RunDetail | null>(null);
  const [error, setError] = useState(""), [listError, setListError] = useState("");
  const [pending, setPending] = useState(false), [refresh, setRefresh] = useState(0);
  const [dayChoice, setDayChoice] = useState(""), [tokenChoice, setTokenChoice] = useState("");
  const [market, setMarket] = useState<Market | null>(null), [marketError, setMarketError] = useState("");
  const [prices, setPrices] = useState<Point[]>([]), [priceStatus, setPriceStatus] = useState("");
  const [pricesKey, setPricesKey] = useState("");
  const [metric, setMetric] = useState<keyof typeof metricLabels>("equity");
  const [focus, setFocus] = useState<StrategyEvent | null>(null);
  const [eventLimit, setEventLimit] = useState(100);
  const submitLock = useRef(false);
  const selected = useRef(runId); selected.current = runId;
  const current = detail?.run_id === runId ? detail : null;

  useEffect(() => {
    const abort = new AbortController(); let timer: ReturnType<typeof setTimeout>;
    async function poll() {
      try {
        const rows = await read<Run[]>(apiBase, "backtests", abort.signal);
        if (abort.signal.aborted) return;
        setRuns(rows); setListError("");
        setRunId(previous => previous || rows[0]?.run_id || "");
      } catch (e) { if (!abort.signal.aborted) setListError(String(e)); }
      if (!abort.signal.aborted) timer = setTimeout(poll, 2000);
    }
    void poll();
    return () => { abort.abort(); clearTimeout(timer); };
  }, [apiBase, refresh]);

  useEffect(() => {
    setDetail(null); setError(""); setDayChoice(""); setFocus(null); setEventLimit(100);
    if (!runId) return;
    const abort = new AbortController(); let timer: ReturnType<typeof setTimeout>;
    async function poll() {
      try {
        const result = await read<RunDetail>(apiBase, `backtests/${encodeURIComponent(runId)}`, abort.signal);
        if (abort.signal.aborted || selected.current !== runId) return;
        if (result.run_id !== runId) throw new Error("回测响应身份不匹配");
        setDetail(result); setError("");
        if (busy.has(result.status)) timer = setTimeout(poll, 2000);
      } catch (e) {
        if (!abort.signal.aborted) { setError(String(e)); timer = setTimeout(poll, 3000); }
      }
    }
    void poll();
    return () => { abort.abort(); clearTimeout(timer); };
  }, [runId, apiBase]);

  const config = current?.config;
  const firstDay = config?.start_day ?? (config ? climateDay(config.start) : "");
  const lastDay = config?.end_day ?? (config ? climateDay(config.end - 0.001) : "");
  const days = useMemo(() => firstDay && lastDay ? daysBetween(firstDay, lastDay) : [], [firstDay, lastDay]);
  const day = days.includes(dayChoice) ? dayChoice : days[0] ?? "";
  const runVenue = config?.venue;

  useEffect(() => {
    setMarket(null); setMarketError(""); setPrices([]);
    if (!runVenue || !day) return;
    const abort = new AbortController();
    read<Market>(apiBase, `market-day?${new URLSearchParams({ venue: runVenue, day })}`, abort.signal)
      .then(result => {
        if (abort.signal.aborted) return;
        if (result.venue !== runVenue || result.day !== day) throw new Error("市场响应身份不匹配");
        setMarket(result);
      }).catch(e => { if (!abort.signal.aborted) setMarketError(String(e)); });
    return () => abort.abort();
  }, [apiBase, runId, runVenue, day]);
  const activeMarket = market && market.venue === runVenue && market.day === day ? market : null;
  const token = activeMarket?.contracts.some(c => c.yes_token_id === tokenChoice) ? tokenChoice :
    activeMarket?.contracts[0]?.yes_token_id ?? "";
  const from = config?.start, to = config?.end;
  const priceKey = `${runId}:${runVenue}:${day}:${token}`;
  useEffect(() => {
    setPrices([]); setPriceStatus("");
    if (!runVenue || !day || !token || from == null || to == null) return;
    const abort = new AbortController();
    async function load() {
      setPriceStatus("赔率加载中…");
      try {
        const rows: History["points"] = []; let before: number | null = null;
        do {
          const params = new URLSearchParams({ venue: runVenue!, day, token });
          if (before != null) params.set("before", String(before));
          const result = await read<History>(apiBase, `history?${params}`, abort.signal);
          if (abort.signal.aborted) return;
          if (result.venue !== runVenue || result.day !== day || result.token !== token)
            throw new Error("赔率响应身份不匹配");
          rows.push(...result.points);
          if (result.next_before != null && before != null && result.next_before >= before)
            throw new Error("赔率分页未前进");
          before = result.next_before ?? null;
        } while (before != null);
        const points = [...new Map(rows.map(r => [r.seq, r])).values()]
          .map(r => ({ ...r, time: r.received_at ?? r.time }))
          .filter(r => r.time >= from! && r.time <= to!)
          .sort((a, b) => a.time - b.time || a.seq - b.seq)
          .map(r => ({ time: r.time, value: r.valid === false || r.complete === false || r.gap || !r.bids.length || !r.asks.length
            || r.bids[0][0] > r.asks[0][0] ? null : (r.bids[0][0] + r.asks[0][0]) / 2 }));
        setPrices(points); setPricesKey(`${runId}:${runVenue}:${day}:${token}`);
        setPriceStatus(points.some(p => p.value != null) ? "" : "该档在本次回测区间没有有效赔率");
      } catch (e) { if (!abort.signal.aborted) setPriceStatus(String(e)); }
    }
    void load(); return () => abort.abort();
  }, [apiBase, runId, runVenue, day, token, from, to]);

  const events = current?.events ?? emptyEvents;
  const markerSelection = useMemo(() => runVenue ? { venue: runVenue, day, token } : undefined,
    [runVenue, day, token]);
  const markedEvents = useMemo(() => runVenue ? scopedEvents(events, runVenue, day, token) : [], [events, runVenue, day, token]);
  const curve = useMemo(() => {
    const points: Point[] = []; let previous: number | undefined;
    for (const p of current?.curve ?? []) {
      // Account observations are minute samples. Missing sampled minutes remain gaps.
      if (previous != null && Math.floor(p.time / 60) > Math.floor(previous / 60) + 1)
        points.push({ time: (Math.floor(previous / 60) + 1) * 60, value: null });
      points.push({ time: p.time, value: p[metric] ?? null }); previous = p.time;
    }
    return points;
  }, [current, metric]);
  const chartPrices = useMemo(() => pricesKey === priceKey ? prices : [], [pricesKey, priceKey, prices]);
  const visibleEvents = events.filter(e => e.stage !== "diagnostic");
  const diagnostics = [...new Set(events.filter(e => e.stage === "diagnostic").map(e => e.reason).filter(Boolean))];
  function locate(e: StrategyEvent) {
    if (e.day) setDayChoice(e.day);
    if (e.token) setTokenChoice(e.token);
    setFocus(e);
  }
  async function submit(event: React.FormEvent) {
    event.preventDefault();
    if (submitLock.current) return;
    if (startDay > endDay) { setError("结束市场日不能早于开始市场日"); return; }
    submitLock.current = true; setPending(true); setError("");
    try {
      // The backend resolves market-day windows. Browser timezone never determines them.
      const requestId = crypto.randomUUID();
      const response = await fetch(`${apiBase}/commands`, {
        method: "POST", credentials: "same-origin", headers: {
          "Content-Type": "application/json", "X-CSRF-Token": csrf,
        }, body: JSON.stringify({ request_id: requestId, venue, mode: "backtest", kind: "backtest",
          payload: { start_day: startDay, end_day: endDay, strategy, cash: Number(cash),
            simulation: { estimated_fee_rate: Number(fee) / 100, slippage_pp: Number(slip) } } }),
      });
      if (!response.ok) {
        const body = await response.json().catch(() => ({}));
        throw new Error(body.detail ?? `提交失败 (${response.status})`);
      }
      setRunId(requestId); setRefresh(n => n + 1);
    } catch (e) { setError(String(e)); }
    finally { submitLock.current = false; setPending(false); }
  }
  function download() {
    const url = URL.createObjectURL(new Blob([JSON.stringify(current, null, 2)], { type: "application/json" }));
    const a = document.createElement("a"); a.href = url; a.download = `backtest-${runId}.json`; a.click(); URL.revokeObjectURL(url);
  }
  const values = current?.snapshot;
  const settlement = { pending: "持仓待结算", settled: "已结算", "no-position": "无待结算持仓" };
  return <section className="bt-workspace" aria-label="回测工作区">
    <header className="bt-header"><div><h2>历史回测</h2><p>账户与成交来自同一次运行 · 图表时间为纽约时间</p></div></header>
    <form className="bt-controls" onSubmit={submit}>
      <label>平台<select value={venue} onChange={e => setVenue(e.target.value as Venue)}><option value="kalshi">Kalshi</option><option value="poly_us">Polymarket US</option></select></label>
      <label>开始市场日<input type="date" required value={startDay} onChange={e => setStartDay(e.target.value)} /></label>
      <label>结束市场日<input type="date" required min={startDay} value={endDay} onChange={e => setEndDay(e.target.value)} /></label>
      <label>策略<select value={strategy} onChange={e => setStrategy(e.target.value)}><option value="S1_S2_S3">S1 / S2 / S3</option>{["S1", "S2", "S3"].map(s => <option key={s}>{s}</option>)}</select></label>
      <label>初始资金（$）<input type="number" required min="0.01" max="1000000" step="0.01" value={cash} onChange={e => setCash(e.target.value)} /></label>
      <label>未知费用估算（%）<input type="number" required min="0" max="100" step="0.01" value={fee} onChange={e => setFee(e.target.value)} /></label>
      <label>滑点（百分点）<input type="number" required min="0" max="100" step="0.01" value={slip} onChange={e => setSlip(e.target.value)} /></label>
      <button type="submit" disabled={pending}>{pending ? "提交中…" : "运行回测"}</button>
    </form>
    <p className="bt-muted">已知费用使用平台规则；估算费率仅用于未知费用。单日回测选择相同起止日。</p>
    {(error || listError) && <p role="alert">{error || listError}</p>}
    <label className="bt-history">历史运行<select aria-label="历史运行" value={runId} onChange={e => setRunId(e.target.value)}>
      {!runId && <option value="">暂无运行</option>}
      {runId && !runs.some(r => r.run_id === runId) && <option value={runId}>本次运行 · 等待回执</option>}
      {runs.map(r => <option key={r.run_id} value={r.run_id}>{r.config.venue} · {r.config.start_day ?? climateDay(r.config.start)} · {r.config.strategy_id ?? r.config.strategy} · {statusText[r.status] ?? r.status} · {r.run_id.slice(0, 8)}</option>)}
    </select></label>
    {!current ? <p role="status">{runId ? "读取运行结果…" : "暂无回测，请选择参数运行。"}</p> : <div className="bt-result" data-run-id={current.run_id}>
      <div className="bt-result-title"><h3>{current.config.venue === "kalshi" ? "Kalshi" : "Polymarket US"} · {firstDay} — {lastDay}</h3><span role="status">{statusText[current.status] ?? current.status}</span></div>
      {current.error && <p role="alert">{current.error}</p>}
      <p>本次参数：{config?.strategy_id ?? config?.strategy} · 初始资金 {money(config?.cash)} · {config?.simulation ? `未知费用估算 ${(config.simulation.estimated_fee_rate * 100).toFixed(2)}% · 滑点 ${config.simulation.slippage_pp} 百分点` : "历史运行未记录费用/滑点设置"}</p>
      <div className="bt-metrics">{Object.entries(metricLabels).map(([key, name]) => <div key={key}><span>{name}</span><strong>{money(values?.[key as keyof typeof metricLabels])}</strong></div>)}<div><span>费用</span><strong>{money(values?.fees)}</strong></div><div><span>结算状态</span><strong>{settlement[current.settlement as keyof typeof settlement] ?? "尚未记录"}</strong></div></div>
      {!busy.has(current.status) && !events.some(e => e.stage === "fill") && <p className="bt-notice">本次无成交。{values?.prediction_events === 0 ? "本次区间缺少策略概率输入，无法验证策略触发。" : diagnostics.length ? `未触发原因：${diagnostics.join("、")}` : "查看信号、执行拒绝及数据覆盖情况。"}</p>}
      {current.snapshot.replay_audit && <p className="bt-muted">实际输入覆盖：{Object.entries(current.snapshot.replay_audit.inputs).map(([kind, coverage]) => `${kind} ${coverage.count} 条（${nyTime(coverage.first_received)} 至 ${nyTime(coverage.last_received)}，最大间隔 ${Math.round(coverage.max_gap_seconds)} 秒）`).join("；") || "尚无输入"}。采集间隔不代表连续完整覆盖。</p>}
      {!current.history_complete && <p className="bt-notice">此运行未保存完整账户曲线；仅展示已有记录。</p>}
      <div className="bt-section-title"><h3>全区间账户曲线</h3><label>曲线<select value={metric} onChange={e => setMetric(e.target.value as keyof typeof metricLabels)}>{Object.entries(metricLabels).map(([key, name]) => <option key={key} value={key}>{name}</option>)}</select></label></div>
      <BacktestChart points={curve} label={`${metricLabels[metric]}曲线`} selectionKey={`${runId}:${metric}`} />
      <div className="bt-section-title"><h3>市场赔率与策略事件</h3><label>图表市场日<select value={day} onChange={e => { setDayChoice(e.target.value); setFocus(null); }}>{days.map(d => <option key={d}>{d}</option>)}</select></label></div>
      {marketError && <p role="alert">{marketError}</p>}
      {!activeMarket && !marketError && <p role="status">市场加载中…</p>}
      <div className="bt-bins">{activeMarket?.contracts.map(c => <button key={c.yes_token_id} aria-pressed={c.yes_token_id === token} onClick={() => { setTokenChoice(c.yes_token_id); setFocus(null); }}>{c.title}</button>)}</div>
      {activeMarket?.contracts.length === 0 && <p>该市场日没有已采集合约。</p>}
      <p className="bt-muted">YES 中间价（美元/份）· 合约市场日 {day} · 观测窗口 {activeMarket?.observation_start ?? "未记录"} 至 {activeMarket?.observation_end ?? "未记录"}。赔率仅展示本次运行区间内记录，缺失报价不连接。</p>
      {priceStatus && <p role="status">{priceStatus}</p>}
      <BacktestChart points={chartPrices} label="市场赔率图" selectionKey={`${runId}:${day}:${token}`} selection={markerSelection} events={markedEvents} focus={focus?.day === day && focus.token === token ? focus : null} onLocate={locate} />
      {focus && <div className="bt-focus" role="status"><b>已定位事件</b> · <EventDetails event={focus} />{(!focus.token || !focus.day) && <p>该历史事件缺少市场日或 bin，无法在赔率图精确定位。</p>}</div>}
      <h3>信号与交易记录 <small>（{visibleEvents.length}）</small></h3>
      <div className="bt-table"><table><thead><tr><th>纽约时间</th><th>策略 / 阶段</th><th>市场日 / bin</th><th>交易信息</th><th>定位</th></tr></thead><tbody>
        {visibleEvents.slice(0, eventLimit).map(e => <tr key={e.id}><td>{nyTime(e.time)}</td><td>{e.strategy ?? "未记录"}<br />{stages[e.stage] ?? e.stage}</td><td>{e.day ?? "未记录"}<br />{e.token ?? "未记录"}</td><td><EventDetails event={e} /></td><td><button onClick={() => locate(e)} aria-label={`定位 ${e.id}`}>定位</button></td></tr>)}
        {!visibleEvents.length && <tr><td colSpan={5}>暂无预警、触发、订单或成交事件。</td></tr>}
      </tbody></table></div>
      {visibleEvents.length > eventLimit && <button onClick={() => setEventLimit(n => n + 100)}>显示更多记录</button>}
      <details className="bt-audit"><summary>工程详情与完整结果导出</summary><button onClick={download}>导出本次结果</button><pre>{JSON.stringify({ run_id: runId, config, audit: current.snapshot.replay_audit, diagnostics }, null, 2)}</pre></details>
    </div>}
  </section>;
}
