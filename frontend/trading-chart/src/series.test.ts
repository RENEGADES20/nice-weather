import { describe, expect, it } from "vitest";
import {
  appendOnly,
  compactDisplayEvents,
  downsample,
  durationLabel,
  mergePoints,
  proportionalPosition,
} from "./series";
import type { DisplayEvent } from "./series";

describe("timeline helpers", () => {
  it("merges revisions and preserves A to B to A", () => {
    expect(
      mergePoints(
        [{ time: 1, value: 0.2 }, { time: 2, value: 0.4 }],
        [{ time: 2, value: 0.5 }, { time: 3, value: 0.2 }],
      ),
    ).toEqual([
      { time: 1, value: 0.2 },
      { time: 2, value: 0.5 },
      { time: 3, value: 0.2 },
    ]);
  });

  it("detects append-only updates", () => {
    expect(appendOnly([{ time: 2, value: 1 }], [{ time: 3, value: 2 }])).toBe(true);
    expect(appendOnly([{ time: 2, value: 1 }], [{ time: 1, value: 2 }])).toBe(false);
  });

  it("formats signed latency", () => {
    expect(durationLabel(115)).toBe("+1m55s");
    expect(durationLabel(-70)).toBe("-1m10s");
  });

  it("downsamples while retaining extrema and endpoints", () => {
    const points = Array.from({ length: 100 }, (_, time) => ({ time, value: time === 51 ? 999 : time }));
    const sampled = downsample(points, 20);
    expect(sampled.length).toBeLessThanOrEqual(20);
    expect(sampled[0]).toEqual(points[0]);
    expect(sampled.at(-1)).toEqual(points.at(-1));
    expect(sampled.some((point) => point.value === 999)).toBe(true);
  });

  it("compacts repeated forecast revisions without changing other events", () => {
    const events = compactDisplayEvents<DisplayEvent>([
      { id: "a", type: "forecast_revised", time: 1_800, temperature_f: 84 },
      { id: "b", type: "forecast_revised", time: 1_900, temperature_f: 84 },
      { id: "c", type: "forecast_revised", time: 2_000, temperature_f: 85 },
      { id: "d", type: "forecast_revised", time: 2_200, temperature_f: 86 },
      { id: "e", type: "bin_entered", time: 2_300 },
    ]);

    expect(events.map((event) => event.id)).toEqual(["d", "e"]);
    expect(events[0].groupCount).toBe(3);
  });

  it("places latency nodes on a shared proportional scale", () => {
    expect(proportionalPosition(-60, -60, 180)).toBe(0);
    expect(proportionalPosition(60, -60, 180)).toBe(50);
    expect(proportionalPosition(180, -60, 180)).toBe(100);
  });
});
