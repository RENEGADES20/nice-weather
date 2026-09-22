import { useCallback, useEffect, useRef, useState } from "react";
import { type Catalog, type History, type Quote, type Selection, type WeatherHistory,
  selectionKey } from "./data";

async function get<T>(path: string, signal: AbortSignal, timeout = 10000): Promise<T> {
  const response = await fetch(`/api/${path}`, { credentials: "same-origin",
    signal: AbortSignal.any([signal, AbortSignal.timeout(timeout)]) });
  if (!response.ok) throw new Error(`${path.split("?")[0]} 读取失败 (${response.status})`);
  return response.json();
}
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

type Loaded = { key: string; weather?: WeatherHistory; quotes: Quote[]; loading: boolean;
  error: string; priceReason?: string | null };
export function useMarketWeather(value: Selection, active = false, enabled = true) {
  const key = selectionKey(value);
  const [state, setState] = useState<Loaded>({ key, quotes: [], loading: true, error: "" });
  const cache = useRef(new Map<string, Loaded>());
  const cachedWeather = [...cache.current.values()].find(item =>
    item.weather?.venue === value.venue && item.weather?.day === value.day)?.weather;
  const latest = useRef(key);
  latest.current = key;
  useEffect(() => {
    if (!enabled || !value.day) return;
    const controller = new AbortController();
    const valid = () => !controller.signal.aborted && latest.current === key;
    setState(cache.current.get(key) ?? { key, weather: cachedWeather,
      quotes: [], loading: true, error: "" });
    const params = new URLSearchParams(value);
    const update = (change: Partial<Loaded>) => {
      if (!valid()) return;
      setState(old => {
        if (!valid()) return old;
        const next = { ...(old.key === key ? old : { key, quotes: [], loading: true, error: "" }), ...change };
        cache.current.set(key, next);
        // Cache only recently visited selections; this does not truncate server history.
        if (cache.current.size > 12) cache.current.delete(cache.current.keys().next().value!);
        return next;
      });
    };
    async function read(refreshWeatherOnly = false) {
      if (!value.day) { update({ loading: false }); return; }
      const weatherRead = async () => {
        const weather = await get<WeatherHistory>(`weather-history?${params}`, controller.signal, 60000);
        if (weather.venue !== value.venue || weather.day !== value.day) throw new Error("天气日期不匹配");
        update({ weather });
      };
      const historyRead = async () => {
        if (value.token && !refreshWeatherOnly) {
          let before: number | null = null;
          const points: Quote[] = [];
          do {
            const page: History = await get(`history?${params}${before ? `&before=${before}` : ""}`,
              controller.signal);
            if (selectionKey(page) !== key) throw new Error("行情归属不匹配");
            points.push(...page.points);
            before = page.next_before;
            if (!valid()) return;
          } while (before != null);
          update({ quotes: points.sort((a, b) => a.time - b.time),
            priceReason: points.length ? null : "尚无已采集价格历史" });
        }
      };
      // Weather revisions can involve more history than prices. A slow or failed
      // weather read must not hide the independently available market chart.
      const results = await Promise.allSettled([weatherRead(), historyRead()]);
      update({ loading: false, error: results.filter(r => r.status === "rejected")
        .map(r => String(r.reason)).join("；") });
    }
    async function quote() {
      if (!active || !value.token || !valid()) return;
      try {
        const result = await get<Selection & { quote: Quote | null; reason: string | null }>(
          `market-quote?${params}`, controller.signal);
        if (selectionKey(result) !== key) throw new Error("报价归属不匹配");
        if (result.quote && valid()) setState(old => old.key === key
          ? { ...old, quotes: [...old.quotes, result.quote!] } : old);
      } catch (e) { update({ priceReason: String(e) }); }
    }
    void read().then(quote);
    const timer = setInterval(() => { void quote(); }, 15000);
    const weatherTimer = setInterval(() => { void read(true); }, 60000);
    return () => { controller.abort(); clearInterval(timer); clearInterval(weatherTimer); };
  }, [key, active, enabled]);

  // The integrating terminal may forward its existing WebSocket book events here.
  const acceptBook = useCallback((event: { key: string; received: number; data: Omit<Quote, "time"> }) => {
    if (event.key !== value.token || latest.current !== key) return;
    setState(old => old.key === key ? { ...old,
      quotes: [...old.quotes, { ...event.data, time: event.received,
        received_at: event.data.received_at ?? event.received }] } : old);
  }, [key, value.token]);
  return { ...(state.key === key ? state : { key, weather: cachedWeather,
    quotes: [], loading: true, error: "" }), acceptBook };
}
