import {
  ColorType,
  CrosshairMode,
  LineSeries,
  LineStyle,
  createChart,
  createSeriesMarkers,
  type IChartApi,
  type ISeriesApi,
  type ISeriesMarkersPluginApi,
  type LineData,
  type Time,
  type UTCTimestamp,
} from "lightweight-charts";
import { Expand, Eye, LocateFixed, RotateCcw, createIcons } from "lucide";
import { Streamlit, type RenderData } from "streamlit-component-lib";
import {
  alignToMinuteGrid,
  differencePoints,
  mergeRawPoints,
  zAnchor,
  type AlignableSeries,
  type AlignedPoint,
  type DifferencePoint,
  type RawPoint,
  type ZAnchor,
} from "./difference";
import { formatAxisTime } from "./series";
import "./redesign.css";

type SeriesSpec = AlignableSeries & {
  name: string;
  description: string;
  group: "Weather" | "Market";
  pane: "weather" | "market";
  format: "temperature" | "probability";
  color: string;
  lineStyle?: "solid" | "dashed" | "dotted";
  currentPrice?: {
    value: number;
    source: string;
    time?: string;
    received_at?: string;
  } | null;
};

type EventSpec = { id: string; type: string; time: number; title: string };
type Payload = {
  mode: "full" | "delta" | "feed";
  channelId: string;
  revision: string;
  signature?: string;
  timezone?: string;
  selectedBinId: string;
  referenceSeriesId: string;
  windowStart: number;
  windowEnd: number;
  series: SeriesSpec[];
  events?: EventSpec[];
};

const root = document.querySelector<HTMLElement>("#app")!;
const seriesApis = new Map<string, ISeriesApi<"Line">>();
const seriesData = new Map<string, RawPoint[]>();
const differenceApis = new Map<string, ISeriesApi<"Line">>();
const differenceData = new Map<string, DifferencePoint[]>();
const aligned = new Map<string, AlignedPoint[]>();
const anchors = new Map<string, ZAnchor | null>();
let mainChart: IChartApi | null = null;
let differenceChart: IChartApi | null = null;
let mainTimeBasis: ISeriesApi<"Line"> | null = null;
let differenceTimeBasis: ISeriesApi<"Line"> | null = null;
let payload: Payload | null = null;
let signature = "";
let referenceSeriesId = "forecast";
let referenceUserSelected = false;
let eventsVisible = false;
let markers: ISeriesMarkersPluginApi<Time> | null = null;
let channel: BroadcastChannel | null = null;
let channelId = "";
let rangeSyncReady = false;
let mainRangeKey = "";
let differenceRangeKey = "";
let syncingCrosshair = false;
let crosshairTime: Time | undefined;
let following = true;
let chartPointerActive = false;
let pendingDelta: Payload | null = null;

const style = (value?: string): LineStyle => value === "dashed"
  ? LineStyle.Dashed
  : value === "dotted" ? LineStyle.Dotted : LineStyle.Solid;

function chartOptions() {
  return {
    autoSize: true,
    layout: {
      background: { type: ColorType.Solid, color: "#FFFFFF" },
      textColor: "#667085",
      attributionLogo: false,
      panes: { separatorColor: "#E2E7EE", separatorHoverColor: "#93C5FD", enableResize: true },
    },
    grid: { vertLines: { color: "#EDF1F5" }, horzLines: { color: "#EDF1F5" } },
    crosshair: { mode: CrosshairMode.Normal },
    leftPriceScale: { visible: true, borderColor: "#E2E7EE" },
    rightPriceScale: { visible: true, borderColor: "#E2E7EE" },
    timeScale: {
      borderColor: "#E2E7EE",
      timeVisible: true,
      secondsVisible: false,
      tickMarkFormatter: (time: Time, type: number) => formatAxisTime(
        Number(time), type, "America/New_York",
      ),
    },
    handleScroll: { mouseWheel: true, pressedMouseMove: true, horzTouchDrag: true },
    handleScale: { mouseWheel: true, pinch: true, axisPressedMouseMove: true },
  };
}

function escapeHtml(value: string): string {
  return value.replace(/[&<>'"]/g, (character) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", "'": "&#39;", '"': "&quot;",
  })[character]!);
}

function formatRaw(value: number, unit?: string): string {
  if (unit === "probability") return `${(value * 100).toFixed(0)}%`;
  return `${value.toFixed(1)}${unit || ""}`;
}

function buildShell(): void {
  root.innerHTML = `
    <div class="shell" id="shell">
      <div class="toolbar">
        <button id="events-button" class="toolbar-command" aria-pressed="false"><i data-lucide="eye"></i><span>Events</span></button>
        <span class="price-readout" id="price-readout">Price Unavailable</span>
        <span class="spacer"></span>
        <button id="follow-button" class="tool-button" title="Follow latest"><i data-lucide="locate-fixed"></i></button>
        <button id="reset-button" class="tool-button" title="Reset view"><i data-lucide="rotate-ccw"></i></button>
        <button id="fullscreen-button" class="tool-button" title="Full screen"><i data-lucide="expand"></i></button>
      </div>
      <div id="channel-warning" class="notice hidden">Live updates are unavailable in this browser. Manual controls remain available.</div>
      <div class="legend" id="main-legend"></div>
      <div id="main-chart"></div>
      <div class="difference-title"><strong>Difference</strong><span>Zero means equal relative position within each series window.</span></div>
      <div class="legend reference-legend" id="difference-legend"></div>
      <div class="difference-wrap"><div id="difference-chart"></div><div id="difference-empty" class="empty hidden">Insufficient overlapping data</div></div>
      <div class="disclaimer">Difference shows co-movement and divergence only. It does not establish causality or true response delay.</div>
    </div>`;
  createIcons({ icons: { Expand, Eye, LocateFixed, RotateCcw } });
  mainChart = createChart(document.querySelector<HTMLElement>("#main-chart")!, chartOptions());
  mainChart.panes()[0].setStretchFactor(65);
  mainChart.addPane().setStretchFactor(35);
  differenceChart = createChart(
    document.querySelector<HTMLElement>("#difference-chart")!, chartOptions(),
  );
  root.dataset.feedPaused = "false";
  mainChart.timeScale().subscribeVisibleLogicalRangeChange((range) => {
    if (!range) return;
    mainRangeKey = `${range.from}:${range.to}`;
    root.dataset.mainRange = mainRangeKey;
    if (!rangeSyncReady || mainRangeKey === differenceRangeKey) return;
    differenceRangeKey = mainRangeKey;
    differenceChart?.timeScale().setVisibleLogicalRange(range);
  });
  differenceChart.timeScale().subscribeVisibleLogicalRangeChange((range) => {
    if (!range) return;
    differenceRangeKey = `${range.from}:${range.to}`;
    root.dataset.differenceRange = differenceRangeKey;
    if (!rangeSyncReady || differenceRangeKey === mainRangeKey) return;
    mainRangeKey = differenceRangeKey;
    mainChart?.timeScale().setVisibleLogicalRange(range);
  });
  mainChart.subscribeCrosshairMove((param) => {
    root.dataset.mainCrosshair = param.time == null ? "" : String(param.time);
    renderMainLegend(param.time, param.seriesData);
    if (!syncingCrosshair) {
      crosshairTime = param.time;
      syncCrosshair(param.time, true);
    }
  });
  differenceChart.subscribeCrosshairMove((param) => {
    root.dataset.differenceCrosshair = param.time == null ? "" : String(param.time);
    renderDifferenceLegend(param.time, param.seriesData);
    if (!syncingCrosshair) {
      crosshairTime = param.time;
      syncCrosshair(param.time, false);
    }
  });
  document.querySelector("#events-button")?.addEventListener("click", () => {
    eventsVisible = !eventsVisible;
    document.querySelector("#events-button")?.setAttribute("aria-pressed", String(eventsVisible));
    renderMarkers();
  });
  document.querySelector("#follow-button")?.addEventListener("click", () => {
    following = true;
    mainChart?.timeScale().scrollToRealTime();
  });
  document.querySelector("#main-chart")?.addEventListener("pointerdown", () => { following = false; });
  for (const selector of ["#main-chart", "#difference-chart"]) {
    const target = document.querySelector(selector);
    target?.addEventListener("pointerenter", () => {
      chartPointerActive = true;
      root.dataset.feedPaused = "true";
    });
    target?.addEventListener("pointerleave", () => flushPendingDelta());
    target?.addEventListener("pointercancel", () => flushPendingDelta());
    target?.addEventListener("pointerup", (event) => {
      if ((event as PointerEvent).pointerType !== "mouse") flushPendingDelta();
    });
  }
  document.querySelector("#reset-button")?.addEventListener("click", () => {
    following = false;
    mainChart?.timeScale().fitContent();
  });
  document.querySelector("#fullscreen-button")?.addEventListener("click", () => {
    void document.querySelector<HTMLElement>("#shell")?.requestFullscreen();
  });
}

function setupChannel(nextId: string): void {
  if (nextId === channelId) return;
  channel?.close();
  channelId = nextId;
  if (!("BroadcastChannel" in window)) {
    document.querySelector("#channel-warning")?.classList.remove("hidden");
    return;
  }
  document.querySelector("#channel-warning")?.classList.add("hidden");
  channel = new BroadcastChannel(`nice-weather-${nextId}`);
  channel.addEventListener("message", (event) => applyPayload(event.data as Payload));
}

function seriesOptions(spec: SeriesSpec) {
  return {
    title: "",
    color: spec.color,
    lineWidth: spec.id === "price" ? 3 as const : 2 as const,
    lineStyle: style(spec.lineStyle),
    priceScaleId: spec.pane === "weather" ? "left" : "right",
    priceLineVisible: false,
    lastValueVisible: false,
    crosshairMarkerVisible: true,
    priceFormat: spec.format === "probability"
      ? { type: "custom" as const, minMove: 0.001, formatter: (v: number) => `${(v * 100).toFixed(0)}%` }
      : { type: "price" as const, precision: 1, minMove: 0.1 },
  };
}

function lineData(point: RawPoint) {
  return point.value == null
    ? { time: point.time as UTCTimestamp }
    : { time: point.time as UTCTimestamp, value: point.value };
}

function reconcileFull(specs: SeriesSpec[]): void {
  if (!mainChart) return;
  const ids = new Set(specs.map((spec) => spec.id));
  for (const [id, api] of seriesApis) {
    if (ids.has(id)) continue;
    mainChart.removeSeries(api);
    seriesApis.delete(id);
    seriesData.delete(id);
  }
  for (const spec of specs) {
    let api = seriesApis.get(spec.id);
    if (!api) {
      api = mainChart.addSeries(LineSeries, seriesOptions(spec), spec.pane === "market" ? 1 : 0);
      seriesApis.set(spec.id, api);
    } else api.applyOptions(seriesOptions(spec));
    api.setData(spec.points.map(lineData));
    seriesData.set(spec.id, [...spec.points].sort((a, b) => a.time - b.time));
  }
}

function reconcileDelta(specs: SeriesSpec[]): void {
  for (const spec of specs) {
    const api = seriesApis.get(spec.id);
    if (!api) continue;
    const previous = seriesData.get(spec.id) || [];
    const previousByTime = new Map(previous.map((point) => [point.time, point.value]));
    const merged = mergeRawPoints(previous, spec.points);
    for (const point of spec.points) {
      if (previousByTime.has(point.time) && Object.is(previousByTime.get(point.time), point.value)) {
        continue;
      }
      const historical = previous.length > 0 && point.time < previous.at(-1)!.time;
      api.update(lineData(point), historical);
    }
    seriesData.set(spec.id, merged);
  }
}

function windowBounds(): { start: number; end: number } | null {
  const times = [...seriesData.values()].flatMap((points) => points.map((point) => point.time));
  return times.length ? { start: Math.min(...times), end: Math.max(...times) } : null;
}

function rebuildAlignment(resetAnchors: boolean): void {
  if (!payload) return;
  const bounds = windowBounds();
  if (!bounds) return;
  for (const spec of payload.series) {
    const points = alignToMinuteGrid({ ...spec, points: seriesData.get(spec.id) || [] }, bounds.start, bounds.end);
    aligned.set(spec.id, points);
    if (resetAnchors) anchors.set(spec.id, zAnchor(points));
  }
}

function differenceOptions(spec: SeriesSpec) {
  return {
    title: "", color: spec.color, lineWidth: 2 as const,
    priceLineVisible: false, lastValueVisible: false,
    priceFormat: { type: "custom" as const, minMove: 0.01, formatter: (v: number) => `${v >= 0 ? "+" : ""}${v.toFixed(2)}σ` },
  };
}

function setTimeBasis(start: number, end: number): void {
  if (!mainChart || !differenceChart) return;
  const points: LineData[] = [];
  for (let current = Math.floor(start / 60) * 60; current <= end; current += 60) {
    points.push({ time: current as UTCTimestamp, value: 0 });
  }
  const options = { visible: false, priceLineVisible: false, lastValueVisible: false };
  if (!mainTimeBasis) mainTimeBasis = mainChart.addSeries(LineSeries, options, 0);
  if (!differenceTimeBasis) differenceTimeBasis = differenceChart.addSeries(LineSeries, options);
  mainTimeBasis.setData(points);
  differenceTimeBasis.setData(points);
}

function renderDifferences(fullReplace: boolean): void {
  if (!payload || !differenceChart) return;
  const reference = aligned.get(referenceSeriesId) || [];
  const validIds = new Set<string>();
  for (const spec of payload.series) {
    if (spec.id === referenceSeriesId) continue;
    const points = differencePoints(
      aligned.get(spec.id) || [], reference, anchors.get(spec.id) || null,
      anchors.get(referenceSeriesId) || null,
    );
    if (!points) continue;
    validIds.add(spec.id);
    let api = differenceApis.get(spec.id);
    if (!api) {
      api = differenceChart.addSeries(LineSeries, differenceOptions(spec));
      differenceApis.set(spec.id, api);
    }
    const previous = differenceData.get(spec.id) || [];
    if (fullReplace) api.setData(points.map((point) => ({ time: point.time as UTCTimestamp, value: point.value })));
    else {
      const previousByTime = new Map(previous.map((point) => [point.time, point.value]));
      for (const point of points) {
        if (previousByTime.get(point.time) === point.value) continue;
        api.update(
          { time: point.time as UTCTimestamp, value: point.value },
          previous.length > 0 && point.time < previous.at(-1)!.time,
        );
      }
    }
    differenceData.set(spec.id, points);
  }
  for (const [id, api] of differenceApis) {
    if (validIds.has(id)) continue;
    differenceChart.removeSeries(api);
    differenceApis.delete(id);
    differenceData.delete(id);
  }
  document.querySelector("#difference-empty")?.classList.toggle("hidden", validIds.size > 0);
  renderDifferenceLegend();
}

function latestPoint(id: string): RawPoint | undefined {
  return seriesData.get(id)?.filter((point) => point.value !== null).at(-1);
}

function renderPrice(): void {
  const point = payload?.series.find((spec) => spec.id === "price")?.currentPrice;
  const target = document.querySelector<HTMLElement>("#price-readout")!;
  if (!point) {
    target.textContent = "Price Unavailable";
    return;
  }
  const receivedAt = Date.parse(point.received_at || point.time || "") / 1000;
  const age = receivedAt ? Math.max(0, Math.round(Date.now() / 1000 - receivedAt)) : 0;
  target.textContent = `Price ${(point.value * 100).toFixed(1)}% · ${point.source} · ${age < 60 ? `${age}s` : `${Math.floor(age / 60)}m`} old`;
}

function renderMainLegend(time?: Time, values?: ReadonlyMap<unknown, unknown>): void {
  if (!payload) return;
  const stamp = time == null ? "ET" : `${formatAxisTime(Number(time), 3, "America/New_York")} ET`;
  const items = payload.series.map((spec) => {
    const api = seriesApis.get(spec.id);
    const crosshair = api && values ? values.get(api) as LineData | undefined : undefined;
    const point = crosshair && "value" in crosshair ? crosshair.value : latestPoint(spec.id)?.value;
    const value = point == null ? "Unavailable" : formatRaw(point, spec.format === "probability" ? "probability" : "°F");
    return `<span class="legend-item"><i style="background:${spec.color}"></i>${escapeHtml(spec.name)} <button class="info" title="${escapeHtml(spec.description)}">i</button><strong>${value}</strong></span>`;
  }).join("");
  document.querySelector<HTMLElement>("#main-legend")!.innerHTML = `<span class="legend-time">${stamp}</span>${items}`;
}

function renderDifferenceLegend(time?: Time, values?: ReadonlyMap<unknown, unknown>): void {
  if (!payload) return;
  const radios = payload.series.map((spec) => `
    <label><input type="radio" name="reference" value="${escapeHtml(spec.id)}" ${spec.id === referenceSeriesId ? "checked" : ""}/><i style="background:${spec.color}"></i>${escapeHtml(spec.name)}</label>`).join("");
  let detail = "";
  if (time != null && values) {
    for (const spec of payload.series) {
      const api = differenceApis.get(spec.id);
      const line = api ? values.get(api) as LineData | undefined : undefined;
      const point = differenceData.get(spec.id)?.find((item) => item.time === Number(time));
      if (!line || !("value" in line) || !point) continue;
      const reference = payload.series.find((item) => item.id === referenceSeriesId)!;
      detail = `${spec.name} ${formatRaw(point.rawValue, point.rawUnit)} / ${reference.name} ${formatRaw(point.referenceRawValue, point.referenceRawUnit)} / Difference ${line.value >= 0 ? "+" : ""}${line.value.toFixed(2)}σ`;
      break;
    }
  }
  const target = document.querySelector<HTMLElement>("#difference-legend")!;
  target.innerHTML = `<span class="reference-label">Reference</span>${radios}<strong>${escapeHtml(detail)}</strong>`;
  target.querySelectorAll<HTMLInputElement>("input[name='reference']").forEach((input) => {
    input.addEventListener("change", () => {
      referenceSeriesId = input.value;
      referenceUserSelected = true;
      renderDifferences(true);
      Streamlit.setComponentValue({ referenceSeriesId });
    });
  });
}

function renderMarkers(): void {
  if (!payload) return;
  const anchor = payload.series.find((spec) => spec.group === "Weather");
  const api = anchor ? seriesApis.get(anchor.id) : undefined;
  if (!api) return;
  const items = eventsVisible ? (payload.events || []).map((event) => ({
    id: event.id, time: event.time as UTCTimestamp, position: "aboveBar" as const,
    color: "#667085", shape: "circle" as const, text: "", size: 0.6,
  })) : [];
  if (markers) markers.setMarkers(items);
  else markers = createSeriesMarkers(api, items);
}

function syncCrosshair(time: Time | undefined, fromMain: boolean): void {
  const target = fromMain ? differenceChart : mainChart;
  const apis = fromMain ? differenceApis : seriesApis;
  if (!target || time == null) {
    target?.clearCrosshairPosition();
    return;
  }
  syncingCrosshair = true;
  const basis = fromMain ? differenceTimeBasis : mainTimeBasis;
  if (basis) {
    target.setCrosshairPosition(0, time, basis);
    if (fromMain) root.dataset.differenceCrosshair = String(time);
    else root.dataset.mainCrosshair = String(time);
    syncingCrosshair = false;
    return;
  }
  const values = fromMain ? differenceData : aligned;
  for (const [id, api] of apis) {
    const point = values.get(id)?.find((item) => item.time === Number(time));
    if (!point || !Number.isFinite(point.value)) continue;
    target.setCrosshairPosition(point.value, time, api);
    break;
  }
  syncingCrosshair = false;
}

function restoreCrosshair(time: number, onDifference: boolean): boolean {
  const chart = onDifference ? differenceChart : mainChart;
  const apis = onDifference ? differenceApis : seriesApis;
  const values = onDifference ? differenceData : aligned;
  if (!chart) return false;
  try {
    const basis = onDifference ? differenceTimeBasis : mainTimeBasis;
    if (basis) {
      chart.setCrosshairPosition(0, time as UTCTimestamp, basis);
      return true;
    }
    for (const [id, api] of apis) {
      const point = values.get(id)?.find((item) => item.time === time);
      if (!point || !Number.isFinite(point.value)) continue;
      chart.setCrosshairPosition(point.value, time as UTCTimestamp, api);
      return true;
    }
  } catch {
    // Lightweight Charts 5.2 can retain a stale hovered row until a later paint after
    // series.update(). Retry after another frame without leaking its internal null error.
    return false;
  }
  return false;
}

function restoreCrosshairsAfterPaint(time: number, attempt = 0): void {
  requestAnimationFrame(() => requestAnimationFrame(() => {
    const mainRestored = restoreCrosshair(time, false);
    const differenceRestored = restoreCrosshair(time, true);
    if ((!mainRestored || !differenceRestored) && attempt < 2) {
      window.setTimeout(() => restoreCrosshairsAfterPaint(time, attempt + 1), 32);
      return;
    }
    root.dataset.mainCrosshair = String(time);
    root.dataset.differenceCrosshair = String(time);
    crosshairTime = time as UTCTimestamp;
    syncingCrosshair = false;
  }));
}

function updateIncrementally(next: Payload): void {
  const savedCrosshair = crosshairTime == null ? undefined : Number(crosshairTime);

  // Lightweight Charts can reuse stale hovered pane rows while several series update and the
  // time scale changes. Clear the transient cursor and allow that invalidation to paint before
  // the batch, then restore it after later paints. This keeps deltas on series.update() while
  // avoiding the library's asynchronous `Value is null` race in its hovered pane cache.
  syncingCrosshair = true;
  mainChart?.clearCrosshairPosition();
  differenceChart?.clearCrosshairPosition();
  requestAnimationFrame(() => {
    reconcileDelta(next.series);
    rebuildAlignment(false);
    renderDifferences(false);
    if (following) mainChart?.timeScale().scrollToRealTime();

    if (savedCrosshair != null && Number.isFinite(savedCrosshair)) {
      restoreCrosshairsAfterPaint(savedCrosshair);
    }
    else syncingCrosshair = false;
  });
}

function flushPendingDelta(): void {
  chartPointerActive = false;
  root.dataset.feedPaused = "false";
  const next = pendingDelta;
  pendingDelta = null;
  if (next) applyPayload(next);
  root.dataset.pendingRevision = "";
}

function applyPayload(next: Payload): void {
  if (next.channelId !== channelId || next.mode === "feed") return;
  if (next.mode === "delta" && chartPointerActive) {
    pendingDelta = next;
    root.dataset.pendingRevision = String(next.revision);
    return;
  }
  const signatureChanged = signature !== next.signature;
  payload = { ...payload, ...next, mode: next.mode, series: next.series };
  if (!referenceUserSelected || (next.mode === "full" && signatureChanged)) {
    referenceSeriesId = next.referenceSeriesId || referenceSeriesId;
    if (next.mode === "full" && signatureChanged) referenceUserSelected = false;
  }
  if (next.mode === "full") {
    const changed = signatureChanged;
    if (changed) {
      rangeSyncReady = false;
      signature = next.signature || "";
      setTimeBasis(next.windowStart, next.windowEnd);
      reconcileFull(next.series);
      rebuildAlignment(true);
      renderDifferences(true);
      rangeSyncReady = true;
      mainChart?.timeScale().fitContent();
    }
  } else updateIncrementally(next);
  renderPrice();
  renderMainLegend();
  renderMarkers();
  root.dataset.appliedRevision = String(next.revision);
}

function render(data: RenderData): void {
  const next = data.args.payload as Payload;
  if (next.mode === "feed") {
    root.replaceChildren();
    root.style.display = "none";
    if ("BroadcastChannel" in window) {
      const feed = new BroadcastChannel(`nice-weather-${next.channelId}`);
      feed.postMessage({ ...next, mode: "delta" });
      feed.close();
    }
    return;
  }
  root.style.display = "block";
  if (!mainChart) buildShell();
  setupChannel(next.channelId);
  applyPayload(next);
}

Streamlit.events.addEventListener(Streamlit.RENDER_EVENT, (event) => {
  render((event as CustomEvent<RenderData>).detail);
});
Streamlit.setComponentReady();
