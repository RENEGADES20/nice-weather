import {
  ColorType,
  CrosshairMode,
  LineSeries,
  LineStyle,
  createChart,
  createSeriesMarkers,
  type IChartApi,
  type IPriceLine,
  type ISeriesApi,
  type ISeriesMarkersPluginApi,
  type LineData,
  type SeriesMarker,
  type Time,
  type UTCTimestamp,
} from "lightweight-charts";
import { Expand, Eye, LocateFixed, RotateCcw, createIcons } from "lucide";
import { Streamlit, type RenderData } from "streamlit-component-lib";
import {
  compactDisplayEvents,
  downsample,
  durationLabel,
  mergePoints,
  proportionalPosition,
  type Point,
} from "./series";
import "./style.css";

type ChartPoint = Point & { received_at?: string; object_time?: string };
type PaneName = "weather" | "market" | "response";
type ValueFormat = "temperature" | "probability";
type SeriesRole = "primary" | "context" | "bid" | "ask" | "trade" | "fallback";

type SeriesSpec = {
  id: string;
  name: string;
  group: "Weather" | "Market";
  axis: "left" | "right";
  pane: PaneName;
  format: ValueFormat;
  role: SeriesRole;
  color: string;
  lineStyle?: "solid" | "dashed" | "dotted";
  defaultVisible: boolean;
  points: ChartPoint[];
};

type KnowledgeEvent = {
  id: string;
  type: string;
  title: string;
  shortLabel: string;
  displayPriority: number;
  groupCount: number;
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
  focusBinId: string;
  series: SeriesSpec[];
  events: KnowledgeEvent[];
  uiState?: {
    visibility?: Record<string, boolean>;
    responseVisibility?: Record<string, boolean>;
    eventVisibility?: Record<string, boolean>;
    selectedEventId?: string;
    comparisonMode?: boolean;
  };
};

type Theme = {
  primary: string;
  background: string;
  secondary: string;
  border: string;
  text: string;
  muted: string;
  grid: string;
};

const fallbackTheme: Theme = {
  primary: "#ff4b4b",
  background: "#ffffff",
  secondary: "#f6f8fb",
  border: "#e2e7ee",
  text: "#172033",
  muted: "#667085",
  grid: "#edf1f5",
};

const root = document.querySelector<HTMLElement>("#app")!;
let theme = fallbackTheme;
let mainChart: IChartApi | null = null;
let responseChart: IChartApi | null = null;
let payload: Payload | null = null;
let signature = "";
let following = true;
let selectedEventId = "";
let comparisonMode = localStorage.getItem("nice-weather-event-mode") === "compare";
let syncingCrosshair = false;
let lastFrameHeight = 0;
let layerSignature = "";
let resizeObserver: ResizeObserver | null = null;
let markersApi: ISeriesMarkersPluginApi<Time> | null = null;
let thresholdLine: IPriceLine | null = null;
let thresholdOwner: ISeriesApi<"Line"> | null = null;

const seriesApis = new Map<string, ISeriesApi<"Line">>();
const seriesData = new Map<string, Point[]>();
const responseApis = new Map<string, ISeriesApi<"Line">>();
const responseData = new Map<string, Point[]>();

function storedObject<T>(key: string): T {
  try {
    return JSON.parse(localStorage.getItem(key) || "{}") as T;
  } catch {
    localStorage.removeItem(key);
    return {} as T;
  }
}

const visibility = storedObject<Record<string, boolean>>("nice-weather-layers");
const responseVisibility = storedObject<Record<string, boolean>>("nice-weather-response-layers");
const styles = storedObject<Record<string, { color?: string; width?: number }>>("nice-weather-styles");
const eventVisibility: Record<string, boolean> = {
  bin_entered: true,
  bin_eliminated: true,
  sunset: true,
  dusk: true,
  forecast_revised: false,
  ...storedObject<Record<string, boolean>>("nice-weather-events"),
};

function escapeHtml(value: string): string {
  return value.replace(/[&<>'"]/g, (character) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", "'": "&#39;", '"': "&quot;",
  })[character]!);
}

function lineStyle(value?: string): LineStyle {
  if (value === "dashed") return LineStyle.Dashed;
  if (value === "dotted") return LineStyle.Dotted;
  return LineStyle.Solid;
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

function formatRelative(seconds: number): string {
  const sign = seconds >= 0 ? "+" : "-";
  return `${sign}${Math.abs(Math.round(seconds / 60))}m`;
}

function formatValue(spec: SeriesSpec, value: number): string {
  return spec.format === "probability" ? `${(value * 100).toFixed(1)}%` : `${value.toFixed(1)} F`;
}

function resolveTheme(data: RenderData): Theme {
  const incoming = data.theme;
  return {
    ...fallbackTheme,
    primary: incoming?.primaryColor || fallbackTheme.primary,
    background: incoming?.backgroundColor || fallbackTheme.background,
    secondary: incoming?.secondaryBackgroundColor || fallbackTheme.secondary,
    text: incoming?.textColor || fallbackTheme.text,
  };
}

function chartOptions(relative = false) {
  const formatter = relative
    ? (chartTime: Time) => formatRelative(Number(chartTime) - 1_700_000_000)
    : (chartTime: Time) => formatTime(Number(chartTime), payload?.displayTimezone);
  return {
    autoSize: true,
    layout: {
      background: { type: ColorType.Solid, color: theme.background },
      textColor: theme.muted,
      attributionLogo: false,
      panes: { separatorColor: theme.border, separatorHoverColor: theme.primary, enableResize: true },
    },
    grid: {
      vertLines: { color: theme.grid },
      horzLines: { color: theme.grid },
    },
    crosshair: { mode: CrosshairMode.Normal },
    leftPriceScale: { visible: true, borderColor: theme.border },
    rightPriceScale: { visible: true, borderColor: theme.border },
    timeScale: {
      borderColor: theme.border,
      timeVisible: true,
      secondsVisible: true,
      tickMarkFormatter: formatter,
    },
    handleScroll: { mouseWheel: true, pressedMouseMove: true, horzTouchDrag: true },
    handleScale: { mouseWheel: true, pinch: true, axisPressedMouseMove: true },
  };
}

function applyTheme(next: Theme): void {
  theme = next;
  for (const [name, value] of Object.entries(theme)) root.style.setProperty(`--${name}`, value);
  mainChart?.applyOptions(chartOptions());
  responseChart?.applyOptions(chartOptions(true));
}

function announceFrameHeight(): void {
  const height = Math.max(780, Math.ceil(root.scrollHeight));
  if (height === lastFrameHeight) return;
  lastFrameHeight = height;
  Streamlit.setFrameHeight(height);
}

function eventColor(event: KnowledgeEvent): string {
  if (event.id === selectedEventId) return theme.primary;
  if (event.type === "forecast_revised") return "#356ae680";
  if (event.type === "bin_eliminated") return "#c65a5580";
  if (event.type === "sunset" || event.type === "dusk") return "#8a6bb880";
  return "#1f7a6880";
}

function publishUiState(): void {
  Streamlit.setComponentValue({
    visibility,
    responseVisibility,
    eventVisibility,
    selectedEventId,
    comparisonMode,
  });
}

function buildShell(): void {
  root.innerHTML = `
    <div class="shell" id="shell">
      <div class="toolbar">
        <button class="toolbar-command" id="layers-button" title="Layers"><i data-lucide="eye"></i><span>Layers</span></button>
        <select class="event-select" id="mode-select" title="Delay view"><option value="single">Single event</option><option value="compare">Compare events</option></select>
        <select class="event-select event-picker" id="event-select" title="Tmax event"></select>
        <span class="spacer"></span>
        <button class="tool-button" id="follow-button" title="Follow latest"><i data-lucide="locate-fixed"></i></button>
        <button class="tool-button" id="reset-button" title="Reset view"><i data-lucide="rotate-ccw"></i></button>
        <button class="tool-button" id="fullscreen-button" title="Full screen"><i data-lucide="expand"></i></button>
      </div>
      <div class="layers hidden" id="layers"></div>
      <div class="timeline-head">
        <div class="pane-legend" id="weather-legend"></div>
        <div class="pane-legend" id="market-legend"></div>
      </div>
      <div class="chart-wrap">
        <div class="event-cursor hidden" id="event-cursor"><span id="event-cursor-label"></span></div>
        <div id="main-chart"></div>
      </div>
      <div class="event-summary" id="event-summary"></div>
      <div class="section-title"><span>Price-in delay</span><span class="section-note" id="response-status"></span></div>
      <div class="chart-wrap response-wrap">
        <div class="pane-legend" id="response-legend"></div>
        <div id="response-chart"></div>
      </div>
      <div class="latency" id="latency"></div>
    </div>`;
  createIcons({ icons: { Expand, Eye, LocateFixed, RotateCcw } });

  mainChart = createChart(document.querySelector<HTMLElement>("#main-chart")!, chartOptions());
  const weatherPane = mainChart.panes()[0];
  const marketPane = mainChart.addPane();
  weatherPane.setStretchFactor(65);
  marketPane.setStretchFactor(35);
  responseChart = createChart(document.querySelector<HTMLElement>("#response-chart")!, chartOptions(true));

  const mainElement = document.querySelector<HTMLElement>("#main-chart")!;
  mainElement.addEventListener("wheel", () => { following = false; }, { passive: true });
  mainElement.addEventListener("pointerdown", () => { following = false; });
  mainChart.subscribeCrosshairMove((param) => {
    updateMainLegend(param.time, param.seriesData);
    if (!syncingCrosshair) syncResponseCrosshair(param.time);
  });
  responseChart.subscribeCrosshairMove((param) => {
    updateResponseLegend(param.time, param.seriesData);
    if (!syncingCrosshair) syncMainCrosshair(param.time);
  });
  mainChart.subscribeClick((param) => {
    const markerId = param.hoveredObjectId ? String(param.hoveredObjectId) : "";
    if (!payload?.events.some((event) => event.id === markerId)) return;
    selectedEventId = markerId;
    renderEvents();
    renderResponse();
    publishUiState();
  });
  mainChart.timeScale().subscribeVisibleTimeRangeChange(renderEventCursor);

  document.querySelector("#layers-button")?.addEventListener("click", () => {
    document.querySelector("#layers")?.classList.toggle("hidden");
    announceFrameHeight();
  });
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
    renderEvents();
    renderResponse();
    publishUiState();
  });
  const modeSelect = document.querySelector<HTMLSelectElement>("#mode-select")!;
  modeSelect.value = comparisonMode ? "compare" : "single";
  modeSelect.addEventListener("change", () => {
    comparisonMode = modeSelect.value === "compare";
    localStorage.setItem("nice-weather-event-mode", modeSelect.value);
    renderResponse();
    publishUiState();
  });
  document.addEventListener("fullscreenchange", announceFrameHeight);
  resizeObserver = new ResizeObserver(() => {
    renderEventCursor();
    announceFrameHeight();
  });
  resizeObserver.observe(root);
  window.requestAnimationFrame(announceFrameHeight);
}

function specWidth(spec: SeriesSpec): 1 | 2 | 3 | 4 {
  const stored = styles[spec.id]?.width;
  if (stored) return stored as 1 | 2 | 3 | 4;
  if (spec.role === "primary" || spec.id === `${payload?.focusBinId}:mid`) return 3;
  if (spec.role === "bid" || spec.role === "ask" || spec.role === "fallback") return 1;
  return 2;
}

function seriesOptions(spec: SeriesSpec) {
  return {
    title: "",
    color: styles[spec.id]?.color || spec.color,
    lineWidth: specWidth(spec),
    lineStyle: lineStyle(spec.lineStyle),
    priceScaleId: spec.axis,
    visible: visibility[spec.id] ?? spec.defaultVisible,
    priceLineVisible: false,
    lastValueVisible: false,
    crosshairMarkerVisible: true,
    priceFormat: spec.format === "probability"
      ? { type: "custom" as const, minMove: 0.001, formatter: (value: number) => `${(value * 100).toFixed(0)}%` }
      : { type: "price" as const, precision: 1, minMove: 0.1 },
  };
}

function reconcileMainSeries(specs: SeriesSpec[]): void {
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
    } else {
      api.applyOptions(seriesOptions(spec));
      api.moveToPane(spec.pane === "market" ? 1 : 0);
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
      for (const point of merged.slice(firstDifference)) api.update({ time: point.time as UTCTimestamp, value: point.value });
    } else if (JSON.stringify(previous) !== JSON.stringify(merged)) {
      api.setData(merged.map((point) => ({ time: point.time as UTCTimestamp, value: point.value })));
    }
    seriesData.set(spec.id, merged);
  }
}

function latestLegendPoint(spec: SeriesSpec): Point | undefined {
  return seriesData.get(spec.id)?.at(-1);
}

function legendItems(group: "Weather" | "Market", values?: ReadonlyMap<unknown, unknown>): string {
  if (!payload) return "";
  return payload.series
    .filter((spec) => spec.group === group && (visibility[spec.id] ?? spec.defaultVisible))
    .map((spec) => {
      const api = seriesApis.get(spec.id);
      const crosshair = api && values ? values.get(api) as LineData | undefined : undefined;
      const point = crosshair && "value" in crosshair ? { value: crosshair.value } : latestLegendPoint(spec);
      const value = point ? formatValue(spec, point.value) : "Unavailable";
      const color = styles[spec.id]?.color || spec.color;
      return `<span class="legend-item"><i style="background:${escapeHtml(color)}"></i>${escapeHtml(spec.name)} <strong>${escapeHtml(value)}</strong></span>`;
    }).join("");
}

function updateMainLegend(chartTime?: Time, values?: ReadonlyMap<unknown, unknown>): void {
  if (!payload) return;
  const latest = Math.max(...payload.series.flatMap((spec) => spec.points.map((point) => point.time)), 0);
  const instant = Number(chartTime ?? latest);
  const stamp = instant > 0
    ? `${formatTime(instant, payload.displayTimezone)} / ${formatTime(instant, payload.objectTimezone)}`
    : "Unavailable";
  document.querySelector<HTMLElement>("#weather-legend")!.innerHTML = `<span class="legend-time">${escapeHtml(stamp)}</span>${legendItems("Weather", values)}`;
  document.querySelector<HTMLElement>("#market-legend")!.innerHTML = legendItems("Market", values);
}

function updateResponseLegend(chartTime: Time | undefined, values: ReadonlyMap<unknown, unknown>): void {
  if (!payload) return;
  const labels: string[] = [];
  for (const spec of payload.series) {
    const api = responseApis.get(spec.id);
    const value = api ? values.get(api) as LineData | undefined : undefined;
    if (value && "value" in value) labels.push(`${spec.name} ${formatValue(spec, value.value)}`);
  }
  const instant = chartTime === undefined ? "" : `Delta ${formatRelative(Number(chartTime) - 1_700_000_000)}`;
  document.querySelector<HTMLElement>("#response-legend")!.textContent = `${instant}  ${labels.join("  ") || "Unavailable"}`.trim();
}

function renderLayers(): void {
  if (!payload) return;
  const nextSignature = JSON.stringify(payload.series.map((spec) => [spec.id, spec.name, spec.defaultVisible]));
  if (nextSignature === layerSignature) return;
  layerSignature = nextSignature;
  const container = document.querySelector<HTMLElement>("#layers")!;
  const seriesGroups = (["Weather", "Market"] as const).map((group) => `
    <div class="layer-group">${group}</div>
    ${payload!.series.filter((item) => item.group === group).map((spec) => `
      <label class="layer-row">
        <input type="checkbox" data-layer="${escapeHtml(spec.id)}" ${(visibility[spec.id] ?? spec.defaultVisible) ? "checked" : ""}/>
        <span class="layer-swatch" style="background:${escapeHtml(styles[spec.id]?.color || spec.color)}"></span>
        <span>${escapeHtml(spec.name)}</span>
      </label>
    `).join("")}`).join("");
  const eventRows = [
    ["bin_entered", "Bin entered"],
    ["bin_eliminated", "Bin eliminated"],
    ["forecast_revised", "Forecast revisions"],
    ["sunset", "Sunset"],
    ["dusk", "Civil twilight"],
  ].map(([id, label]) => `<label class="layer-row"><input type="checkbox" data-event="${id}" ${eventVisibility[id] ? "checked" : ""}/><span>${label}</span></label>`).join("");
  const responseRows = payload.series
    .filter((spec) => spec.id === "running-tmax" || spec.role === "ask" || (spec.role === "context" && spec.id.endsWith(":mid")))
    .map((spec) => `<label class="layer-row"><input type="checkbox" data-response="${escapeHtml(spec.id)}" ${responseVisibility[spec.id] ? "checked" : ""}/><span class="layer-swatch" style="background:${escapeHtml(styles[spec.id]?.color || spec.color)}"></span><span>${escapeHtml(spec.name)}</span></label>`)
    .join("");
  const advanced = payload.series.map((spec) => `
    <label class="style-row"><input type="color" data-color="${escapeHtml(spec.id)}" value="${styles[spec.id]?.color || spec.color}" title="Line color"/><select data-width="${escapeHtml(spec.id)}" title="Line width">${[1, 2, 3, 4].map((width) => `<option value="${width}" ${specWidth(spec) === width ? "selected" : ""}>${width}px</option>`).join("")}</select><span>${escapeHtml(spec.name)}</span></label>
  `).join("");
  container.innerHTML = `${seriesGroups}<div class="layer-group">Response</div>${responseRows}<div class="layer-group">Events</div>${eventRows}<details><summary>Line styles</summary>${advanced}</details>`;
  container.querySelectorAll<HTMLInputElement>("[data-layer]").forEach((input) => {
    input.addEventListener("change", () => {
      const id = input.dataset.layer!;
      visibility[id] = input.checked;
      localStorage.setItem("nice-weather-layers", JSON.stringify(visibility));
      seriesApis.get(id)?.applyOptions({ visible: input.checked });
      responseApis.get(id)?.applyOptions({ visible: input.checked });
      updateMainLegend();
      publishUiState();
    });
  });
  container.querySelectorAll<HTMLInputElement>("[data-event]").forEach((input) => {
    input.addEventListener("change", () => {
      eventVisibility[input.dataset.event!] = input.checked;
      localStorage.setItem("nice-weather-events", JSON.stringify(eventVisibility));
      renderEvents();
      publishUiState();
    });
  });
  container.querySelectorAll<HTMLInputElement>("[data-response]").forEach((input) => {
    input.addEventListener("change", () => {
      const id = input.dataset.response!;
      responseVisibility[id] = input.checked;
      localStorage.setItem("nice-weather-response-layers", JSON.stringify(responseVisibility));
      renderResponse();
      publishUiState();
    });
  });
  container.querySelectorAll<HTMLInputElement>("[data-color]").forEach((input) => {
    input.addEventListener("change", () => {
      const id = input.dataset.color!;
      styles[id] = { ...styles[id], color: input.value };
      localStorage.setItem("nice-weather-styles", JSON.stringify(styles));
      seriesApis.get(id)?.applyOptions({ color: input.value });
      responseApis.get(id)?.applyOptions({ color: input.value });
      layerSignature = "";
      renderLayers();
      updateMainLegend();
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

function visibleEvents(): KnowledgeEvent[] {
  if (!payload) return [];
  return compactDisplayEvents(payload.events).filter((event) => eventVisibility[event.type] ?? true);
}

function renderEvents(): void {
  if (!payload || !mainChart) return;
  const selector = document.querySelector<HTMLSelectElement>("#event-select")!;
  const selectable = compactDisplayEvents(payload.events);
  const selectorSignature = selectable.map((event) => event.id).join("|");
  if (selector.dataset.signature !== selectorSignature) {
    selector.innerHTML = selectable.map((event) => `<option value="${escapeHtml(event.id)}">${escapeHtml(event.title)}${event.groupCount > 1 ? ` (${event.groupCount})` : ""}</option>`).join("");
    selector.dataset.signature = selectorSignature;
  }
  if (!selectable.some((event) => event.id === selectedEventId)) {
    selectedEventId = selectable.find((event) => event.bin_id === payload?.focusBinId)?.id || selectable.at(-1)?.id || "";
  }
  selector.disabled = selectable.length === 0;
  selector.value = selectedEventId;

  const anchor = payload.series.find((item) => item.id === "running-tmax");
  const api = anchor ? seriesApis.get(anchor.id) : undefined;
  if (api) {
    const markers: SeriesMarker<Time>[] = visibleEvents().map((event) => ({
      id: event.id,
      time: event.time as UTCTimestamp,
      position: event.type === "bin_eliminated" ? "belowBar" : "aboveBar",
      color: eventColor(event),
      shape: event.type === "forecast_revised" ? "square" : event.type === "bin_eliminated" ? "arrowDown" : "circle",
      text: "",
      size: event.id === selectedEventId ? 1.05 : 0.55,
    }));
    if (markersApi) markersApi.setMarkers(markers);
    else markersApi = createSeriesMarkers(api, markers);
  }
  renderEventCursor();
  renderEventSummary();
}

function renderEventCursor(): void {
  const cursor = document.querySelector<HTMLElement>("#event-cursor");
  const label = document.querySelector<HTMLElement>("#event-cursor-label");
  const event = payload?.events.find((item) => item.id === selectedEventId);
  const x = event && mainChart ? mainChart.timeScale().timeToCoordinate(event.time as UTCTimestamp) : null;
  if (!cursor || !label || !event || x === null) {
    cursor?.classList.add("hidden");
    return;
  }
  cursor.classList.remove("hidden");
  cursor.style.left = `${x}px`;
  label.textContent = event.shortLabel;
}

function renderEventSummary(): void {
  const container = document.querySelector<HTMLElement>("#event-summary")!;
  const event = payload?.events.find((item) => item.id === selectedEventId);
  if (!event || !payload) {
    container.textContent = "No Tmax event available";
    return;
  }
  const group = event.groupCount > 1 ? `<span class="event-count">${event.groupCount} revisions</span>` : "";
  container.innerHTML = `<strong>${escapeHtml(event.title)}</strong>${group}<span>Object ${escapeHtml(formatTime(event.time, payload.objectTimezone))}</span><span>Known ${escapeHtml(formatTime(new Date(event.received_at).getTime() / 1000, payload.displayTimezone))}</span><span>Propagation ${escapeHtml(durationLabel(event.source_latency_seconds ?? null))}</span>`;
}

function closestPoint(points: Point[], timeValue: number): Point | undefined {
  return points.reduce<Point | undefined>((best, point) => (
    !best || Math.abs(point.time - timeValue) < Math.abs(best.time - timeValue) ? point : best
  ), undefined);
}

function syncResponseCrosshair(chartTime: Time | undefined): void {
  if (!responseChart || chartTime === undefined) {
    responseChart?.clearCrosshairPosition();
    return;
  }
  const event = payload?.events.find((item) => item.id === selectedEventId);
  const first = responseApis.entries().next().value as [string, ISeriesApi<"Line">] | undefined;
  if (!event || !first) return;
  const relativeTime = 1_700_000_000 + Number(chartTime) - event.time;
  const point = closestPoint(responseData.get(first[0]) || [], relativeTime);
  if (!point) return;
  syncingCrosshair = true;
  responseChart.setCrosshairPosition(point.value, relativeTime as UTCTimestamp, first[1]);
  syncingCrosshair = false;
}

function syncMainCrosshair(chartTime: Time | undefined): void {
  if (!mainChart || chartTime === undefined) {
    mainChart?.clearCrosshairPosition();
    return;
  }
  const event = payload?.events.find((item) => item.id === selectedEventId);
  const api = seriesApis.get("running-tmax") || seriesApis.values().next().value;
  if (!event || !api) return;
  const absoluteTime = event.time + Number(chartTime) - 1_700_000_000;
  const point = closestPoint(seriesData.get("running-tmax") || [], absoluteTime);
  if (!point) return;
  syncingCrosshair = true;
  mainChart.setCrosshairPosition(point.value, absoluteTime as UTCTimestamp, api);
  syncingCrosshair = false;
}

type ResponseSpec = {
  id: string; name: string; color: string; width: 1 | 2 | 3; style: string;
  axis: string; visible: boolean; format: ValueFormat; points: Point[];
};

function reconcileResponseSeries(specs: ResponseSpec[]): void {
  if (!responseChart) return;
  const ids = new Set(specs.map((spec) => spec.id));
  for (const [id, api] of responseApis) {
    if (ids.has(id)) continue;
    if (thresholdOwner === api && thresholdLine) api.removePriceLine(thresholdLine);
    responseChart.removeSeries(api);
    responseApis.delete(id);
    responseData.delete(id);
  }
  for (const spec of specs) {
    let api = responseApis.get(spec.id);
    const options = {
      title: "",
      color: spec.color,
      lineWidth: spec.width,
      lineStyle: lineStyle(spec.style),
      priceScaleId: spec.axis,
      visible: spec.visible,
      priceLineVisible: false,
      lastValueVisible: false,
      priceFormat: spec.format === "probability"
        ? { type: "custom" as const, minMove: 0.001, formatter: (value: number) => `${(value * 100).toFixed(0)}%` }
        : { type: "price" as const, precision: 1, minMove: 0.1 },
    };
    if (!api) {
      api = responseChart.addSeries(LineSeries, options);
      responseApis.set(spec.id, api);
    } else api.applyOptions(options);
    const previous = responseData.get(spec.id) || [];
    if (JSON.stringify(previous) !== JSON.stringify(spec.points)) {
      api.setData(spec.points.map((point) => ({ time: point.time as UTCTimestamp, value: point.value })));
      responseData.set(spec.id, spec.points);
    }
  }
}

function quantile(values: number[], fraction: number): number {
  const clean = values.filter(Number.isFinite).sort((left, right) => left - right);
  if (!clean.length) return Number.NaN;
  const index = (clean.length - 1) * fraction;
  const lower = Math.floor(index);
  const upper = Math.ceil(index);
  return clean[lower] + (clean[upper] - clean[lower]) * (index - lower);
}

function renderComparison(base: number, status: HTMLElement): ResponseSpec[] | null {
  if (!payload) return null;
  const aligned: number[][] = [];
  for (const candidate of payload.events.filter((item) => item.type === "bin_entered")) {
    const mid = payload.series.find((item) => item.id === `${candidate.bin_id}:mid`);
    if (!mid) continue;
    aligned.push(Array.from({ length: 191 }, (_, index) => {
      const target = candidate.time + (index - 10) * 60;
      return mid.points.filter((point) => point.time <= target).at(-1)?.value ?? Number.NaN;
    }));
  }
  if (aligned.length < 2) {
    status.textContent = "Insufficient events";
    document.querySelector<HTMLElement>("#latency")!.innerHTML = `<div class="empty">Insufficient events</div>`;
    return null;
  }
  status.textContent = `${aligned.length} aligned events`;
  document.querySelector<HTMLElement>("#latency")!.innerHTML = `<div class="latency-summary"><strong>Aligned response</strong><span>Median with Q25-Q75 bounds</span></div>`;
  return ([
    ["aggregate-q25", "Q25", 0.25, "#98a2b3", 1, "dotted"],
    ["aggregate-median", "Median", 0.5, "#356ae6", 3, "solid"],
    ["aggregate-q75", "Q75", 0.75, "#98a2b3", 1, "dotted"],
  ] as const).map(([id, name, fraction, color, width, style]) => ({
    id, name, color, width, style, axis: "right", visible: true, format: "probability" as const,
    points: Array.from({ length: 191 }, (_, index) => ({
      time: base + (index - 10) * 60,
      value: quantile(aligned.map((row) => row[index]), fraction),
    })).filter((point) => Number.isFinite(point.value)),
  }));
}

function renderResponse(): void {
  if (!payload || !responseChart) return;
  const event = payload.events.find((item) => item.id === selectedEventId);
  const status = document.querySelector<HTMLElement>("#response-status")!;
  if (!event) {
    reconcileResponseSeries([]);
    document.querySelector<HTMLElement>("#latency")!.innerHTML = `<div class="empty">No Tmax event available</div>`;
    status.textContent = "";
    return;
  }
  const base = 1_700_000_000;
  let specs: ResponseSpec[] = [];
  if (comparisonMode) {
    specs = renderComparison(base, status) || [];
  } else {
    for (const series of payload.series) {
      const isFocusMid = series.id === `${payload.focusBinId}:mid`;
      const isFocusAsk = series.id === `${payload.focusBinId}:best_ask`;
      const isRunning = series.id === "running-tmax";
      const isContextMid = series.role === "context" && series.id.endsWith(":mid");
      if (!isFocusMid && !isFocusAsk && !isRunning && !isContextMid) continue;
      const points = series.points
        .filter((point) => point.time >= event.time - 600 && point.time <= event.time + 10800)
        .map((point) => ({ time: base + point.time - event.time, value: point.value }));
      if (!points.length) continue;
      specs.push({
        id: series.id,
        name: series.name,
        color: styles[series.id]?.color || series.color,
        width: isFocusMid ? 3 : 1,
        style: series.lineStyle || "solid",
        axis: series.axis,
        visible: isFocusMid || (responseVisibility[series.id] ?? false),
        format: series.format,
        points,
      });
    }
    status.textContent = event.shortLabel;
    renderLatency(event);
  }
  reconcileResponseSeries(specs);
  responseChart.applyOptions({
    leftPriceScale: {
      visible: specs.some((spec) => spec.axis === "left" && spec.visible),
      borderColor: theme.border,
    },
    rightPriceScale: { visible: true, borderColor: theme.border },
  });
  if (thresholdOwner && thresholdLine) thresholdOwner.removePriceLine(thresholdLine);
  thresholdLine = null;
  thresholdOwner = responseApis.get(`${payload.focusBinId}:mid`) || null;
  if (thresholdOwner && !comparisonMode) {
    thresholdLine = thresholdOwner.createPriceLine({
      price: Number(payload.threshold), color: "#356ae6", lineStyle: LineStyle.Dashed,
      lineWidth: 1, axisLabelVisible: false, title: `${Number(payload.threshold) * 100}%`,
    });
  }
  responseChart.timeScale().setVisibleRange({ from: (base - 600) as UTCTimestamp, to: (base + 10800) as UTCTimestamp });
}

function renderLatency(event: KnowledgeEvent): void {
  if (!payload) return;
  const object = event.time;
  const known = new Date(event.received_at).getTime() / 1000;
  const market = event.first_market_move_at ? new Date(event.first_market_move_at).getTime() / 1000 : null;
  const thresholdAt = event.threshold_times?.[payload.threshold]
    ? new Date(event.threshold_times[payload.threshold]!).getTime() / 1000
    : null;
  const offsets = [0, known - object, market === null ? null : market - object, thresholdAt === null ? null : thresholdAt - object];
  const finite = offsets.filter((value): value is number => value !== null && Number.isFinite(value));
  const minimum = Math.min(-60, ...finite);
  const maximum = Math.max(60, ...finite);
  const position = (value: number) => proportionalPosition(value, minimum, maximum);
  const segment = (start: number, end: number | null, css: string) => end === null ? "" : `<span class="latency-segment ${css}" style="left:${position(Math.min(start, end))}%;width:${Math.max(1, Math.abs(position(end) - position(start)))}%"></span>`;
  const node = (value: number | null, label: string, detail: string, kind: string) => value === null ? "" : `<span class="latency-node ${kind}" style="left:${position(value)}%"><i></i><strong>${label}</strong><small>${escapeHtml(detail)}</small></span>`;
  const detail = (value: number | null, label: string) => value === null ? "" : `<span><strong>${label}</strong><small>${escapeHtml(durationLabel(value))}</small></span>`;
  const marketOffset = market === null ? null : market - object;
  const thresholdOffset = thresholdAt === null ? null : thresholdAt - object;
  const lead = event.tradable_lead_seconds ?? null;
  document.querySelector<HTMLElement>("#latency")!.innerHTML = `
    <div class="latency-summary"><strong>${escapeHtml(event.title)}</strong><span>${lead !== null && lead < 0 ? "Market led" : "System lead"} ${escapeHtml(durationLabel(lead))}</span></div>
    <div class="latency-scale">
      <div class="latency-axis"></div>
      ${segment(0, known - object, "propagation")}
      ${segment(known - object, marketOffset, lead !== null && lead < 0 ? "lag" : "lead")}
      ${marketOffset === null ? "" : segment(marketOffset, thresholdOffset, "pricing")}
      ${node(0, "Object", "0s", "object")}
      ${node(known - object, "Known", durationLabel(known - object), "known")}
      ${node(marketOffset, "First move", durationLabel(marketOffset), "market")}
      ${node(thresholdOffset, `${Number(payload.threshold) * 100}%`, durationLabel(thresholdOffset), "threshold")}
    </div>
    <div class="latency-details">
      ${detail(0, "Object")}
      ${detail(known - object, "Known")}
      ${detail(marketOffset, "First move")}
      ${detail(thresholdOffset, `${Number(payload.threshold) * 100}%`)}
    </div>`;
  window.requestAnimationFrame(announceFrameHeight);
}

function render(data: RenderData): void {
  payload = data.args.payload as Payload;
  if (signature === "" && payload.uiState) {
    if (localStorage.getItem("nice-weather-layers") === null) {
      Object.assign(visibility, payload.uiState.visibility || {});
    }
    if (localStorage.getItem("nice-weather-response-layers") === null) {
      Object.assign(responseVisibility, payload.uiState.responseVisibility || {});
    }
    if (localStorage.getItem("nice-weather-events") === null) {
      Object.assign(eventVisibility, payload.uiState.eventVisibility || {});
    }
    if (localStorage.getItem("nice-weather-event-mode") === null) {
      comparisonMode = payload.uiState.comparisonMode ?? comparisonMode;
    }
    selectedEventId = payload.uiState.selectedEventId || selectedEventId;
  }
  applyTheme(resolveTheme(data));
  if (!mainChart || !responseChart) buildShell();
  const firstRender = signature === "";
  const changedSelection = !firstRender && signature !== payload.signature;
  signature = payload.signature;
  reconcileMainSeries(payload.series);
  renderLayers();
  renderEvents();
  renderResponse();
  updateMainLegend();
  if (firstRender || changedSelection) mainChart?.timeScale().fitContent();
  else if (following) mainChart?.timeScale().scrollToRealTime();
  window.requestAnimationFrame(announceFrameHeight);
}

Streamlit.events.addEventListener(Streamlit.RENDER_EVENT, (event) => {
  render((event as CustomEvent<RenderData>).detail);
});
Streamlit.setComponentReady();
