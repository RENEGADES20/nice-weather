export type Point = { time: number; value: number };

export function mergePoints(current: Point[], incoming: Point[]): Point[] {
  const merged = new Map(current.map((point) => [point.time, point]));
  for (const point of incoming) merged.set(point.time, point);
  return [...merged.values()].sort((left, right) => left.time - right.time);
}

export function appendOnly(current: Point[], incoming: Point[]): boolean {
  if (!current.length) return false;
  const last = current[current.length - 1].time;
  return incoming.every((point) => point.time >= last);
}

export function durationLabel(seconds: number | null): string {
  if (seconds === null || !Number.isFinite(seconds)) return "Unavailable";
  const sign = seconds >= 0 ? "+" : "-";
  const absolute = Math.abs(Math.round(seconds));
  const minutes = Math.floor(absolute / 60);
  const remainder = absolute % 60;
  return `${sign}${minutes}m${remainder.toString().padStart(2, "0")}s`;
}

export function downsample(points: Point[], maxPoints = 10_000): Point[] {
  if (points.length <= maxPoints) return points;
  const bucketSize = Math.ceil(points.length / Math.max(1, Math.floor(maxPoints / 4)));
  const kept = new Map<number, Point>();
  kept.set(points[0].time, points[0]);
  kept.set(points.at(-1)!.time, points.at(-1)!);
  for (let start = 0; start < points.length; start += bucketSize) {
    const bucket = points.slice(start, start + bucketSize);
    const minimum = bucket.reduce((best, point) => point.value < best.value ? point : best);
    const maximum = bucket.reduce((best, point) => point.value > best.value ? point : best);
    for (const point of [bucket[0], minimum, maximum, bucket.at(-1)!]) {
      kept.set(point.time, point);
    }
  }
  return [...kept.values()].sort((left, right) => left.time - right.time).slice(0, maxPoints);
}
