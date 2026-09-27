import { Fragment, useCallback, useEffect, useId, useMemo, useRef, useState, type ReactNode } from "react";
import { EventDetails } from "./backtest/BacktestChart";
import { EventList } from "./EventHistory";
import { scopedEvents } from "./backtest/StrategyMarkers";
import { stages, type StrategyEvent, type Venue } from "./backtest/types";
import { marketProbability, nyTime, type Quote } from "./market-weather/data";

const ranges = { "1H": 3600, "6H": 21600, "1D": 86400, "1W": 604800, "1M": 2592000, ALL: 0 };
const percent = (value: number | null) => value == null ? "—" : `${(value * 100).toFixed(1)}%`;
const colors: Record<string, string> = { S1: "#8854ce", S2: "#dd8a13", S3: "#2671d8" };
const top = 20, bottom = 258, height = 310;
type Sample = { time: number; value: number | null; quote: Quote };
type MarkerGroup = { key: string; events: StrategyEvent[] };
const markerClock = new Intl.DateTimeFormat("zh-CN", { timeZone: "America/New_York",
  month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", second: "2-digit", hourCycle: "h23" });
const markerTime = (time: number) => markerClock.format(new Date(time * 1000));

export function groupEventMarkers(events: StrategyEvent[], start: number, end: number, columns: number): MarkerGroup[] {
  const visible = events.filter(e => e.time >= start && e.time <= end);
  if (visible.length <= columns * 2) return visible.map(e => ({ key: e.id, events: [e] }));
  const groups = new Map<string, MarkerGroup>();
  for (const event of visible) {
    const column = Math.min(columns - 1, Math.floor((event.time - start) / (end - start) * columns));
    const key = `${column}/${event.strategy ?? ""}/${event.stage}`;
    const group = groups.get(key);
    if (group) group.events.push(event); else groups.set(key, { key, events: [event] });
  }
  return [...groups.values()];
}


// Keep within-pixel extrema and gaps; plotting every 2-second capture adds no visible detail.
export function displaySamples(points: Sample[], start: number, end: number, columns = 942) {
  const result: Sample[] = []; let bucket: Sample[] = [], column = -1;
  const flush = () => {
    if (!bucket.length) return;
    const low = bucket.reduce((a, b) => a.value! <= b.value! ? a : b);
    const high = bucket.reduce((a, b) => a.value! >= b.value! ? a : b);
    result.push(...[...new Set([bucket[0], low, high, bucket.at(-1)!])].sort((a, b) => a.time - b.time));
    bucket = [];
  };
  for (const point of points) {
    const next = Math.floor((point.time - start) / (end - start) * columns);
    if (point.value == null) { flush(); result.push(point); continue; }
    if (next !== column || (bucket.length && point.time - bucket.at(-1)!.time > 600)) flush();
    column = next; bucket.push(point);
  }
  flush(); return result;
}

// Both the cursor and signal markers use the last value known at that time.
// Never attach a signal to a later quote, its model probability, or a fill price.
export function sampleAt(points: Sample[], time: number) {
  let lo = 0, hi = points.length;
  while (lo < hi) { const mid = (lo + hi) >>> 1; if (points[mid].time <= time) lo = mid + 1; else hi = mid; }
  const point = points[lo - 1];
  return point && time - point.time <= 600 ? point : undefined;
}

export function ProbabilityChart({ quotes, selection, title, endTime, events, focus, onLocate, loading }: {
  quotes: Quote[]; selection: { venue: Venue; day: string; token: string }; title: string;
  endTime: number; events: StrategyEvent[]; focus: StrategyEvent | null;
  onLocate: (event: StrategyEvent) => void; loading: boolean;
}) {
  const [range, setRange] = useState<keyof typeof ranges>("1D");
  const [cursor, setCursor] = useState<number | null>(null);
  const [selectedEvent, setSelectedEvent] = useState<StrategyEvent | null>(null);
  const [expandedGroup, setExpandedGroup] = useState<{ scope: string; key: string } | null>(null);
  const host = useRef<HTMLDivElement>(null);
  const [width, setWidth] = useState(1000);
  const right = width - 48;
  useEffect(() => {
    const observer = new ResizeObserver(([entry]) => setWidth(Math.max(200, entry.contentRect.width)));
    observer.observe(host.current!); return () => observer.disconnect();
  }, []);
  const clip = useId();
  const identity = `${selection.venue}/${selection.day}/${selection.token}`;
  useEffect(() => { setSelectedEvent(null); setExpandedGroup(null); }, [identity]);
  useEffect(() => { setExpandedGroup(null); }, [range]);
  useEffect(() => { setCursor(null); }, [identity, range]);
  const samples = useMemo(() => [...new Map(quotes.map(q => {
    const time = Math.max(q.time, q.received_at, q.probability_received_at ?? q.received_at);
    return [time, { time, value: marketProbability(q), quote: q }] as const;
  })).values()].sort((a, b) => a.time - b.time), [quotes]);
  const selected = useMemo(() => scopedEvents(events, selection.venue, selection.day, selection.token)
    .filter(e => e.stage !== "diagnostic").sort((a, b) => a.time - b.time), [events, identity]);
  const activeFocus = useMemo(() => focus && selected.some(e => e.id === focus.id) ? focus : null, [focus, selected]);
  const locateEvent = useCallback((event: StrategyEvent) => { setSelectedEvent(event); onLocate(event); }, [onLocate]);
  useEffect(() => { if (activeFocus) { setSelectedEvent(activeFocus);
    if (activeFocus.time < start || activeFocus.time > end) setRange("ALL"); } }, [activeFocus]);
  const end = endTime;
  const earliest = Math.min(samples[0]?.time ?? end - 86400, selected[0]?.time ?? end);
  const start = ranges[range] ? end - ranges[range] : Math.min(earliest, end - 60);
  const x = (time: number) => 8 + (time - start) / (end - start) * (right - 8);
  const y = (value: number) => bottom - value * (bottom - top);
  const visible = useMemo(() => {
    const before = sampleAt(samples, start);
    return [...(before ? [before] : []), ...samples.filter(p => p.time > start && p.time <= end)];
  }, [samples, start, end]);
  const paths = useMemo(() => {
    const result: string[] = []; let path = "", previous: Sample | undefined;
    for (const p of displaySamples(visible, start, end, right - 8)) {
      if (p.value == null) { if (path) result.push(path); path = ""; previous = undefined; continue; }
      if (!previous || p.time - previous.time > 600) {
        if (path) result.push(path);
        path = `M${x(p.time).toFixed(2)},${y(p.value).toFixed(2)}`;
      } else path += `H${x(p.time).toFixed(2)}V${y(p.value).toFixed(2)}`;
      previous = p;
    }
    if (path) result.push(path);
    return result;
  }, [visible, start, end, right]);
  const current = cursor == null ? visible.at(-1) : sampleAt(samples, cursor);
  const latest = visible.at(-1);
  const markerScope = `${identity}/${range}`;
  const groups = useMemo(() => groupEventMarkers(selected, start, end, Math.max(1, Math.floor((right - 8) / 24))),
    [selected, start, end, right]);
  const group = expandedGroup?.scope === markerScope ? groups.find(g => g.key === expandedGroup.key) : undefined;
  const markerNodes = useMemo(() => {
    const drawn = groups.map(g => ({ ...g, focused: g.events.length === 1 && g.events[0].id === activeFocus?.id }));
    if (activeFocus && groups.some(g => g.events.length > 1 && g.events.some(e => e.id === activeFocus.id))) {
      drawn.push({ key: `focus/${activeFocus.id}`, events: [activeFocus], focused: true });
    }
    const annotationLanes: number[][] = [], placedLabels: { x: number; y: number }[] = [];
    const anchors: ReactNode[] = [];
    const nodes = drawn.map((g, i) => {
      const e = g.events[0], last = g.events.at(-1)!;
      const value = sampleAt(samples, e.time)?.value;
      const lane = drawn.slice(Math.max(0, i - 3), i).filter(other => Math.abs(x(other.events[0].time) - x(e.time)) < 26).length;
      const px = x(e.time), labelY = value == null ? 0 : Math.max(top, y(value) - 14 - lane * 16);
      const annotated = value == null || placedLabels.some(p => Math.abs(p.x - px) < 96 && Math.abs(p.y - labelY) < 24);
      let annotationLane = 0;
      if (annotated) {
        // Share annotation rows with missing values; keep every original label clickable.
        annotationLane = annotationLanes.findIndex(row => row.every(other => Math.abs(other - px) >= 96));
        if (annotationLane < 0) { annotationLane = annotationLanes.length; annotationLanes.push([]); }
        annotationLanes[annotationLane].push(px);
      } else placedLabels.push({ x: px, y: labelY });
      const color = e.stage === "fill" ? "#09875b" : colors[e.strategy ?? ""] ?? "#766b8e";
      if (annotated && value != null) anchors.push(<circle key={g.key} className="probability-marker-anchor"
        data-event-id={e.id} cx={px} cy={y(value)} r={3} fill={color} pointerEvents="none" />);
      const label = `${e.strategy ?? "人工"} · ${stages[e.stage] ?? e.stage}`;
      const caption = g.events.length > 1 ? `${label} · ${g.events.length} 条 · ${markerTime(e.time)}—${markerTime(last.time)}`
        : `${label} ${markerTime(e.time)}`;
      return <Fragment key={g.key}>
        {annotated && value != null && <span className="probability-marker-guide" aria-hidden="true"
          style={{ position: "absolute", pointerEvents: "none", left: `${px / width * 100}%`, top: `${y(value) / height * 100}%`,
            height: `calc(${100 - y(value) / height * 100}% + ${10 + annotationLane * 20}px)`, borderLeft: `1px dotted ${color}`, opacity: .45 }} />}
        <button className={`probability-marker ${e.stage === "fill" ? "is-fill" : ""} ${g.focused ? "is-focused" : ""}`}
        data-event-count={g.events.length} data-stage={e.stage} data-focus-marker={g.focused}
        // Displaced labels retain their actual probability anchor; missing values have no anchor.
        style={{ left: `${px / width * 100}%`, top: annotated ? `calc(100% + ${10 + annotationLane * 20}px)`
          : `${labelY / height * 100}%`, color }}
        aria-label={caption} title={g.events.length > 1 ? caption : `${caption}${value == null ? " · 同期市场概率缺失" : ` · ${percent(value)}`}`}
        onClick={() => g.events.length > 1 ? setExpandedGroup({ scope: markerScope, key: g.key }) : locateEvent(e)}>
        <span aria-hidden="true">{e.stage === "fill" ? "◆" : "●"}</span><small>{e.strategy ?? "人工"}{g.events.length > 1 ? ` ×${g.events.length}` : ""}</small>
      </button></Fragment>;
    });
    return { nodes, anchors, annotationSpace: annotationLanes.length ? annotationLanes.length * 20 + 8 : 0 };
  }, [groups, activeFocus, samples, start, end, width, right, markerScope, locateEvent]);
  const event = selectedEvent && selected.some(e => e.id === selectedEvent.id) ? selectedEvent : null;
  const source = current?.quote.probability_source;
  const tickCount = width < 500 ? 3 : 5;
  const ticks = Array.from({ length: tickCount }, (_, i) => start + (end - start) * i / (tickCount - 1));
  return <div ref={host} className="probability-chart" aria-label="市场概率图">
    <div className="probability-summary">
      <div><span className="probability-bin"><i />{title} · YES</span>
        <strong>{percent(current?.value ?? null)}</strong></div>
      <span>{cursor == null ? "市场概率" : nyTime(cursor)}</span>
    </div>
    <div className="probability-plot" style={{ marginBottom: markerNodes.annotationSpace }}>
      <svg viewBox={`0 0 ${width} ${height}`} preserveAspectRatio="none" role="img"
        aria-label="市场赔率与策略标记" data-focus={activeFocus?.id ?? ""}
        tabIndex={0} aria-describedby={`${clip}-keyboard`}
        onKeyDown={e => { if (["ArrowLeft", "ArrowRight", "Home", "End", "Escape"].includes(e.key)) {
          e.preventDefault();
          setCursor(e.key === "Escape" ? null : e.key === "Home" ? start : e.key === "End" ? end :
            Math.max(start, Math.min(end, (cursor ?? end) + (e.key === "ArrowLeft" ? -60 : 60))));
        } }}
        data-start={start} data-end={end} data-range={range} data-selection={identity}
        onPointerMove={e => { const box = e.currentTarget.getBoundingClientRect();
          const px = (e.clientX - box.left) / box.width * width;
          setCursor(start + Math.min(1, Math.max(0, (px - 8) / (right - 8))) * (end - start)); }}
        onPointerLeave={() => setCursor(null)}>
        <defs><clipPath id={clip}><rect x={8} y={top - 6} width={right - 8} height={bottom - top + 12} /></clipPath></defs>
        {[0, .25, .5, .75, 1].map(p => <g key={p}>
          <line x1={8} x2={right} y1={y(p)} y2={y(p)} className="probability-grid" />
          <text x={right + 12} y={y(p) + 4}>{p * 100}%</text>
        </g>)}
        {ticks.map((t, i) => <text key={i} x={x(t)} y={295} textAnchor={i === 0 ? "start" : i === tickCount - 1 ? "end" : "middle"}>
          {new Date(t * 1000).toLocaleString("en-US", { timeZone: "America/New_York",
            ...(end - start > 86400 ? { month: "short", day: "numeric" } : { hour: "numeric", minute: "2-digit" }) })}
        </text>)}
        <g clipPath={`url(#${clip})`} className="probability-lines" key={`${identity}/${range}`}>
          {paths.map((d, i) => <path key={i} d={d} className="probability-line" data-testid="probability-line" />)}
          {visible.filter((p, i) => p.value != null && (i === 0 || visible[i - 1].value == null || p.time - visible[i - 1].time > 600))
            .map(p => <circle key={p.time} cx={x(p.time)} cy={y(p.value!)} r={2.5} fill="#3864ef" />)}
          {latest?.value != null && <g><circle cx={x(latest.time)} cy={y(latest.value)} r={12} fill="#3864ef" opacity=".09" />
            <circle cx={x(latest.time)} cy={y(latest.value)} r={4} fill="#3864ef" /></g>}
        </g>
        {cursor != null && <g><line x1={x(cursor)} x2={x(cursor)} y1={top} y2={bottom} stroke="#a0a8b6" strokeDasharray="3 4" />
          {current?.value != null && <circle cx={x(cursor)} cy={y(current.value)} r={4} fill="#3864ef" />}</g>}
        {markerNodes.anchors}
      </svg>
      {markerNodes.nodes}
      {!visible.some(p => p.value != null) && <div className="probability-empty" role="status">
        {loading ? "读取所选时间段概率…" : "该时间段尚无已采集市场概率"}</div>}
    </div>
    <div className="probability-controls"><span>{selection.day} · 纽约时间</span>
      <div role="group" aria-label="概率时间范围">{(Object.keys(ranges) as (keyof typeof ranges)[]).map(r =>
        <button key={r} aria-pressed={range === r} onClick={() => { setRange(r); setSelectedEvent(null); }}>{r === "ALL" ? "All" : r}</button>)}</div>
    </div>
    <span className="sr-only" id={`${clip}-keyboard`}>左右方向键按分钟查看概率，Home 和 End 跳到窗口起止，Escape 退出。</span>
    <div className="probability-evidence" aria-live="polite">
      {current?.value != null ? <>{source ?? "平台市场概率"}
        {current.quote.probability_time != null && <> · 来源 {nyTime(current.quote.probability_time)}</>}
        {current.quote.probability_received_at != null && <> · 收到 {nyTime(current.quote.probability_received_at)}</>}</>
        : "市场概率缺口保留；盘口买卖报价单独显示。"}
      <span>● 策略信号　◆ 实际成交</span>
    </div>
    {group && <div className="probability-group">
      <b>{group.events[0].strategy ?? "人工"} · {stages[group.events[0].stage] ?? group.events[0].stage} · {group.events.length} 条</b>
      <span> · {markerTime(group.events[0].time)}—{markerTime(group.events.at(-1)!.time)}</span>
      <button onClick={() => setExpandedGroup(null)} aria-label="关闭事件分组">关闭</button>
      <EventList key={`${markerScope}/${group.key}`} events={group.events} onLocate={locateEvent} label="图表分组事件" />
    </div>}
    {event && <div className="probability-event" role="status"><b>{event.strategy ?? "人工"} · {stages[event.stage] ?? event.stage}</b>
      <EventDetails event={event} /><span>同期市场概率：{percent(sampleAt(samples, event.time)?.value ?? null)}</span>
      <button onClick={() => setSelectedEvent(null)} aria-label="关闭信号详情">关闭</button></div>}
  </div>;
}
