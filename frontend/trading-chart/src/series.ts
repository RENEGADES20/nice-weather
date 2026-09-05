export type Point = { time: number; value: number };

const axisFormatters = new Map<string, Intl.DateTimeFormat>();

export function formatAxisTime(
  epoch: number,
  tickMarkType: number,
  zone?: string,
  locale?: string,
): string {
  const key = JSON.stringify([tickMarkType, zone, locale]);
  let formatter = axisFormatters.get(key);
  if (!formatter) {
    const options: Intl.DateTimeFormatOptions = tickMarkType === 0 ? { year: "numeric" }
      : tickMarkType === 1 ? { month: "short" }
      : tickMarkType === 2 ? { month: "short", day: "2-digit" }
      : { hour: "2-digit", minute: "2-digit", second: tickMarkType === 4 ? "2-digit" : undefined, hour12: false };
    formatter = new Intl.DateTimeFormat(locale, { ...options, timeZone: zone });
    if (axisFormatters.size >= 32) axisFormatters.delete(axisFormatters.keys().next().value!);
    axisFormatters.set(key, formatter);
  }
  return formatter.format(new Date(epoch * 1000));
}

export type DisplayEvent = {
  id: string;
  type: string;
  time: number;
  temperature_f?: number;
  groupCount?: number;
};

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

export function compactDisplayEvents<T extends DisplayEvent>(events: T[]): T[] {
  const sorted = [...events].sort((left, right) => left.time - right.time);
  const deduplicated: T[] = [];
  let previousForecast: number | undefined;
  for (const event of sorted) {
    if (event.type !== "forecast_revised") {
      deduplicated.push(event);
      continue;
    }
    if (event.temperature_f === previousForecast) continue;
    previousForecast = event.temperature_f;
    deduplicated.push(event);
  }

  const result: T[] = [];
  const forecastBuckets = new Map<number, T & { groupCount: number }>();
  for (const event of deduplicated) {
    if (event.type !== "forecast_revised") {
      result.push(event);
      continue;
    }
    const bucket = Math.floor(event.time / 1_800);
    const existing = forecastBuckets.get(bucket);
    forecastBuckets.set(bucket, {
      ...event,
      groupCount: (existing?.groupCount ?? 0) + (event.groupCount ?? 1),
    });
  }
  result.push(...forecastBuckets.values());
  return result.sort((left, right) => left.time - right.time);
}

export function proportionalPosition(value: number, minimum: number, maximum: number): number {
  if (!Number.isFinite(value) || maximum <= minimum) return 50;
  return Math.max(0, Math.min(100, ((value - minimum) / (maximum - minimum)) * 100));
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
