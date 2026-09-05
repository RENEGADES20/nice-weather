export type RawPoint = {
  time: number;
  value: number | null;
  rawValue?: number | null;
  rawUnit?: string;
  displayValue?: number | null;
  source?: string;
  priceSource?: string;
  received_at?: string;
  receivedAt?: string;
  object_time?: string;
  objectTime?: string;
  issuedAt?: string;
  validFrom?: string;
  validTo?: string;
  captureId?: string;
  ageSeconds?: number;
  binId?: string;
  quality?: string;
  reason?: string;
};

export type PointSeries = {
  id: string;
  name: string;
  removedTimes?: number[];
  points: RawPoint[];
  binId?: string;
};

export function mergeRawPoints(existing: RawPoint[], incoming: RawPoint[]): RawPoint[] {
  const byTime = new Map(existing.map((point) => [point.time, point]));
  for (const point of incoming) byTime.set(point.time, point);
  return [...byTime.values()].sort((left, right) => left.time - right.time);
}

export function nonNullSegments(points: RawPoint[]): RawPoint[][] {
  const segments: RawPoint[][] = [];
  let current: RawPoint[] = [];
  for (const point of [...points].sort((left, right) => left.time - right.time)) {
    if (point.value === null) {
      if (current.length) segments.push(current);
      current = [];
      continue;
    }
    current.push(point);
  }
  if (current.length) segments.push(current);
  return segments;
}

// WithSteps needs each value change and the segment endpoints. Audit points stay untouched.
export function stepVertices(points: RawPoint[]): RawPoint[] {
  return points.filter((point, index) => index === 0 || index === points.length - 1
    || point.value !== points[index - 1].value);
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

export type DifferencePoint = {
  time: number;
  value: number | null;
  left: RawPoint | null;
  right: RawPoint | null;
};

export function differencePoints(
  left: RawPoint[],
  right: RawPoint[],
): DifferencePoint[] {
  const leftByTime = new Map(left.map((point) => [point.time, point]));
  const rightByTime = new Map(right.map((point) => [point.time, point]));
  const times = [...new Set([...leftByTime.keys(), ...rightByTime.keys()])]
    .sort((a, b) => a - b);
  return times.map((time) => {
    const leftPoint = leftByTime.get(time) || null;
    const rightPoint = rightByTime.get(time) || null;
    const leftValue = leftPoint?.value;
    const rightValue = rightPoint?.value;
    const value = leftValue == null || rightValue == null
      ? null
      : leftValue - rightValue;
    return {
      time,
      value: value != null && Number.isFinite(value) ? value : null,
      left: leftPoint,
      right: rightPoint,
    };
  });
}
