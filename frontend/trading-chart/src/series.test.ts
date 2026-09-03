import { describe, expect, it } from "vitest";
import { appendOnly, downsample, durationLabel, mergePoints } from "./series";

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
});
