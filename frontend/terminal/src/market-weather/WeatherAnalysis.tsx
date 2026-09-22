import React, { useEffect, useMemo, useRef, useState } from "react";
import { ColorType, createChart, CrosshairMode, LineSeries, type IChartApi,
  type ISeriesApi, type Time } from "lightweight-charts";
import { createBidirectionalSync, nonNullSegments, type RawPoint } from "../../../trading-chart/src/difference";
import { analysis, names, nyTime, observationLine, midpoint, views,
  type Quote, type WeatherHistory } from "./data";
import "./market-weather.css";

type Line = { name: string; unit: string; points: RawPoint[]; color: string; axis: string };
const colors = ["#28765c", "#8b5e00", "#734b98", "#216bb3", "#b14332", "#3730a3"];
function draw(chart: IChartApi, lines: Line[]) {
  const drawn: { api: ISeriesApi<"Line">; line: Line }[] = [];
  for (const line of lines) {
    for (const segment of nonNullSegments(line.points)) {
      const api = chart.addSeries(LineSeries, { color: line.color, lineWidth: 2,
        priceScaleId: line.axis, lastValueVisible: false, priceLineVisible: false,
        pointMarkersVisible: segment.length === 1,
        priceFormat: { type: "custom", formatter: (v: number) => `${v.toFixed(2)} ${line.unit}` } });
      api.setData(segment.map(p => ({ time: p.time as Time, value: p.value! })));
      drawn.push({ api, line });
    }
  }
  return drawn;
}

export function WeatherAnalysis({ data, quotes = [], token, loading = false, error = "",
  priceReason = "" }: { data?: WeatherHistory; quotes?: Quote[]; token: string;
    loading?: boolean; error?: string; priceReason?: string | null }) {
  const main = useRef<HTMLDivElement>(null), changes = useRef<HTMLDivElement>(null);
  const charts = useRef<IChartApi[]>([]);
  const rebuilding = useRef(false);
  const viewport = useRef<{ from: Time; to: Time } | null>(null);
  const [enabled, setEnabled] = useState(["metar", "hrrr", "price"]);
  const [forecast, setForecast] = useState("hrrr"), [view, setView] = useState(3);
  const [hover, setHover] = useState("移动十字线查看来源、时间和数值");
  const identity = data ? `${data.venue}/${data.day}` : "";
  const lines = useMemo(() => {
    if (!data) return [[], []] as Line[][];
    const weather = Object.entries(data.sources).filter(([source]) => enabled.includes(source))
      .map(([source, points], i) => ({ name: names[source], unit: "°F", axis: "left",
        color: colors[i % colors.length], points: observationLine(points, ["hrrr", "nws_forecast"].includes(source)) }));
    if (enabled.includes("price")) {
      const sorted = [...new Map(quotes.map(q => [Math.floor(q.time), q])).values()]
        .sort((a, b) => a.time - b.time);
      const points = sorted.flatMap((q, i): RawPoint[] => {
        const p = { time: Math.floor(q.time), value: midpoint(q), source: "平台公开盘口中间价",
          received_at: new Date(q.received_at * 1000).toISOString(), binId: token };
        return i && q.time - sorted[i - 1].time > 600
          ? [{ time: Math.floor(sorted[i - 1].time) + 600, value: null }, p] : [p];
      });
      weather.push({ name: "所选 bin 中间价", unit: "%", axis: "right", color: "#3730a3", points });
    }
    const diff = analysis(data, quotes, token, forecast, view).map((line, i) => ({ ...line,
      axis: line.unit === "°F" ? "left" : "right", color: colors[i + 3] }));
    return [weather, diff];
  }, [data, quotes, token, enabled, forecast, view]);

  useEffect(() => {
    const create = (node: HTMLDivElement) => createChart(node, { autoSize: true,
      layout: { background: { type: ColorType.Solid, color: "#ffffff" }, textColor: "#334155" },
      crosshair: { mode: CrosshairMode.Normal }, leftPriceScale: { visible: true },
      timeScale: { timeVisible: true, tickMarkFormatter: (t: Time) =>
        new Intl.DateTimeFormat("en-GB", { timeZone: "America/New_York", hour: "2-digit",
          minute: "2-digit", hourCycle: "h23" }).format(new Date(Number(t) * 1000)) },
      localization: { timeFormatter: (t: Time) => nyTime(Number(t)) } });
    const first = create(main.current!), second = create(changes.current!);
    charts.current = [first, second];
    viewport.current = null;
    if (data) for (const chart of charts.current) {
      const basis = chart.addSeries(LineSeries, { color: "transparent", priceScaleId: "basis",
        lastValueVisible: false, priceLineVisible: false, crosshairMarkerVisible: false });
      const begin = Math.min(data.start, Math.floor(data.as_of / 60) * 60);
      basis.setData(Array.from({ length: Math.ceil((data.end - begin) / 60) }, (_, i) =>
        ({ time: (begin + i * 60) as Time, value: 0 })));
    }
    const sync = createBidirectionalSync<{ from: Time; to: Time }>(
      r => first.timeScale().setVisibleRange(r), r => second.timeScale().setVisibleRange(r));
    first.timeScale().subscribeVisibleTimeRangeChange(range => {
      if (!rebuilding.current && range) { viewport.current = range; sync.fromLeft(range); }
    });
    second.timeScale().subscribeVisibleTimeRangeChange(range => {
      if (!rebuilding.current && range) { viewport.current = range; sync.fromRight(range); }
    });
    return () => { first.remove(); second.remove(); charts.current = []; };
  }, [identity]);

  useEffect(() => {
    if (!data || charts.current.length !== 2) return;
    const saved = viewport.current;
    rebuilding.current = true;
    const drawn = charts.current.map((chart, i) => draw(chart, lines[i]));
    let syncing = false;
    const handlers = charts.current.map((chart, i) => {
      const callback: Parameters<IChartApi["subscribeCrosshairMove"]>[0] = event => {
        if (syncing) return;
        if (!event.time) { charts.current[1 - i]?.clearCrosshairPosition(); return; }
        const time = Number(event.time);
        const details = lines[i].flatMap(line => {
          const point = line.points.filter(p => p.time <= time).at(-1);
          return point?.value == null ? [] : [`${line.name} ${point.value.toFixed(2)} ${line.unit}`
            + (point.received_at ? ` · 收到 ${nyTime(Date.parse(point.received_at) / 1000)}` : " · 接收时间缺失")
            + (point.object_time ? ` · 有效/观测 ${nyTime(Date.parse(point.object_time) / 1000)}` : "")];
        });
        setHover(`${nyTime(time)} · ${details.join("；") || "数据缺失"}`);
        const target = drawn[1 - i].find(item => item.line.points.some(p => p.time === time && p.value != null));
        syncing = true;
        if (target) charts.current[1 - i].setCrosshairPosition(
          target.line.points.find(p => p.time === time)!.value!, event.time, target.api);
        else charts.current[1 - i].clearCrosshairPosition();
        syncing = false;
      };
      chart.subscribeCrosshairMove(callback);
      return callback;
    });
    const range = saved ?? { from: Math.min(data.start, Math.floor(data.as_of / 60) * 60) as Time,
      to: (data.end - 60) as Time };
    for (const chart of charts.current) chart.timeScale().setVisibleRange(range);
    rebuilding.current = false;
    return () => { rebuilding.current = true; charts.current.forEach((chart, i) => {
      chart.unsubscribeCrosshairMove(handlers[i]);
      for (const item of drawn[i]) chart.removeSeries(item.api);
    }); };
  }, [lines, identity]);

  const hourly = observationLine(data?.sources.hourly_temp ?? [], false)
    .flatMap(point => point.value == null ? [] : [point.value]);
  return <section className="mw-analysis" aria-label="天气分析">
    <h2>KNYC 天气与行情{data ? ` · ${data.venue} · ${data.day}` : ""}</h2>
    {loading && <p role="status">读取所选市场数据…</p>}
    {error && <p role="alert">{error}</p>}
    <p>最近有效中间价：{quotes.length && midpoint(quotes.at(-1)!) != null
      ? `${midpoint(quotes.at(-1)!)!.toFixed(2)}% · 收到 ${nyTime(quotes.at(-1)!.received_at)}`
      : "缺失"}（市场报价，仅用于展示）</p>
    {quotes.length > 0 && <p>Bid：{quotes.at(-1)!.bids[0]?.[0] == null ? "缺失"
      : `${(quotes.at(-1)!.bids[0][0] * 100).toFixed(2)}%`} · Ask：{quotes.at(-1)!.asks[0]?.[0] == null
      ? "缺失" : `${(quotes.at(-1)!.asks[0][0] * 100).toFixed(2)}%`} ·
      报价收到：{nyTime(quotes.at(-1)!.received_at)}</p>}
    <div className="mw-controls">{[...Object.keys(names), "price"].map(source => <label key={source}>
      <input type="checkbox" checked={enabled.includes(source)} onChange={e => setEnabled(old =>
        e.target.checked ? [...old, source] : old.filter(s => s !== source))} />
      {names[source] ?? "bin 价格"}</label>)}
      <button onClick={() => { if (data) charts.current[0]?.timeScale().setVisibleRange({
        from: data.start as Time, to: (data.end - 1) as Time }); }}>重置市场日视野</button>
      <button onClick={() => charts.current[0]?.timeScale().fitContent()}>全部已收集时间</button>
    </div>
    <p>纽约当地时间 · 温度 °F · 价格 %。主图按观测/有效时刻显示已取得的最新版本；分钟分析仅使用当时已收到的数据。</p>
    <div className="mw-chart" ref={main} aria-label="天气与价格交互图" />
    <div className="mw-controls">{lines[0].map(line => <span key={line.name} style={{ color: line.color }}>
      {line.name} · {line.unit}</span>)}</div>
    <div className="mw-controls"><label>预报来源 <select aria-label="预报来源" value={forecast}
      onChange={e => setForecast(e.target.value)}><option value="hrrr">HRRR</option>
      <option value="nws_forecast">NWS 预报</option></select></label>
      <label>分析 <select aria-label="分钟分析" value={view} onChange={e => setView(Number(e.target.value))}>
        {views.map((label, i) => <option value={i} key={i}>{label}</option>)}</select></label></div>
    <div className="mw-chart mw-difference" ref={changes} aria-label="分钟变化交互图" />
    <div className="mw-controls">{lines[1].map(line => <span key={line.name} style={{ color: line.color }}>
      {line.name} · {line.unit}</span>)}</div>
    <output className="mw-hover" aria-live="polite">{hover}</output>
    {data && <>
      <p>观察窗口：{nyTime(data.start)} 至 {nyTime(data.end)} · {data.window_source}</p>
      <p>已采集官方小时最高温：{hourly.length
        ? `${Math.max(...hourly).toFixed(1)} °F` : "缺失"}
        （观测口径，CLI 见下方报告）</p>
      {data.missing_sources.length > 0 && <p>未采集来源：{data.missing_sources.map(s => names[s]).join("、")}。缺口不插值。</p>}
      {priceReason && <p>{priceReason}</p>}
      {!lines[1].some(line => line.points.some(p => p.value != null)) && <p>所选分析暂无可比较数据。</p>}
      <details><summary>CLI 报告（与小时最高温分别展示）· {data.cli.length} 个版本</summary>
        {data.cli.length ? data.cli.map((report, i) => <div key={i}>
          <p>发布 {report.issued_at} · 收到 {nyTime(report.received_at)} · {report.finality}</p>
          <pre>{report.text}</pre></div>) : <p>该市场日暂无 CLI 报告。</p>}</details>
    </>}
  </section>;
}
