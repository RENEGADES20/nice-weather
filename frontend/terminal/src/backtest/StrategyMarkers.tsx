import { createSeriesMarkers, type IChartApi, type ISeriesApi, type UTCTimestamp } from "lightweight-charts";
import { stages, type StrategyEvent, type Venue } from "./types";

const colors: Record<string, string> = {
  warning: "#a76d00", trigger: "#8d4bc1", candidate: "#336acc", order: "#596676",
  fill: "#087f68", rejection: "#c34242", diagnostic: "#7b8490",
};
export function scopedEvents(events: StrategyEvent[], venue: Venue, day: string, token: string) {
  return events.filter(e => e.venue === venue && e.day === day && e.token === token);
}

/** Shared by replay and live charts. No dependency on the backtest workspace. */
export function attachStrategyMarkers(
  chart: IChartApi, series: ISeriesApi<"Line">,
  selection: { venue: Venue; day: string; token: string }, events: StrategyEvent[],
  onLocate: (event: StrategyEvent) => void,
) {
  const selected = scopedEvents(events, selection.venue, selection.day, selection.token)
    .filter(e => e.stage !== "diagnostic").sort((a, b) => a.time - b.time);
  const markers = createSeriesMarkers(series, selected.map(e => ({
    id: e.id, time: e.time as UTCTimestamp,
    position: "aboveBar" as const,
    shape: e.stage === "fill" ? "arrowDown" as const : "circle" as const,
    color: colors[e.stage] ?? "#596676", text: `${e.strategy ?? ""} ${stages[e.stage] ?? e.stage}`,
  })));
  const click: Parameters<IChartApi["subscribeClick"]>[0] = param => {
    const event = selected.find(e => e.id === param.hoveredObjectId);
    if (event) onLocate(event);
  };
  chart.subscribeClick(click);
  return () => { chart.unsubscribeClick(click); markers.detach(); };
}
