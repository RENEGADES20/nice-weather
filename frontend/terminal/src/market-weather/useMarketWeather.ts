import { useCallback, useEffect, useRef, useState } from "react";
import { type Catalog, type History, type Quote, type Selection, type WeatherHistory,
  selectionKey } from "./data";

async function get<T>(path: string, signal: AbortSignal, timeout = 10000): Promise<T> {
  const response = await fetch(`/api/${path}`, { credentials: "same-origin",
    signal: AbortSignal.any([signal, AbortSignal.timeout(timeout)]) });
  if (!response.ok) throw new Error(`${path.split("?")[0]} 读取失败 (${response.status})`);
  return response.json();
}
const readError = (source: string, error: unknown) =>
  error instanceof DOMException && error.name === "TimeoutError"
    ? `${source}读取超时，请稍后重试。` : `${source}：${String(error)}`;

export function useMarketCatalog(venue: string) {
  const [state, setState] = useState<{ data?: Catalog; loading: boolean; error: string }>({
    loading: true, error: "" });
  useEffect(() => {
    if (!venue) return;
    const controller = new AbortController();
    setState({ loading: true, error: "" });
    const read = async () => {
      try {
        const data = await get<Catalog>(`markets?venue=${encodeURIComponent(venue)}`, controller.signal);
        if (data.venue !== venue) throw new Error("市场目录归属不匹配");
        if (!controller.signal.aborted) setState({ data, loading: false, error: "" });
      } catch (e) {
        if (!controller.signal.aborted) setState(s => ({ ...s, loading: false, error: String(e) }));
      }
    };
    void read();
    const timer = setInterval(read, 300000);
    return () => { controller.abort(); clearInterval(timer); };
  }, [venue]);
  return { ...state, data: state.data?.venue === venue ? state.data : undefined };
}

// A historical page and the WebSocket can contain the same capture. Merge instead
// of replacing the array, so a slow older page cannot erase a newer live quote.
export function mergeQuotes(previous: Quote[], incoming: Quote[]): Quote[] {
  const points = new Map(previous.map(q => [q.time, q]));
  for (const q of incoming) {
    const old = points.get(q.time);
    if (!old || (q.seq ?? 0) >= (old.seq ?? 0)) points.set(q.time, q);
  }
  return [...points.values()].sort((a, b) => a.time - b.time);
}

type Market = { key: string; quotes: Quote[]; loading: boolean; historyLoading: boolean;
  complete: boolean; error: string; priceReason?: string | null };
type Weather = { key: string; data?: WeatherHistory; loading: boolean; error: string; loadedAt: number };
const emptyMarket = (key: string): Market => ({ key, quotes: [], loading: true,
  historyLoading: true, complete: false, error: "" });

export function useMarketWeather(value: Selection, active = false, enabled = true) {
  const key = selectionKey(value), weatherKey = `${value.venue}/${value.day}`;
  const latest = useRef(key), latestWeather = useRef(weatherKey);
  latest.current = key; latestWeather.current = weatherKey;
  const cache = useRef(new Map<string, Market>());
  const weatherCache = useRef(new Map<string, Weather>());
  const [market, setMarket] = useState<Market>(() => emptyMarket(key));
  const [weather, setWeather] = useState<Weather>({ key: weatherKey, loading: true,
    error: "", loadedAt: 0 });

  useEffect(() => {
    if (!enabled || !value.day) return;
    const controller = new AbortController();
    const valid = () => !controller.signal.aborted && latestWeather.current === weatherKey;
    const cached = weatherCache.current.get(weatherKey);
    setWeather(cached ?? { key: weatherKey, loading: true, error: "", loadedAt: 0 });
    const params = new URLSearchParams({ venue: value.venue, day: value.day });
    let timer: ReturnType<typeof setTimeout>;
    async function read() {
      try {
        const data = await get<WeatherHistory>(`weather-history?${params}`, controller.signal, 60000);
        if (data.venue !== value.venue || data.day !== value.day) throw new Error("天气日期不匹配");
        if (!valid()) return;
        const next = { key: weatherKey, data, loading: false, error: "", loadedAt: Date.now() };
        weatherCache.current.set(weatherKey, next);
        if (weatherCache.current.size > 12) weatherCache.current.delete(weatherCache.current.keys().next().value!);
        setWeather(next);
      } catch (e) {
        if (valid()) setWeather(old => ({ ...old, loading: false, error: readError("天气数据", e) }));
      } finally {
        if (valid()) timer = setTimeout(read, 60000);
      }
    }
    // A bin change never restarts this request. Returning to a recently visited day
    // uses its data immediately and keeps the remaining refresh interval.
    const remaining = cached ? Math.max(0, 60000 - (Date.now() - cached.loadedAt)) : 0;
    if (remaining) timer = setTimeout(read, remaining); else void read();
    return () => { controller.abort(); clearTimeout(timer); };
  }, [weatherKey, enabled]);

  const update = useCallback((change: Partial<Market>, points: Quote[] = []) => {
    if (latest.current !== key) return;
    setMarket(old => {
      if (latest.current !== key) return old;
      const current = old.key === key ? old : cache.current.get(key) ?? emptyMarket(key);
      const next = { ...current, ...change,
        quotes: points.length ? mergeQuotes(current.quotes, points) : current.quotes };
      cache.current.set(key, next);
      if (cache.current.size > 12) cache.current.delete(cache.current.keys().next().value!);
      return next;
    });
  }, [key]);

  useEffect(() => {
    if (!enabled || !value.day) return;
    const controller = new AbortController();
    const valid = () => !controller.signal.aborted && latest.current === key;
    const cached = cache.current.get(key);
    setMarket(cached ?? emptyMarket(key));
    const params = new URLSearchParams(value);
    async function history() {
      if (!value.token) {
        update({ loading: false, historyLoading: false }); return;
      }
      if (cached?.complete && !active) return;
      update({ historyLoading: true, error: "" });
      let before: number | null = null;
      const known = new Set(cached?.quotes.map(q => q.seq ?? q.time));
      try {
        do {
          const page: History = await get(`history?${params}${before ? `&before=${before}` : ""}`,
            controller.signal);
          if (selectionKey(page) !== key) throw new Error("行情归属不匹配");
          if (!valid()) return;
          if (page.next_before != null && before != null && page.next_before >= before)
            throw new Error("行情分页游标未前进");
          const overlaps = cached?.complete && page.points.some(q => known.has(q.seq ?? q.time));
          before = overlaps ? null : page.next_before;
          update({ loading: false, historyLoading: before != null, complete: before == null,
            priceReason: null }, page.points);
        } while (before != null && valid());
      } catch (e) {
        if (valid()) update({ loading: false, historyLoading: false, error: readError("行情历史", e) });
      }
    }
    void history();
    return () => controller.abort();
  }, [key, enabled, update]);

  useEffect(() => {
    if (!enabled || !active || !value.day || !value.token) return;
    const controller = new AbortController();
    const valid = () => !controller.signal.aborted && latest.current === key;
    const params = new URLSearchParams(value);
    let quoteTimer: ReturnType<typeof setTimeout>;
    async function quote() {
      if (!active || !value.token || !valid()) return;
      try {
        const result = await get<Selection & { quote: Quote | null; reason: string | null }>(
          `market-quote?${params}`, controller.signal);
        if (selectionKey(result) !== key) throw new Error("报价归属不匹配");
        if (valid()) update({ priceReason: result.reason }, result.quote ? [result.quote] : []);
      } catch (e) {
        if (valid()) update({ priceReason: String(e) });
      } finally {
        if (valid()) quoteTimer = setTimeout(quote, 15000);
      }
    }
    void quote();
    return () => { controller.abort(); clearTimeout(quoteTimer); };
  }, [key, active, enabled, update]);

  const acceptBook = useCallback((event: { key: string; received: number;
    data: Omit<Quote, "time"> & { time?: number } }) => {
    if (event.key !== value.token || latest.current !== key) return;
    const time = Number.isFinite(event.data.time) ? event.data.time! : Math.max(event.received,
      event.data.received_at ?? event.received, event.data.probability_received_at ?? 0);
    update({}, [{ ...event.data, time,
      received_at: event.data.received_at ?? event.received }]);
  }, [key, value.token, update]);
  const selected = market.key === key ? market : cache.current.get(key) ?? emptyMarket(key);
  const selectedWeather = weather.key === weatherKey ? weather : weatherCache.current.get(weatherKey);
  return { ...selected, weather: selectedWeather?.data, weatherLoading: selectedWeather?.loading ?? true,
    historyError: selected.error, weatherError: selectedWeather?.error ?? "",
    error: selectedWeather?.error ?? "", // Compatibility for the standalone weather preview.
    priceReason: selected.priceReason || (!selected.loading && !selected.quotes.length ? "尚无已采集价格历史" : null),
    acceptBook };
}
