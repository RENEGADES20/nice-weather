import { describe, expect, it } from "vitest";
import {
  alignToMinuteGrid,
  createBidirectionalSync,
  differencePoints,
  mergeRawPoints,
  nonNullSegments,
  zAnchor,
} from "./difference";

describe("difference alignment", () => {
  it("aligns forecast by linear interpolation on a one-minute grid", () => {
    const result = alignToMinuteGrid({
      id: "forecast", fill: "forecast", points: [
        { time: 0, value: 70 }, { time: 120, value: 74 },
      ],
    }, 0, 120);
    expect(result.map((point) => [point.time, point.value])).toEqual([
      [0, 70], [60, 72], [120, 74],
    ]);
  });

  it("carries observations only through their freshness limit", () => {
    const result = alignToMinuteGrid({
      id: "metar", fill: "step-fresh", maxAgeSeconds: 90,
      points: [{ time: 0, value: 80 }],
    }, 0, 180);
    expect(result.map((point) => point.time)).toEqual([0, 60]);
  });

  it("stops day-held values at validTo and stops price at an explicit gap", () => {
    const day = alignToMinuteGrid({
      id: "weather-gov", fill: "step-day", validTo: 120,
      points: [{ time: 0, value: 81 }],
    }, 0, 180);
    const price = alignToMinuteGrid({
      id: "price", fill: "price",
      points: [{ time: 0, value: 0.4 }, { time: 120, value: null }],
    }, 0, 180);
    expect(day.map((point) => point.time)).toEqual([0, 60]);
    expect(price.map((point) => point.time)).toEqual([0, 60]);
  });

  it("computes frozen population z-score differences", () => {
    const reference = [0, 1, 2, 3, 4].map((value) => ({ time: value * 60, value, rawValue: value }));
    const other = [0, 2, 4, 6, 8].map((value, index) => ({ time: index * 60, value, rawValue: value }));
    const result = differencePoints(other, reference, zAnchor(other), zAnchor(reference));
    expect(result).not.toBeNull();
    expect(result!.every((point) => Math.abs(point.value) < 1e-10)).toBe(true);
  });

  it("rejects zero variance and insufficient overlap", () => {
    const flat = [0, 1, 2, 3, 4].map((value) => ({ time: value * 60, value: 1, rawValue: 1 }));
    const variable = [0, 1, 2, 3].map((value) => ({ time: value * 60, value, rawValue: value }));
    expect(zAnchor(flat)).toBeNull();
    expect(differencePoints(variable, variable, zAnchor(variable), zAnchor(variable))).toBeNull();
  });

  it("merges incremental points and replaces a revised historical minute", () => {
    expect(mergeRawPoints(
      [{ time: 0, value: 1 }, { time: 60, value: 2 }],
      [{ time: 60, value: 3 }, { time: 120, value: 4 }],
    )).toEqual([
      { time: 0, value: 1 }, { time: 60, value: 3 }, { time: 120, value: 4 },
    ]);
    expect(mergeRawPoints([], [
      { time: 60, value: 2 }, { time: 60, value: 3 },
    ])).toEqual([{ time: 60, value: 3 }]);
  });

  it("splits explicit gaps without passing null points to any chart series", () => {
    expect(nonNullSegments([
      { time: 0, value: 0.4 },
      { time: 60, value: null },
      { time: 120, value: 0.5 },
      { time: 180, value: 0.6 },
    ])).toEqual([
      [{ time: 0, value: 0.4 }],
      [{ time: 120, value: 0.5 }, { time: 180, value: 0.6 }],
    ]);
  });

  it("synchronizes either time axis without recursive feedback", () => {
    const left: number[] = [];
    const right: number[] = [];
    let sync: ReturnType<typeof createBidirectionalSync<number>>;
    sync = createBidirectionalSync(
      (value) => { left.push(value); sync.fromLeft(value); },
      (value) => { right.push(value); sync.fromRight(value); },
    );
    sync.fromLeft(10);
    sync.fromRight(20);
    expect(left).toEqual([20]);
    expect(right).toEqual([10]);
  });
});
