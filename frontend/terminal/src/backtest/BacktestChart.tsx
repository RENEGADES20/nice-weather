import { useEffect, useRef, useState } from "react";
import { createChart, LineSeries, type IChartApi, type ISeriesApi, type Time, type UTCTimestamp } from "lightweight-charts";
import { attachStrategyMarkers } from "./StrategyMarkers";
import { nyTime, money, numeric, percent, stages, type Point, type StrategyEvent, type Venue } from "./types";

const emptyEvents: StrategyEvent[] = [];

export function EventDetails({ event: e }: { event: StrategyEvent }) {
  return <span>{e.strategy ?? "未记录策略"} · {stages[e.stage] ?? e.stage} · {nyTime(e.time)}<br />
    价格 {money(e.price)} · 数量 {numeric(e.quantity)} · 概率 {percent(e.probability)}
    {e.p_end != null && <> · 结束概率 {percent(e.p_end)}</>}<br />
    费用 {money(e.fee)} · {e.side === "SELL" ? `净回款 ${money(e.proceeds)}` : `成本 ${money(e.cost)}`} · {e.reason ?? ""}
    {e.group_id && <><br />组合 {e.group_id.slice(0, 12)}</>}
    {e.time_basis === "source_time" && " · 来源时间（研究）"}</span>;
}

export function BacktestChart({ points, label, selectionKey, selection, events = emptyEvents, focus, onLocate }: {
  points: Point[]; label: string; selectionKey: string;
  selection?: { venue: Venue; day: string; token: string };
  events?: StrategyEvent[]; focus?: StrategyEvent | null; onLocate?: (e: StrategyEvent) => void;
}) {
  const host = useRef<HTMLDivElement>(null);
  const chart = useRef<IChartApi | null>(null);
  const lastKey = useRef("");
  const [hover, setHover] = useState<StrategyEvent[]>([]);
  const [cursor, setCursor] = useState("");
  const locate = useRef(onLocate); locate.current = onLocate;
  const activeFocus = focus && (!selection || (focus.venue === selection.venue &&
    focus.day === selection.day && focus.token === selection.token)) ? focus : null;
  useEffect(() => {
    const api = createChart(host.current!, {
      autoSize: true, height: 300,
      layout: { background: { color: "#ffffff" }, textColor: "#425466", fontSize: 12 },
      grid: { vertLines: { color: "#eef1f4" }, horzLines: { color: "#eef1f4" } },
      timeScale: { timeVisible: true, secondsVisible: false, minBarSpacing: 0.001,
        tickMarkFormatter: (time: number) => new Date(time * 1000).toLocaleString("zh-CN", {
          timeZone: "America/New_York", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", hour12: false,
        }) },
      localization: { timeFormatter: (time: Time) => nyTime(Number(time)), priceFormatter: money },
    });
    chart.current = api;
    return () => { api.remove(); chart.current = null; };
  }, []);
  useEffect(() => {
    const api = chart.current!;
    const range = api.timeScale().getVisibleRange();
    const created: ISeriesApi<"Line">[] = [];
    // Separate series actually break lines at missing values; whitespace alone connects them.
    const ordered = [...new Map(points.map(p => [p.time, p])).values()].sort((a, b) => a.time - b.time);
    let segment: { time: UTCTimestamp; value: number }[] = [];
    const flush = () => {
      if (!segment.length) return;
      const series = api.addSeries(LineSeries, { color: "#16816d", lineWidth: 2,
        lastValueVisible: false, priceLineVisible: false, pointMarkersVisible: segment.length === 1 });
      series.setData(segment); created.push(series); segment = [];
    };
    for (const p of ordered) {
      if (p.value == null || !Number.isFinite(p.value)) flush();
      else segment.push({ time: p.time as UTCTimestamp, value: p.value });
    }
    flush();
    let detach = () => {};
    if (selection && events.length) {
      // Invisible anchors place markers at their exact event times, including inside price gaps.
      const anchors = api.addSeries(LineSeries, { visible: true, lineVisible: false,
        crosshairMarkerVisible: false, lastValueVisible: false, priceLineVisible: false });
      const times = new Map<number, number>();
      for (const e of events) times.set(e.time, e.price ?? 0.5);
      anchors.setData([...times].sort(([a], [b]) => a - b).map(([time, value]) => ({ time: time as UTCTimestamp, value })));
      created.push(anchors);
      detach = attachStrategyMarkers(api, anchors, selection, events, e => locate.current?.(e));
    }
    const crosshair: Parameters<IChartApi["subscribeCrosshairMove"]>[0] = p => {
      setCursor(p.time == null ? "" : nyTime(Number(p.time)));
      setHover(p.time == null ? [] : events.filter(e => e.time === Number(p.time)));
    };
    api.subscribeCrosshairMove(crosshair);
    if (lastKey.current !== selectionKey || !range) api.timeScale().fitContent();
    else if (created.length) api.timeScale().setVisibleRange(range);
    lastKey.current = selectionKey;
    setHover([]); setCursor("");
    return () => {
      // The mount effect disposes the chart first when this component unmounts.
      if (chart.current !== api) return;
      detach(); api.unsubscribeCrosshairMove(crosshair); created.forEach(s => api.removeSeries(s));
    };
  }, [points, events, selectionKey, selection]);
  useEffect(() => {
    if (activeFocus && chart.current?.timeScale().getVisibleLogicalRange()) chart.current.timeScale().setVisibleRange({
      from: (activeFocus.time - 900) as UTCTimestamp, to: (activeFocus.time + 900) as UTCTimestamp,
    });
  }, [activeFocus, points, selectionKey]);
  return <div className="bt-chart-wrap">
    <div ref={host} className="bt-chart" role="img" aria-label={label} data-focus={activeFocus?.id ?? ""} />
    <div className="bt-crosshair" aria-live="polite">{cursor || "滚轮缩放 · 拖动平移 · 十字线查看纽约时间"}
      {hover.map(e => <div key={e.id}><EventDetails event={e} /></div>)}
    </div>
  </div>;
}
