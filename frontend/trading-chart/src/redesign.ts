import {
  ColorType,
  CrosshairMode,
  LineSeries,
  LineStyle,
  LineType,
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
  differencePoints,
  mergeRawPoints,
  nonNullSegments,
  type DifferencePoint,
  type PointSeries,
  type RawPoint,
} from "./difference";
import { formatAxisTime } from "./series";
import "./redesign.css";

type SeriesSpec = PointSeries & {
  description: string;
  group: "Weather" | "Market";
  pane: "weather" | "market";
  format: "temperature" | "probability";
  fill: "forecast" | "step-fresh" | "step-day" | "price";
  color: string;
  lineStyle?: "solid" | "dashed" | "dotted";
  maxAgeSeconds?: number | null;
  validTo?: number | null;
  currentPrice?: {
    value: number;
    display_value?: number | null;
    source: string;
    time?: string;
    received_at?: string;
    bin_id?: string;
  } | null;
};

type DifferenceSpec = {
  id: string;
  name: string;
  leftId: string;
  rightId: string;
  unit: "°F" | "display spread";
  axis: "left" | "right";
  color: string;
};

type EventSpec = { id: string; type: string; time: number; title: string };
type Payload = {
  mode: "full" | "delta" | "feed";
  channelId: string;
  revision: string;
  signature: string;
  timezone?: string;
  selectedBinId: string;
  selectedDifferenceIds?: string[];
  windowStart: number;
  windowEnd: number;
  series: SeriesSpec[];
  differenceInputs: PointSeries[];
  differenceSpecs: DifferenceSpec[];
  events?: EventSpec[];
};

const root = document.querySelector<HTMLElement>("#app")!;
const seriesApis = new Map<string, ISeriesApi<"Line">>();
const seriesData = new Map<string, RawPoint[]>();
const segmentApis = new Map<string, Map<number, ISeriesApi<"Line">>>();
const segmentTimes = new Map<string, Map<number, number[]>>();
const differenceInputData = new Map<string, RawPoint[]>();
const differenceApis = new Map<string, ISeriesApi<"Line">>();
const differenceSegmentApis = new Map<string, Map<number, ISeriesApi<"Line">>>();
const differenceSegmentTimes = new Map<string, Map<number, number[]>>();
const differenceData = new Map<string, DifferencePoint[]>();
let selectedDifferenceIds = new Set<string>();
let differenceSelectionInitialized = false;
let mainChart: IChartApi | null = null;
let differenceChart: IChartApi | null = null;
let mainTimeBasis: ISeriesApi<"Line"> | null = null;
let differenceTimeBasis: ISeriesApi<"Line"> | null = null;
let payload: Payload | null = null;
let signature = "";
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
let flushingRevision = "";

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
  if (unit === "probability") return `${(value * 100).toFixed(1)}%`;
  return `${value.toFixed(1)}${unit || ""}`;
}

function formatEt(value?: string): string {
  const epoch = value ? Date.parse(value) / 1000 : Number.NaN;
  return Number.isFinite(epoch) ? `${formatAxisTime(epoch, 3, "America/New_York")} ET` : "Unavailable";
}

function setFollowing(value: boolean): void {
  following = value;
  document.querySelector("#follow-button")?.setAttribute("aria-pressed", String(value));
}

function buildShell(): void {
  root.innerHTML = `
    <div class="shell" id="shell">
      <div class="toolbar">
        <button id="events-button" class="toolbar-command" aria-pressed="false"><i data-lucide="eye"></i><span>Events</span></button>
        <span class="price-readout" id="price-readout">Price Unavailable</span>
        <span class="spacer"></span>
        <button id="follow-button" class="tool-button" title="Follow latest" aria-label="Follow latest" aria-pressed="true"><i data-lucide="locate-fixed"></i></button>
        <button id="reset-button" class="tool-button" title="Reset view" aria-label="Reset view"><i data-lucide="rotate-ccw"></i></button>
        <button id="fullscreen-button" class="tool-button" title="Full screen" aria-label="Full screen"><i data-lucide="expand"></i></button>
      </div>
      <div id="channel-warning" class="notice hidden">Live updates are unavailable in this browser. Manual controls remain available.</div>
      <div id="payload-warning" class="notice hidden">A chart update was rejected because its data was invalid or belonged to another selection.</div>
      <div class="legend" id="main-legend"></div>
      <div id="main-chart"></div>
      <div class="difference-title"><strong>Difference</strong><span>Six fixed real-time subtractions on a one-minute as-of grid.</span></div>
      <div class="legend difference-controls" id="difference-controls"></div>
      <div class="legend difference-detail" id="difference-detail"><span class="legend-time">ET</span></div>
      <div class="difference-wrap"><div id="difference-chart"></div><div id="difference-empty" class="empty hidden">No selected difference has two valid inputs</div></div>
      <div class="disclaimer">Weather differences use °F. Price comparisons are display spreads without a shared physical unit and are only for visual research.</div>
    </div>`;
  createIcons({ icons: { Expand, Eye, LocateFixed, RotateCcw } });
  mainChart = createChart(document.querySelector<HTMLElement>("#main-chart")!, chartOptions());
  mainChart.panes()[0].setStretchFactor(65);
  mainChart.addPane().setStretchFactor(35);
  differenceChart = createChart(
    document.querySelector<HTMLElement>("#difference-chart")!, chartOptions(),
  );
  root.dataset.mountCount = "1";
  root.dataset.feedPaused = "false";
  setFollowing(true);

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
    if (syncingCrosshair) return;
    root.dataset.mainCrosshair = param.time == null ? "" : String(param.time);
    renderMainLegend(param.time);
    crosshairTime = param.time;
    syncCrosshair(param.time, true);
  });
  differenceChart.subscribeCrosshairMove((param) => {
    if (syncingCrosshair) return;
    root.dataset.differenceCrosshair = param.time == null ? "" : String(param.time);
    renderDifferenceDetails(param.time);
    crosshairTime = param.time;
    syncCrosshair(param.time, false);
  });
  document.querySelector("#events-button")?.addEventListener("click", () => {
    eventsVisible = !eventsVisible;
    document.querySelector("#events-button")?.setAttribute("aria-pressed", String(eventsVisible));
    renderMarkers();
  });
  document.querySelector("#follow-button")?.addEventListener("click", () => {
    setFollowing(true);
    mainChart?.timeScale().scrollToRealTime();
  });
  for (const selector of ["#main-chart", "#difference-chart"]) {
    const target = document.querySelector(selector);
    const stopFollowing = () => setFollowing(false);
    target?.addEventListener("pointerdown", stopFollowing);
    target?.addEventListener("wheel", stopFollowing, { passive: true });
    target?.addEventListener("touchstart", stopFollowing, { passive: true });
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
    setFollowing(false);
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
  root.dataset.channelSubscriptions = "0";
  if (!("BroadcastChannel" in window)) {
    document.querySelector("#channel-warning")?.classList.remove("hidden");
    return;
  }
  document.querySelector("#channel-warning")?.classList.add("hidden");
  channel = new BroadcastChannel(`nice-weather-${nextId}`);
  channel.addEventListener("message", (event) => applyPayload(event.data as Payload));
  root.dataset.channelSubscriptions = "1";
}

function seriesOptions(spec: SeriesSpec) {
  return {
    title: "",
    color: spec.color,
    lineWidth: spec.id === "price" ? 3 as const : 2 as const,
    lineStyle: style(spec.lineStyle),
    lineType: spec.fill === "forecast" ? LineType.Simple : LineType.WithSteps,
    priceScaleId: spec.pane === "weather" ? "left" : "right",
    priceLineVisible: false,
    lastValueVisible: false,
    crosshairMarkerVisible: true,
    priceFormat: spec.format === "probability"
      ? { type: "custom" as const, minMove: 0.001, formatter: (v: number) => `${(v * 100).toFixed(0)}%` }
      : { type: "price" as const, precision: 1, minMove: 0.1 },
  };
}

function valuedLineData(point: RawPoint): LineData {
  if (point.value === null || !Number.isFinite(point.value)) {
    throw new Error("Chart segment requires a finite point");
  }
  return { time: point.time as UTCTimestamp, value: point.value };
}

function apisFor(id: string): ISeriesApi<"Line">[] {
  return [...(segmentApis.get(id)?.values() || [])];
}

function removeSegment(id: string, key: number): void {
  const apis = segmentApis.get(id);
  const times = segmentTimes.get(id);
  const api = apis?.get(key);
  if (api) mainChart?.removeSeries(api);
  if (api && seriesApis.get(id) === api) {
    seriesApis.delete(id);
    if (markers) markers = null;
  }
  apis?.delete(key);
  times?.delete(key);
  if (apis?.size === 0) segmentApis.delete(id);
  if (times?.size === 0) segmentTimes.delete(id);
}

function reconcileSeriesFull(spec: SeriesSpec): void {
  for (const key of [...(segmentApis.get(spec.id)?.keys() || [])]) removeSegment(spec.id, key);
  const apis = new Map<number, ISeriesApi<"Line">>();
  const times = new Map<number, number[]>();
  const normalized = mergeRawPoints([], spec.points);
  for (const segment of nonNullSegments(normalized)) {
    const key = segment[0].time;
    const api = mainChart!.addSeries(
      LineSeries, seriesOptions(spec), spec.pane === "market" ? 1 : 0,
    );
    api.setData(segment.map(valuedLineData));
    apis.set(key, api);
    times.set(key, segment.map((point) => point.time));
  }
  segmentApis.set(spec.id, apis);
  segmentTimes.set(spec.id, times);
  const representative = apis.values().next().value;
  if (representative) seriesApis.set(spec.id, representative);
  seriesData.set(spec.id, normalized);
}

function reconcileSeriesDelta(spec: SeriesSpec, previous: RawPoint[], merged: RawPoint[]): void {
  const previousByTime = new Map(previous.map((point) => [point.time, point.value]));
  const validKeys = new Set<number>();
  for (const segment of nonNullSegments(merged)) {
    const key = segment[0].time;
    validKeys.add(key);
    const nextTimes = segment.map((point) => point.time);
    const oldTimes = segmentTimes.get(spec.id)?.get(key) || [];
    const keepsPrefix = oldTimes.every((time, index) => nextTimes[index] === time);
    if (segmentApis.get(spec.id)?.has(key) && !keepsPrefix) removeSegment(spec.id, key);
    let api = segmentApis.get(spec.id)?.get(key);
    if (!api) {
      api = mainChart!.addSeries(
        LineSeries, seriesOptions(spec), spec.pane === "market" ? 1 : 0,
      );
      if (!segmentApis.has(spec.id)) segmentApis.set(spec.id, new Map());
      segmentApis.get(spec.id)!.set(key, api);
      for (const point of segment) api.update(valuedLineData(point));
    } else {
      const oldSet = new Set(oldTimes);
      let lastTime = oldTimes.at(-1) ?? Number.NEGATIVE_INFINITY;
      for (const point of segment) {
        const changed = previousByTime.has(point.time)
          && !Object.is(previousByTime.get(point.time), point.value);
        if (oldSet.has(point.time) && !changed) continue;
        api.update(valuedLineData(point), point.time < lastTime);
        lastTime = Math.max(lastTime, point.time);
      }
    }
    if (!segmentTimes.has(spec.id)) segmentTimes.set(spec.id, new Map());
    segmentTimes.get(spec.id)!.set(key, nextTimes);
  }
  for (const key of [...(segmentApis.get(spec.id)?.keys() || [])]) {
    if (!validKeys.has(key)) removeSegment(spec.id, key);
  }
  const representative = segmentApis.get(spec.id)?.values().next().value;
  if (representative) seriesApis.set(spec.id, representative);
  else seriesApis.delete(spec.id);
  seriesData.set(spec.id, merged);
}

function reconcileFull(specs: SeriesSpec[]): void {
  if (!mainChart) return;
  const ids = new Set(specs.map((spec) => spec.id));
  for (const id of [...segmentApis.keys()]) {
    if (ids.has(id)) continue;
    for (const key of [...(segmentApis.get(id)?.keys() || [])]) removeSegment(id, key);
    seriesData.delete(id);
  }
  for (const spec of specs) reconcileSeriesFull(spec);
}

function reconcileDelta(specs: SeriesSpec[]): void {
  for (const spec of specs) {
    const previous = seriesData.get(spec.id) || [];
    const merged = mergeRawPoints(previous, spec.points);
    reconcileSeriesDelta(spec, previous, merged);
  }
}

function reconcileDifferenceInputs(specs: PointSeries[], full: boolean): void {
  if (full) differenceInputData.clear();
  for (const spec of specs) {
    const previous = differenceInputData.get(spec.id) || [];
    differenceInputData.set(spec.id, mergeRawPoints(full ? [] : previous, spec.points));
  }
}

function removeDifferenceSegment(id: string, key: number): void {
  const apis = differenceSegmentApis.get(id);
  const times = differenceSegmentTimes.get(id);
  const api = apis?.get(key);
  if (api) differenceChart?.removeSeries(api);
  if (api && differenceApis.get(id) === api) differenceApis.delete(id);
  apis?.delete(key);
  times?.delete(key);
  if (apis?.size === 0) differenceSegmentApis.delete(id);
  if (times?.size === 0) differenceSegmentTimes.delete(id);
}

function differenceRawPoints(points: DifferencePoint[]): RawPoint[] {
  return points.map((point) => ({ time: point.time, value: point.value }));
}

function reconcileDifferenceFull(spec: DifferenceSpec, points: DifferencePoint[]): void {
  for (const key of [...(differenceSegmentApis.get(spec.id)?.keys() || [])]) {
    removeDifferenceSegment(spec.id, key);
  }
  const apis = new Map<number, ISeriesApi<"Line">>();
  const times = new Map<number, number[]>();
  for (const segment of nonNullSegments(differenceRawPoints(points))) {
    const key = segment[0].time;
    const api = differenceChart!.addSeries(LineSeries, differenceOptions(spec));
    api.setData(segment.map(valuedLineData));
    apis.set(key, api);
    times.set(key, segment.map((point) => point.time));
  }
  differenceSegmentApis.set(spec.id, apis);
  differenceSegmentTimes.set(spec.id, times);
  const representative = apis.values().next().value;
  if (representative) differenceApis.set(spec.id, representative);
}

function reconcileDifferenceDelta(
  spec: DifferenceSpec,
  previous: DifferencePoint[],
  points: DifferencePoint[],
): void {
  const previousByTime = new Map(previous.map((point) => [point.time, point.value]));
  const validKeys = new Set<number>();
  for (const segment of nonNullSegments(differenceRawPoints(points))) {
    const key = segment[0].time;
    validKeys.add(key);
    const nextTimes = segment.map((point) => point.time);
    const oldTimes = differenceSegmentTimes.get(spec.id)?.get(key) || [];
    const keepsPrefix = oldTimes.every((time, index) => nextTimes[index] === time);
    if (differenceSegmentApis.get(spec.id)?.has(key) && !keepsPrefix) {
      removeDifferenceSegment(spec.id, key);
    }
    let api = differenceSegmentApis.get(spec.id)?.get(key);
    if (!api) {
      api = differenceChart!.addSeries(LineSeries, differenceOptions(spec));
      if (!differenceSegmentApis.has(spec.id)) differenceSegmentApis.set(spec.id, new Map());
      differenceSegmentApis.get(spec.id)!.set(key, api);
      for (const point of segment) api.update(valuedLineData(point));
    } else {
      const oldSet = new Set(oldTimes);
      let lastTime = oldTimes.at(-1) ?? Number.NEGATIVE_INFINITY;
      for (const point of segment) {
        const changed = previousByTime.has(point.time)
          && !Object.is(previousByTime.get(point.time), point.value);
        if (oldSet.has(point.time) && !changed) continue;
        api.update(valuedLineData(point), point.time < lastTime);
        lastTime = Math.max(lastTime, point.time);
      }
    }
    if (!differenceSegmentTimes.has(spec.id)) {
      differenceSegmentTimes.set(spec.id, new Map());
    }
    differenceSegmentTimes.get(spec.id)!.set(key, nextTimes);
  }
  for (const key of [...(differenceSegmentApis.get(spec.id)?.keys() || [])]) {
    if (!validKeys.has(key)) removeDifferenceSegment(spec.id, key);
  }
  const representative = differenceSegmentApis.get(spec.id)?.values().next().value;
  if (representative) differenceApis.set(spec.id, representative);
  else differenceApis.delete(spec.id);
}

function differenceOptions(spec: DifferenceSpec) {
  return {
    title: "",
    color: spec.color,
    lineWidth: 2 as const,
    priceScaleId: spec.axis,
    priceLineVisible: false,
    lastValueVisible: false,
    crosshairMarkerVisible: true,
    priceFormat: {
      type: "custom" as const,
      minMove: 0.01,
      formatter: (value: number) => `${value >= 0 ? "+" : ""}${value.toFixed(2)}`,
    },
  };
}

function setTimeBasis(start: number, end: number): void {
  if (!mainChart || !differenceChart) return;
  const points: LineData[] = [];
  for (let current = Math.floor(start / 60) * 60; current <= end; current += 60) {
    points.push({ time: current as UTCTimestamp, value: 0 });
  }
  const options = {
    color: "rgba(0,0,0,0)",
    crosshairMarkerVisible: false,
    priceLineVisible: false,
    lastValueVisible: false,
    priceScaleId: "time-basis",
  };
  if (!mainTimeBasis) mainTimeBasis = mainChart.addSeries(LineSeries, options, 0);
  if (!differenceTimeBasis) {
    differenceTimeBasis = differenceChart.addSeries(LineSeries, options);
  }
  mainTimeBasis.setData(points);
  differenceTimeBasis.setData(points);
}

function renderDifferenceControls(): void {
  if (!payload) return;
  const target = document.querySelector<HTMLElement>("#difference-controls")!;
  target.innerHTML = payload.differenceSpecs.map((spec) => `
    <label title="${escapeHtml(spec.unit)}">
      <input type="checkbox" name="difference" value="${escapeHtml(spec.id)}" ${selectedDifferenceIds.has(spec.id) ? "checked" : ""}/>
      <i style="background:${spec.color}"></i>${escapeHtml(spec.name)}
    </label>`).join("");
  target.querySelectorAll<HTMLInputElement>("input[name='difference']").forEach((input) => {
    input.addEventListener("change", () => {
      if (input.checked) selectedDifferenceIds.add(input.value);
      else selectedDifferenceIds.delete(input.value);
      renderDifferences(true);
      Streamlit.setComponentValue({ selectedDifferenceIds: [...selectedDifferenceIds] });
    });
  });
}

function renderDifferences(fullReplace: boolean): void {
  if (!payload || !differenceChart) return;
  const validIds = new Set<string>();
  for (const spec of payload.differenceSpecs) {
    if (!selectedDifferenceIds.has(spec.id)) continue;
    const points = differencePoints(
      differenceInputData.get(spec.leftId) || [],
      differenceInputData.get(spec.rightId) || [],
    );
    if (!points.some((point) => point.value != null)) continue;
    validIds.add(spec.id);
    const previous = differenceData.get(spec.id) || [];
    if (fullReplace) reconcileDifferenceFull(spec, points);
    else reconcileDifferenceDelta(spec, previous, points);
    differenceData.set(spec.id, points);
  }
  for (const id of [...differenceSegmentApis.keys()]) {
    if (validIds.has(id)) continue;
    for (const key of [...(differenceSegmentApis.get(id)?.keys() || [])]) {
      removeDifferenceSegment(id, key);
    }
    differenceData.delete(id);
  }
  document.querySelector("#difference-empty")?.classList.toggle("hidden", validIds.size > 0);
  root.dataset.differenceSeriesCount = String(
    [...differenceSegmentApis.values()].reduce((count, apis) => count + apis.size, 0),
  );
  renderDifferenceDetails(crosshairTime);
}

function pointAt(points: RawPoint[] | undefined, time: number): RawPoint | undefined {
  return points?.find((point) => point.time === time);
}

function mainPointAt(spec: SeriesSpec, time: number): RawPoint | undefined {
  const points = seriesData.get(spec.id) || [];
  const exact = pointAt(points, time);
  if (exact) return exact.value == null ? undefined : exact;
  const rightIndex = points.findIndex((point) => point.time > time);
  if (rightIndex <= 0) return undefined;
  const left = points[rightIndex - 1];
  const right = points[rightIndex];
  if (left.value == null || right.value == null) return undefined;
  if (spec.validTo != null && time >= spec.validTo) return undefined;
  if (spec.fill !== "forecast") {
    if (spec.maxAgeSeconds != null && time - left.time > spec.maxAgeSeconds) return undefined;
    return { ...left, time };
  }
  if (right.time === left.time) return undefined;
  const ratio = (time - left.time) / (right.time - left.time);
  return {
    ...left,
    time,
    value: left.value + (right.value - left.value) * ratio,
    objectTime: new Date(time * 1000).toISOString(),
    validFrom: left.objectTime || left.object_time,
    validTo: right.objectTime || right.object_time,
  };
}

function pointAudit(point: RawPoint): string {
  const objectTime = formatEt(point.objectTime || point.object_time);
  const receivedAt = formatEt(point.receivedAt || point.received_at);
  const source = point.priceSource || point.source || "Unknown source";
  const age = point.ageSeconds == null ? "age unavailable" : `${Math.round(point.ageSeconds)}s old`;
  const valid = point.validFrom || point.validTo
    ? `; valid bracket ${formatEt(point.validFrom)} to ${formatEt(point.validTo)}`
    : "";
  const issued = point.issuedAt ? `; issued ${formatEt(point.issuedAt)}` : "";
  const price = point.priceSource
    ? `; raw ${point.rawValue ?? "Unavailable"}; display ${point.displayValue ?? point.value}; bin ${point.binId ?? "Unavailable"}`
    : "";
  return `${source}; object ${objectTime}; received ${receivedAt}; ${age}${valid}${issued}${price}`;
}

function renderPrice(): void {
  const spec = payload?.series.find((item) => item.id === "price");
  const point = spec?.currentPrice;
  const target = document.querySelector<HTMLElement>("#price-readout")!;
  if (!point || spec?.binId !== payload?.selectedBinId) {
    target.textContent = "Price Unavailable";
    return;
  }
  const receivedAt = Date.parse(point.received_at || point.time || "") / 1000;
  const age = receivedAt ? Math.max(0, Math.round(Date.now() / 1000 - receivedAt)) : 0;
  const display = point.display_value ?? (point.value <= 1 ? point.value * 100 : point.value);
  target.textContent = `Price ${display.toFixed(1)}% · ${point.source} · ${age < 60 ? `${age}s` : `${Math.floor(age / 60)}m`} old`;
  target.title = `Bin ${payload?.selectedBinId}; raw ${point.value}; display ${display.toFixed(1)}; event ${formatEt(point.time)}; received ${formatEt(point.received_at)}`;
}

function renderMainLegend(time?: Time): void {
  if (!payload) return;
  const numericTime = time == null ? null : Number(time);
  const stamp = numericTime == null ? "ET" : `${formatAxisTime(numericTime, 3, "America/New_York")} ET`;
  const items = payload.series.map((spec) => {
    const point = numericTime == null
      ? seriesData.get(spec.id)?.filter((item) => item.value !== null).at(-1)
      : mainPointAt(spec, numericTime);
    const value = point?.value == null
      ? "Unavailable"
      : formatRaw(point.value, spec.format === "probability" ? "probability" : "°F");
    const details = point
      ? `${spec.description} ${pointAudit(point)}`
      : `${spec.description} Unavailable at the crosshair time.`;
    return `<span class="legend-item"><i style="background:${spec.color}"></i>${escapeHtml(spec.name)} <button class="info" title="${escapeHtml(details)}">i</button><strong>${value}</strong></span>`;
  }).join("");
  document.querySelector<HTMLElement>("#main-legend")!.innerHTML = `<span class="legend-time">${stamp}</span>${items}`;
}

function renderDifferenceDetails(time?: Time): void {
  if (!payload) return;
  const target = document.querySelector<HTMLElement>("#difference-detail")!;
  if (time == null) {
    target.innerHTML = '<span class="legend-time">ET</span><span>Move the crosshair to audit both inputs.</span>';
    return;
  }
  const numericTime = Number(time);
  const stamp = `${formatAxisTime(numericTime, 3, "America/New_York")} ET`;
  const items = payload.differenceSpecs
    .filter((spec) => selectedDifferenceIds.has(spec.id))
    .map((spec) => {
      const point = differenceData.get(spec.id)?.find((item) => item.time === numericTime);
      if (!point || point.value == null || !point.left || !point.right) {
        return `<span class="difference-value"><i style="background:${spec.color}"></i>${escapeHtml(spec.name)} <strong>Unavailable</strong></span>`;
      }
      const title = `Left: ${pointAudit(point.left)}; right: ${pointAudit(point.right)}`;
      const left = point.left.value == null ? "Unavailable" : point.left.value.toFixed(2);
      const right = point.right.value == null ? "Unavailable" : point.right.value.toFixed(2);
      return `<span class="difference-value" title="${escapeHtml(title)}"><i style="background:${spec.color}"></i>${escapeHtml(spec.name)} <strong>${point.value >= 0 ? "+" : ""}${point.value.toFixed(2)} ${escapeHtml(spec.unit)}</strong><small>${left} − ${right}</small></span>`;
    }).join("");
  target.innerHTML = `<span class="legend-time">${stamp}</span>${items}`;
}

function renderMarkers(): void {
  if (!payload) return;
  const anchor = payload.series.find((spec) => spec.group === "Weather");
  const api = anchor ? seriesApis.get(anchor.id) : undefined;
  if (!api) return;
  const items = eventsVisible ? (payload.events || []).map((event) => ({
    id: event.id,
    time: event.time as UTCTimestamp,
    position: "aboveBar" as const,
    color: "#667085",
    shape: "circle" as const,
    text: "",
    size: 0.6,
  })) : [];
  if (markers) markers.setMarkers(items);
  else markers = createSeriesMarkers(api, items);
}

function syncCrosshair(time: Time | undefined, fromMain: boolean): void {
  const target = fromMain ? differenceChart : mainChart;
  if (!target || time == null) {
    target?.clearCrosshairPosition();
    return;
  }
  syncingCrosshair = true;
  const basis = fromMain ? differenceTimeBasis : mainTimeBasis;
  if (fromMain) {
    root.dataset.differenceCrosshair = String(time);
    renderDifferenceDetails(time);
  } else {
    root.dataset.mainCrosshair = String(time);
    renderMainLegend(time);
  }
  try {
    if (basis) target.setCrosshairPosition(0, time, basis);
  } catch {
    requestAnimationFrame(() => restoreCrosshair(Number(time), fromMain));
  } finally {
    syncingCrosshair = false;
  }
}

function restoreCrosshair(time: number, onDifference: boolean): boolean {
  const chart = onDifference ? differenceChart : mainChart;
  const basis = onDifference ? differenceTimeBasis : mainTimeBasis;
  if (!chart || !basis) return false;
  try {
    chart.setCrosshairPosition(0, time as UTCTimestamp, basis);
    return true;
  } catch {
    return false;
  }
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

function mergeSeriesMessages<T extends PointSeries>(existing: T[], incoming: T[]): T[] {
  const result = new Map(existing.map((spec) => [spec.id, spec]));
  for (const spec of incoming) {
    const previous = result.get(spec.id);
    result.set(spec.id, {
      ...previous,
      ...spec,
      points: mergeRawPoints(previous?.points || [], spec.points),
    });
  }
  return [...result.values()];
}

function mergePending(existing: Payload | null, next: Payload): Payload {
  if (!existing) return next;
  return {
    ...existing,
    ...next,
    series: mergeSeriesMessages(existing.series, next.series) as SeriesSpec[],
    differenceInputs: mergeSeriesMessages(existing.differenceInputs, next.differenceInputs),
  };
}

function updateIncrementally(next: Payload): void {
  const savedCrosshair = crosshairTime == null ? undefined : Number(crosshairTime);
  syncingCrosshair = true;
  mainChart?.clearCrosshairPosition();
  differenceChart?.clearCrosshairPosition();
  requestAnimationFrame(() => {
    if (chartPointerActive) {
      pendingDelta = mergePending(pendingDelta, next);
      root.dataset.pendingRevision = String(next.revision);
      syncingCrosshair = false;
      return;
    }
    reconcileDelta(next.series);
    reconcileDifferenceInputs(next.differenceInputs, false);
    renderDifferences(false);
    root.dataset.pricePointCount = String(
      seriesData.get("price")?.filter((point) => point.value !== null).length || 0,
    );
    if (following) mainChart?.timeScale().scrollToRealTime();
    root.dataset.appliedRevision = String(next.revision);
    if (String(next.revision) === flushingRevision) {
      root.dataset.flushedRevision = flushingRevision;
      flushingRevision = "";
    }
    const restoreTime = crosshairTime == null ? savedCrosshair : Number(crosshairTime);
    if (restoreTime != null && Number.isFinite(restoreTime)) {
      restoreCrosshairsAfterPaint(restoreTime);
    } else {
      syncingCrosshair = false;
    }
  });
}

function flushPendingDelta(): void {
  chartPointerActive = false;
  root.dataset.feedPaused = "false";
  const next = pendingDelta;
  pendingDelta = null;
  if (next) {
    flushingRevision = String(next.revision);
    applyPayload(next);
  }
  root.dataset.pendingRevision = "";
}

function validPoint(point: RawPoint): boolean {
  return Number.isFinite(point.time)
    && point.time >= 0
    && (point.value === null || Number.isFinite(point.value));
}

function validatePayload(next: Payload): boolean {
  if (!next || typeof next.channelId !== "string" || typeof next.selectedBinId !== "string") {
    return false;
  }
  if (next.mode !== "feed" && (!next.signature || !Number.isFinite(next.windowStart)
    || !Number.isFinite(next.windowEnd))) return false;
  if (![...next.series, ...next.differenceInputs].every(
    (spec) => Array.isArray(spec.points) && spec.points.every(validPoint),
  )) return false;
  const price = next.series.find((spec) => spec.id === "price");
  const differencePrice = next.differenceInputs.find((spec) => spec.id === "price");
  const differenceInputIds = [...next.differenceInputs.map((spec) => spec.id)].sort().join("|");
  const differenceIds = [...next.differenceSpecs.map((spec) => spec.id)].sort().join("|");
  const pricePointsMatch = differencePrice?.points.every(
    (point) => point.binId === next.selectedBinId,
  );
  return price?.binId === next.selectedBinId
    && (!price.currentPrice || price.currentPrice.bin_id === next.selectedBinId)
    && differencePrice?.binId === next.selectedBinId
    && pricePointsMatch === true
    && differenceInputIds === "forecast|metar|price|weather-gov"
    && differenceIds === [
      "metar-minus-forecast",
      "price-minus-forecast",
      "price-minus-metar",
      "price-minus-weather-gov",
      "weather-gov-minus-forecast",
      "weather-gov-minus-metar",
    ].join("|");
}

function rejectPayload(): void {
  document.querySelector("#payload-warning")?.classList.remove("hidden");
  root.dataset.rejectedUpdates = String(Number(root.dataset.rejectedUpdates || "0") + 1);
}

function applyPayload(next: Payload): void {
  if (next.channelId !== channelId || next.mode === "feed") return;
  if (!validatePayload(next)) {
    rejectPayload();
    return;
  }
  if (next.mode === "delta" && (next.signature !== signature
    || next.selectedBinId !== payload?.selectedBinId)) {
    rejectPayload();
    return;
  }
  document.querySelector("#payload-warning")?.classList.add("hidden");
  if (next.mode === "delta" && chartPointerActive) {
    pendingDelta = mergePending(pendingDelta, next);
    root.dataset.pendingRevision = String(next.revision);
    return;
  }

  const signatureChanged = signature !== next.signature;
  payload = { ...payload, ...next, mode: next.mode, series: next.series };
  if (next.mode === "full") {
    const validDifferenceIds = new Set(next.differenceSpecs.map((spec) => spec.id));
    selectedDifferenceIds = differenceSelectionInitialized
      ? new Set([...selectedDifferenceIds].filter((id) => validDifferenceIds.has(id)))
      : new Set((next.selectedDifferenceIds || []).filter((id) => validDifferenceIds.has(id)));
    differenceSelectionInitialized = true;
    renderDifferenceControls();
    if (signatureChanged) {
      rangeSyncReady = false;
      signature = next.signature;
      setTimeBasis(next.windowStart, next.windowEnd);
      reconcileFull(next.series);
      reconcileDifferenceInputs(next.differenceInputs, true);
      renderDifferences(true);
      rangeSyncReady = true;
      mainChart?.timeScale().fitContent();
    } else {
      renderDifferences(true);
    }
  } else {
    updateIncrementally(next);
  }
  renderPrice();
  renderMainLegend(crosshairTime);
  renderMarkers();
  root.dataset.signature = signature;
  root.dataset.selectedBinId = next.selectedBinId;
  root.dataset.payloadBytes = String(JSON.stringify(next).length);
  root.dataset.mainSeriesCount = String(seriesApis.size);
  root.dataset.pricePointCount = String(
    seriesData.get("price")?.filter((point) => point.value !== null).length || 0,
  );
  if (next.mode === "full") root.dataset.appliedRevision = String(next.revision);
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
  if (!validatePayload(next)) {
    rejectPayload();
    return;
  }
  setupChannel(next.channelId);
  applyPayload(next);
}

Streamlit.events.addEventListener(Streamlit.RENDER_EVENT, (event) => {
  render((event as CustomEvent<RenderData>).detail);
});
Streamlit.setComponentReady();
