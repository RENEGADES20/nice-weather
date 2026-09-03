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
  type SeriesMarker,
  type Time,
  type UTCTimestamp,
} from "lightweight-charts";
import { createIcons, Expand, Eye, LocateFixed, RotateCcw } from "lucide";
import { Streamlit, type RenderData } from "streamlit-component-lib";
import { downsample, durationLabel, mergePoints, type Point } from "./series";
import "./style.css";

type ChartPoint = Point & { received_at?: string; object_time?: string };
type SeriesSpec = {
  id: string;
  name: string;
  group: "Weather" | "Market";
  axis: "left" | "right";
  color: string;
  lineStyle?: "solid" | "dashed" | "dotted";
  defaultVisible: boolean;
  points: ChartPoint[];
};
type KnowledgeEvent = {
  id: string;
  type: string;
  title: string;
  time: number;
  object_time: string;
  received_at: string;
  first_market_move_at?: string | null;
  source_latency_seconds?: number | null;
  tradable_lead_seconds?: number | null;
  threshold_times?: Record<string, string | null>;
  bin_id?: string;
  temperature_f?: number;
};
type Payload = {
  signature: string;
  objectTimezone: string;
  displayTimezone?: string;
  threshold: string;
  series: SeriesSpec[];
  events: KnowledgeEvent[];
};

const root = document.querySelector<HTMLElement>("#app")!;
let mainChart: IChartApi | null = null;
let responseChart: IChartApi | null = null;
let signature = "";
let payload: Payload | null = null;
let following = true;
let selectedEventId = "";
let comparisonMode = localStorage.getItem("nice-weather-event-mode") === "compare";
let frameHeightPulse = 0;
const seriesApis = new Map<string, ISeriesApi<"Line">>();
const responseApis = new Map<string, ISeriesApi<"Line">>();
const seriesData = new Map<string, Point[]>();
let markersApi: ISeriesMarkersPluginApi<Time> | null = null;
const responseData = new Map<string, Point[]>();
let syncingCrosshair = false;

function storedObject<T>(key: string): T {
  try {
    return JSON.parse(localStorage.getItem(key) || "{}") as T;
  } catch {
    localStorage.removeItem(key);
    return {} as T;
  }
}

const visibility = storedObject<Record<string, boolean>>("nice-weather-layers");
const styles = storedObject<Record<string, { color?: string; width?: number }>>("nice-weather-styles");

function escapeHtml(value: string): string {
  return value.replace(/[&<>'"]/g, (character) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", "'": "&#39;", '"': "&quot;",
  })[character]!);
}

function announceFrameHeight(): void {
  const frameHeight = Math.max(760, root.scrollHeight);
  frameHeightPulse = frameHeightPulse === 0 ? 1 : 0;
  Streamlit.setFrameHeight(frameHeight + frameHeightPulse);
}

function formatTime(epoch: number, zone?: string): string {
  return new Intl.DateTimeFormat(undefined, {
    timeZone: zone,
    month: "short",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    hour12: false,
  }).format(new Date(epoch * 1000));
}

function chartOptions(relative = false) {
  const formatter = relative
    ? (time: Time) => {
        const delta = Number(time) - 1_700_000_000;
        const sign = delta >= 0 ? "+" : "-";
        const absolute = Math.abs(Math.round(delta / 60));
        return `${sign}${absolute}m`;
      }
    : (time: Time) => formatTime(Number(time), payload?.displayTimezone);
  return {
    autoSize: true,
    layout: {
      background: { type: ColorType.Solid, color: "#101418" },
      textColor: "#9ca7b3",
      attributionLogo: false,
    },
    grid: {
      vertLines: { color: "#1d232a" },
      horzLines: { color: "#1d232a" },
    },
    crosshair: { mode: CrosshairMode.Normal },
    leftPriceScale: { visible: true, borderColor: "#303740" },
    rightPriceScale: { visible: true, borderColor: "#303740" },
    timeScale: {
      borderColor: "#303740",
      timeVisible: true,
      secondsVisible: true,
      tickMarkFormatter: formatter,
    },
    handleScroll: { mouseWheel: true, pressedMouseMove: true, horzTouchDrag: true },
    handleScale: { mouseWheel: true, pinch: true, axisPressedMouseMove: true },
  };
}

function lineStyle(value?: string): LineStyle {
  if (value === "dashed") return LineStyle.Dashed;
  if (value === "dotted") return LineStyle.Dotted;
  return LineStyle.Solid;
}

function buildShell(): void {
  root.innerHTML = `
    <div class="shell" id="shell">
      <div class="toolbar">
        <span class="toolbar-label">Range</span>
        ${[1, 2, 3, 5].map((day) => `<button class="range-button${day === 2 ? " active" : ""}" data-days="${day}">${day}D</button>`).join("")}
        <button class="tool-button" id="layers-button" title="Layers"><i data-lucide="eye"></i></button>
        <select class="event-select" id="mode-select" title="Delay view"><option value="single">Single event</option><option value="compare">Compare events</option></select>
        <select class="event-select" id="event-select" title="Tmax event"></select>
        <span class="spacer"></span>
        <button class="tool-button" id="follow-button" title="Follow latest"><i data-lucide="locate-fixed"></i></button>
        <button class="tool-button" id="reset-button" title="Reset view"><i data-lucide="rotate-ccw"></i></button>
        <button class="tool-button" id="fullscreen-button" title="Full screen"><i data-lucide="expand"></i></button>
      </div>
      <div class="layers hidden" id="layers"></div>
      <div class="chart-wrap"><div class="legend" id="main-legend">Move the crosshair to inspect values</div><canvas class="delay-connectors" id="delay-connectors"></canvas><div id="main-chart"></div></div>
      <div class="section-title">Price-in delay around selected Tmax event</div>
      <div class="chart-wrap"><div class="legend" id="response-legend">Event-relative market response</div><div id="response-chart"></div></div>
      <div class="latency" id="latency"></div>
    </div>`;
  createIcons({ icons: { Expand, Eye, LocateFixed, RotateCcw } });
  mainChart = createChart(document.querySelector<HTMLElement>("#main-chart")!, chartOptions());
  responseChart = createChart(
    document.querySelector<HTMLElement>("#response-chart")!,
    chartOptions(true),
  );
  mainChart.timeScale().subscribeVisibleLogicalRangeChange(() => {
    if (document.activeElement?.closest("#main-chart")) following = false;
  });
  const mainElement = document.querySelector<HTMLElement>("#main-chart")!;
  mainElement.addEventListener("wheel", () => { following = false; }, { passive: true });
  mainElement.addEventListener("pointerdown", () => { following = false; });
  mainChart.subscribeCrosshairMove((param) => {
    updateLegend("main-legend", param.time, param.seriesData);
    if (!syncingCrosshair) syncResponseCrosshair(param.time);
  });
  responseChart.subscribeCrosshairMove((param) => {
    updateLegend("response-legend", param.time, param.seriesData, true);
    if (!syncingCrosshair) syncMainCrosshair(param.time);
  });
  mainChart.timeScale().subscribeVisibleTimeRangeChange(drawDelayConnectors);
  window.addEventListener("resize", drawDelayConnectors);
  document.querySelector("#layers-button")?.addEventListener("click", () =>
    document.querySelector("#layers")?.classList.toggle("hidden"),
  );
  document.querySelector("#follow-button")?.addEventListener("click", () => {
    following = true;
    mainChart?.timeScale().scrollToRealTime();
  });
  document.querySelector("#reset-button")?.addEventListener("click", () => {
    following = false;
    mainChart?.timeScale().fitContent();
    responseChart?.timeScale().fitContent();
  });
  document.querySelector("#fullscreen-button")?.addEventListener("click", () =>
    document.querySelector<HTMLElement>("#shell")?.requestFullscreen(),
  );
  document.querySelector("#event-select")?.addEventListener("change", (event) => {
    selectedEventId = (event.target as HTMLSelectElement).value;
    renderResponse();
  });
  const modeSelect = document.querySelector<HTMLSelectElement>("#mode-select")!;
  modeSelect.value = comparisonMode ? "compare" : "single";
  modeSelect.addEventListener("change", () => {
    comparisonMode = modeSelect.value === "compare";
    localStorage.setItem("nice-weather-event-mode", modeSelect.value);
    renderResponse();
  });
  document.querySelectorAll<HTMLButtonElement>("[data-days]").forEach((button) => {
    button.addEventListener("click", () => {
      document.querySelectorAll("[data-days]").forEach((item) => item.classList.remove("active"));
      button.classList.add("active");
      const seconds = Number(button.dataset.days) * 86400;
      const now = Math.max(...(payload?.series.flatMap((item) => item.points.map((point) => point.time)) || [0]));
      mainChart?.timeScale().setVisibleRange({ from: (now - seconds) as UTCTimestamp, to: now as UTCTimestamp });
      following = false;
    });
  });
}

function closestPoint(points: Point[], time: number): Point | undefined {
  return points.reduce<Point | undefined>((best, point) => (
    !best || Math.abs(point.time - time) < Math.abs(best.time - time) ? point : best
  ), undefined);
}

function syncResponseCrosshair(time: Time | undefined): void {
  if (!responseChart || time === undefined) {
    responseChart?.clearCrosshairPosition();
    return;
  }
  const event = payload?.events.find((item) => item.id === selectedEventId);
  const first = responseApis.entries().next().value as [string, ISeriesApi<"Line">] | undefined;
  if (!event || !first) return;
  const relativeTime = 1_700_000_000 + Number(time) - event.time;
  const point = closestPoint(responseData.get(first[0]) || [], relativeTime);
  if (!point) return;
  syncingCrosshair = true;
  responseChart.setCrosshairPosition(point.value, relativeTime as UTCTimestamp, first[1]);
  syncingCrosshair = false;
}

function syncMainCrosshair(time: Time | undefined): void {
  if (!mainChart || time === undefined) {
    mainChart?.clearCrosshairPosition();
    return;
  }
  const event = payload?.events.find((item) => item.id === selectedEventId);
  const api = seriesApis.get("running-tmax") || seriesApis.values().next().value;
  if (!event || !api) return;
  const absoluteTime = event.time + Number(time) - 1_700_000_000;
  const point = closestPoint(seriesData.get("running-tmax") || [], absoluteTime);
  if (!point) return;
  syncingCrosshair = true;
  mainChart.setCrosshairPosition(point.value, absoluteTime as UTCTimestamp, api);
  syncingCrosshair = false;
}

function updateLegend(
  elementId: string,
  time: Time | undefined,
  values: ReadonlyMap<unknown, unknown>,
  relative = false,
): void {
  if (!payload || time === undefined) return;
  const labels: string[] = [];
  for (const spec of payload.series) {
    const api = relative ? responseApis.get(spec.id) : seriesApis.get(spec.id);
    const value = api ? values.get(api) as LineData | undefined : undefined;
    if (value && "value" in value) labels.push(`${spec.name} ${value.value.toFixed(spec.axis === "right" ? 3 : 1)}`);
  }
  const instant = relative
    ? `Δt ${formatRelative(Number(time) - 1_700_000_000)}`
    : `${formatTime(Number(time), payload.displayTimezone)} / ${formatTime(Number(time), payload.objectTimezone)}`;
  document.querySelector<HTMLElement>(`#${elementId}`)!.textContent = `${instant}  ${labels.join("  ") || "Unavailable"}`;
}

function formatRelative(seconds: number): string {
  const sign = seconds >= 0 ? "+" : "-";
  return `${sign}${Math.abs(Math.round(seconds / 60))}m`;
}

function updateSeries(spec: SeriesSpec): void {
  if (!mainChart) return;
  let api = seriesApis.get(spec.id);
  if (!api) {
    api = mainChart.addSeries(LineSeries, {
      title: spec.name,
      color: styles[spec.id]?.color || spec.color,
      lineWidth: (styles[spec.id]?.width || 2) as 1 | 2 | 3 | 4,
      lineStyle: lineStyle(spec.lineStyle),
      priceScaleId: spec.axis,
      visible: visibility[spec.id] ?? spec.defaultVisible,
      priceFormat: spec.axis === "right" ? { type: "price", precision: 3, minMove: 0.001 } : { type: "price", precision: 1, minMove: 0.1 },
    });
    seriesApis.set(spec.id, api);
  }
  const previous = seriesData.get(spec.id) || [];
  const incoming = downsample(spec.points.map(({ time, value }) => ({ time, value })));
  const merged = mergePoints(previous, incoming);
  let firstDifference = 0;
  while (
    firstDifference < previous.length
    && firstDifference < merged.length
    && previous[firstDifference].time === merged[firstDifference].time
    && previous[firstDifference].value === merged[firstDifference].value
  ) firstDifference += 1;
  const incremental = previous.length > 0
    && merged.length >= previous.length
    && firstDifference >= previous.length - 1;
  if (incremental) {
    for (const point of merged.slice(firstDifference)) {
      api.update({ time: point.time as UTCTimestamp, value: point.value });
    }
  } else if (JSON.stringify(previous) !== JSON.stringify(merged)) {
    api.setData(merged.map((point) => ({ time: point.time as UTCTimestamp, value: point.value })));
  }
  seriesData.set(spec.id, merged);
}

function renderLayers(): void {
  if (!payload) return;
  const container = document.querySelector<HTMLElement>("#layers")!;
  container.innerHTML = ["Weather", "Market"].map((group) => `
    <div class="layer-group">${group}</div>
    ${payload!.series.filter((item) => item.group === group).map((spec) => `
      <label class="layer-row"><input type="checkbox" data-layer="${escapeHtml(spec.id)}" ${(visibility[spec.id] ?? spec.defaultVisible) ? "checked" : ""}/><input type="color" data-color="${escapeHtml(spec.id)}" value="${styles[spec.id]?.color || spec.color}" title="Line color"/><select data-width="${escapeHtml(spec.id)}" title="Line width">${[1, 2, 3, 4].map((width) => `<option value="${width}" ${(styles[spec.id]?.width || 2) === width ? "selected" : ""}>${width}px</option>`).join("")}</select>${escapeHtml(spec.name)}</label>
    `).join("")}`).join("");
  container.querySelectorAll<HTMLInputElement>("[data-layer]").forEach((input) => {
    input.addEventListener("change", () => {
      const id = input.dataset.layer!;
      visibility[id] = input.checked;
      localStorage.setItem("nice-weather-layers", JSON.stringify(visibility));
      seriesApis.get(id)?.applyOptions({ visible: input.checked });
      responseApis.get(id)?.applyOptions({ visible: input.checked });
    });
  });
  container.querySelectorAll<HTMLInputElement>("[data-color]").forEach((input) => {
    input.addEventListener("change", () => {
      const id = input.dataset.color!;
      styles[id] = { ...styles[id], color: input.value };
      localStorage.setItem("nice-weather-styles", JSON.stringify(styles));
      seriesApis.get(id)?.applyOptions({ color: input.value });
      responseApis.get(id)?.applyOptions({ color: input.value });
    });
  });
  container.querySelectorAll<HTMLSelectElement>("[data-width]").forEach((input) => {
    input.addEventListener("change", () => {
      const id = input.dataset.width!;
      const width = Number(input.value) as 1 | 2 | 3 | 4;
      styles[id] = { ...styles[id], width };
      localStorage.setItem("nice-weather-styles", JSON.stringify(styles));
      seriesApis.get(id)?.applyOptions({ lineWidth: width });
      responseApis.get(id)?.applyOptions({ lineWidth: width });
    });
  });
}

function renderEvents(): void {
  if (!payload || !mainChart) return;
  const selector = document.querySelector<HTMLSelectElement>("#event-select")!;
  const researchEvents = payload.events.filter((event) =>
    ["bin_entered", "bin_eliminated", "forecast_revised"].includes(event.type),
  );
  selector.innerHTML = researchEvents.map((event) => `<option value="${escapeHtml(event.id)}">${escapeHtml(event.title)}</option>`).join("");
  if (!researchEvents.some((event) => event.id === selectedEventId)) selectedEventId = researchEvents.at(-1)?.id || "";
  selector.disabled = researchEvents.length === 0;
  selector.value = selectedEventId;
  const anchor = payload.series.find((item) => item.id === "running-tmax");
  const api = anchor ? seriesApis.get(anchor.id) : undefined;
  if (api) {
    const markers: SeriesMarker<Time>[] = payload.events.map((event) => ({
      time: event.time as UTCTimestamp,
      position: "aboveBar",
      color: event.type === "forecast_revised" ? "#f2c94c" : "#26a69a",
      shape: event.type === "forecast_revised" ? "square" : "arrowUp",
      text: event.title,
    }));
    if (markersApi) markersApi.setMarkers(markers);
    else markersApi = createSeriesMarkers(api, markers);
  }
  drawDelayConnectors();
}

function drawDelayConnectors(): void {
  if (!payload || !mainChart) return;
  const canvas = document.querySelector<HTMLCanvasElement>("#delay-connectors");
  const chartElement = document.querySelector<HTMLElement>("#main-chart");
  if (!canvas || !chartElement) return;
  const ratio = window.devicePixelRatio || 1;
  const width = chartElement.clientWidth;
  const height = chartElement.clientHeight;
  canvas.width = Math.round(width * ratio);
  canvas.height = Math.round(height * ratio);
  canvas.style.width = `${width}px`;
  canvas.style.height = `${height}px`;
  const context = canvas.getContext("2d");
  if (!context) return;
  context.scale(ratio, ratio);
  context.font = "11px Inter, sans-serif";
  for (const event of payload.events) {
    if (!event.first_market_move_at || event.temperature_f === undefined || !event.bin_id) continue;
    const weatherApi = seriesApis.get(event.type === "forecast_revised" ? "forecast" : "running-tmax");
    const marketId = `${event.bin_id}:mid`;
    const marketApi = seriesApis.get(marketId);
    const moveTime = new Date(event.first_market_move_at).getTime() / 1000;
    const marketPoint = closestPoint(seriesData.get(marketId) || [], moveTime);
    const x1 = mainChart.timeScale().timeToCoordinate(event.time as UTCTimestamp);
    const x2 = mainChart.timeScale().timeToCoordinate(moveTime as UTCTimestamp);
    const y1 = weatherApi?.priceToCoordinate(event.temperature_f) ?? null;
    const y2 = marketPoint && marketApi ? marketApi.priceToCoordinate(marketPoint.value) : null;
    if ([x1, x2, y1, y2].some((value) => value === null)) continue;
    const color = (event.tradable_lead_seconds ?? 0) < 0 ? "#d15c5c" : "#26a69a";
    context.strokeStyle = color;
    context.fillStyle = color;
    context.globalAlpha = 0.55;
    context.lineWidth = 1;
    context.setLineDash([4, 4]);
    context.beginPath();
    context.moveTo(x1!, y1!);
    context.lineTo(x2!, y2!);
    context.stroke();
    context.globalAlpha = 0.9;
    context.fillText(durationLabel(event.tradable_lead_seconds ?? null), (x1! + x2!) / 2 + 4, (y1! + y2!) / 2 - 4);
  }
  context.globalAlpha = 1;
  context.setLineDash([]);
}

function renderResponse(): void {
  if (!payload || !responseChart) return;
  for (const api of responseApis.values()) responseChart.removeSeries(api);
  responseApis.clear();
  responseData.clear();
  const event = payload.events.find((item) => item.id === selectedEventId);
  if (!event) {
    document.querySelector<HTMLElement>("#latency")!.innerHTML = `<div class="empty">No Tmax knowledge event is available.</div>`;
    return;
  }
  const base = 1_700_000_000;
  if (comparisonMode) {
    const aligned: number[][] = [];
    for (const candidate of payload.events.filter((item) => item.type === "bin_entered")) {
      const mid = payload.series.find((item) => item.id === `${candidate.bin_id}:mid`);
      if (!mid) continue;
      aligned.push(
        Array.from({ length: 191 }, (_, index) => {
          const target = candidate.time + (index - 10) * 60;
          const eligible = mid.points.filter((point) => point.time <= target);
          return eligible.at(-1)?.value ?? Number.NaN;
        }),
      );
    }
    const quantile = (values: number[], fraction: number): number => {
      const clean = values.filter(Number.isFinite).sort((left, right) => left - right);
      if (!clean.length) return Number.NaN;
      const index = (clean.length - 1) * fraction;
      const lower = Math.floor(index);
      const upper = Math.ceil(index);
      return clean[lower] + (clean[upper] - clean[lower]) * (index - lower);
    };
    for (const [id, name, fraction, color, style] of [
      ["aggregate-q25", "Q25", 0.25, "#55708d", "dotted"],
      ["aggregate-median", "Median", 0.5, "#60a5fa", "solid"],
      ["aggregate-q75", "Q75", 0.75, "#55708d", "dotted"],
    ] as const) {
      const points = Array.from({ length: 191 }, (_, index) => {
        const value = quantile(aligned.map((row) => row[index]), fraction);
        return { time: (base + (index - 10) * 60) as UTCTimestamp, value };
      }).filter((point) => Number.isFinite(point.value));
      const api = responseChart.addSeries(LineSeries, {
        title: name,
        color,
        lineWidth: id === "aggregate-median" ? 3 : 1,
        lineStyle: lineStyle(style),
        priceScaleId: "right",
      });
      api.setData(points);
      responseApis.set(id, api);
      responseData.set(id, points.map((point) => ({ time: Number(point.time), value: point.value })));
    }
    responseChart.timeScale().setVisibleRange({ from: (base - 600) as UTCTimestamp, to: (base + 10800) as UTCTimestamp });
    document.querySelector<HTMLElement>("#latency")!.innerHTML = `
      <div class="latency-label"><strong>Aligned event comparison</strong><br/>${aligned.length} Tmax bin-entry events</div>
      <div class="latency-track"><div class="segment pricing"><span>Median response</span><small>Q25 / Q75 bounds</small></div></div>`;
    return;
  }
  for (const spec of payload.series.filter((item) => item.group === "Market" || item.id === "running-tmax")) {
    const points = spec.points
      .filter((point) => point.time >= event.time - 600 && point.time <= event.time + 10800)
      .map((point) => ({ time: (base + point.time - event.time) as UTCTimestamp, value: point.value }));
    if (!points.length) continue;
    const api = responseChart.addSeries(LineSeries, {
      title: spec.name,
      color: styles[spec.id]?.color || spec.color,
      lineWidth: (styles[spec.id]?.width || 2) as 1 | 2 | 3 | 4,
      lineStyle: lineStyle(spec.lineStyle),
      priceScaleId: spec.axis,
      visible: visibility[spec.id] ?? spec.defaultVisible,
    });
    api.setData(points);
    responseApis.set(spec.id, api);
    responseData.set(spec.id, points.map((point) => ({ time: Number(point.time), value: point.value })));
  }
  responseChart.timeScale().setVisibleRange({ from: (base - 600) as UTCTimestamp, to: (base + 10800) as UTCTimestamp });
  const lead = event.tradable_lead_seconds ?? null;
  const thresholdAt = event.threshold_times?.[payload.threshold] || null;
  const pricingStart = event.first_market_move_at || event.received_at;
  const thresholdSeconds = thresholdAt ? new Date(thresholdAt).getTime() / 1000 - new Date(pricingStart).getTime() / 1000 : null;
  document.querySelector<HTMLElement>("#latency")!.innerHTML = `
    <div class="latency-label"><strong>${escapeHtml(event.title)}</strong><br/>Object ${formatTime(event.time, payload.objectTimezone)}<br/>Known ${formatTime(new Date(event.received_at).getTime() / 1000, payload.displayTimezone)}</div>
    <div class="latency-track">
      <div class="segment propagation"><span>Propagation</span><small>${durationLabel(event.source_latency_seconds ?? null)}</small></div>
      <div class="segment ${lead !== null && lead < 0 ? "lag" : "lead"}"><span>${lead !== null && lead < 0 ? "Market led" : "Tradable lead"}</span><small>${durationLabel(lead)}</small></div>
      <div class="segment pricing"><span>${Number(payload.threshold) * 100}% priced</span><small>${durationLabel(thresholdSeconds)}</small></div>
    </div>`;
}

function render(data: RenderData): void {
  payload = data.args.payload as Payload;
  if (!mainChart || !responseChart) buildShell();
  const fullReset = signature !== payload.signature;
  if (fullReset && signature && mainChart) {
    for (const api of seriesApis.values()) mainChart.removeSeries(api);
    seriesApis.clear();
    seriesData.clear();
    markersApi = null;
  }
  signature = payload.signature;
  for (const spec of payload.series) updateSeries(spec);
  renderLayers();
  renderEvents();
  renderResponse();
  if (fullReset) mainChart?.timeScale().fitContent();
  else if (following) mainChart?.timeScale().scrollToRealTime();
  announceFrameHeight();
  window.setTimeout(announceFrameHeight, 50);
}

Streamlit.events.addEventListener(Streamlit.RENDER_EVENT, (event) => {
  render((event as CustomEvent<RenderData>).detail);
});
Streamlit.setComponentReady();
window.setInterval(announceFrameHeight, 1000);
