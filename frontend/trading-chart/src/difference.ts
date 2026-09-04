export type RawPoint = {
  time: number;
  value: number | null;
  rawValue?: number | null;
  rawUnit?: string;
  priceSource?: string;
  received_at?: string;
};

export type AlignmentKind = "forecast" | "step-fresh" | "step-day" | "price";

export type AlignableSeries = {
  id: string;
  fill: AlignmentKind;
  points: RawPoint[];
  maxAgeSeconds?: number | null;
  validTo?: number | null;
};

export type AlignedPoint = RawPoint & { time: number; value: number };
export type ZAnchor = { mean: number; std: number };

export function mergeRawPoints(existing: RawPoint[], incoming: RawPoint[]): RawPoint[] {
  const byTime = new Map(existing.map((point) => [point.time, point]));
  for (const point of incoming) byTime.set(point.time, point);
  return [...byTime.values()].sort((left, right) => left.time - right.time);
}

export function createBidirectionalSync<T>(
  applyLeft: (value: T) => void,
  applyRight: (value: T) => void,
): { fromLeft: (value: T | null) => void; fromRight: (value: T | null) => void } {
  let syncing = false;
  const relay = (apply: (value: T) => void) => (value: T | null): void => {
    if (syncing || value == null) return;
    syncing = true;
    try {
      apply(value);
    } finally {
      syncing = false;
    }
  };
  return { fromLeft: relay(applyRight), fromRight: relay(applyLeft) };
}

const minute = (value: number): number => Math.floor(value / 60) * 60;

function interpolate(points: RawPoint[], target: number): RawPoint | null {
  const valid = points.filter((point) => point.value !== null).sort((a, b) => a.time - b.time);
  const rightIndex = valid.findIndex((point) => point.time >= target);
  if (rightIndex < 0) return null;
  const right = valid[rightIndex];
  if (right.time === target) return right;
  const left = valid[rightIndex - 1];
  if (!left || left.value === null || right.value === null || right.time === left.time) return null;
  const ratio = (target - left.time) / (right.time - left.time);
  const value = left.value + (right.value - left.value) * ratio;
  return { time: target, value, rawValue: value, rawUnit: left.rawUnit || right.rawUnit };
}

function carry(points: RawPoint[], target: number, maxAge?: number | null): RawPoint | null {
  const candidate = [...points].sort((a, b) => a.time - b.time)
    .filter((point) => point.time <= target).at(-1);
  if (!candidate || candidate.value === null) return null;
  if (maxAge != null && target - candidate.time > maxAge) return null;
  return candidate;
}

export function alignToMinuteGrid(
  series: AlignableSeries,
  start: number,
  end: number,
): AlignedPoint[] {
  const result: AlignedPoint[] = [];
  for (let target = minute(start); target <= minute(end); target += 60) {
    if (series.validTo != null && target >= series.validTo) continue;
    const source = series.fill === "forecast"
      ? interpolate(series.points, target)
      : carry(series.points, target, series.fill === "step-fresh" ? series.maxAgeSeconds : null);
    if (!source || source.value === null) continue;
    result.push({
      ...source,
      time: target,
      value: source.value,
      rawValue: source.rawValue ?? source.value,
    });
  }
  return result;
}

export function zAnchor(points: AlignedPoint[]): ZAnchor | null {
  if (points.length < 2) return null;
  const mean = points.reduce((total, point) => total + point.value, 0) / points.length;
  const variance = points.reduce((total, point) => total + (point.value - mean) ** 2, 0)
    / points.length;
  const std = Math.sqrt(variance);
  return Number.isFinite(std) && std > 0 ? { mean, std } : null;
}

export type DifferencePoint = {
  time: number;
  value: number;
  rawValue: number;
  rawUnit?: string;
  referenceRawValue: number;
  referenceRawUnit?: string;
  zValue: number;
  referenceZValue: number;
};

export function differencePoints(
  other: AlignedPoint[],
  reference: AlignedPoint[],
  otherAnchor: ZAnchor | null,
  referenceAnchor: ZAnchor | null,
  minimumOverlap = 5,
): DifferencePoint[] | null {
  if (!otherAnchor || !referenceAnchor) return null;
  const referenceByTime = new Map(reference.map((point) => [point.time, point]));
  const overlap = other.flatMap((point) => {
    const referencePoint = referenceByTime.get(point.time);
    if (!referencePoint) return [];
    const zValue = (point.value - otherAnchor.mean) / otherAnchor.std;
    const referenceZValue = (referencePoint.value - referenceAnchor.mean) / referenceAnchor.std;
    return [{
      time: point.time,
      value: zValue - referenceZValue,
      rawValue: point.rawValue ?? point.value,
      rawUnit: point.rawUnit,
      referenceRawValue: referencePoint.rawValue ?? referencePoint.value,
      referenceRawUnit: referencePoint.rawUnit,
      zValue,
      referenceZValue,
    }];
  });
  return overlap.length >= minimumOverlap ? overlap : null;
}
