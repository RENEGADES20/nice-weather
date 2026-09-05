import { describe, expect, it } from "vitest";
import {
  createBidirectionalSync,
  differencePoints,
  mergeRawPoints,
  nonNullSegments,
} from "./difference";

describe("difference alignment", () => {
  it("computes ordinary left-minus-right values and preserves input metadata", () => {
    const left = [
      { time: 0, value: 72, objectTime: "left-0" },
      { time: 60, value: 75, objectTime: "left-60" },
    ];
    const right = [
      { time: 0, value: 70, objectTime: "right-0" },
      { time: 60, value: 76, objectTime: "right-60" },
    ];
    expect(differencePoints(left, right)).toEqual([
      { time: 0, value: 2, left: left[0], right: right[0] },
      { time: 60, value: -1, left: left[1], right: right[1] },
    ]);
  });

  it("returns an explicit gap whenever either input is missing", () => {
    const left = [{ time: 0, value: null }, { time: 60, value: 75 }];
    const right = [{ time: 0, value: 70 }, { time: 60, value: null }];
    expect(differencePoints(left, right).map((point) => point.value)).toEqual([null, null]);
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
