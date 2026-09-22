// Local component host only; main.tsx and production navigation remain integration-owned.
import React, { useState } from "react";
import { createRoot } from "react-dom/client";
import { MarketSelector } from "./MarketSelector";
import { WeatherAnalysis } from "./WeatherAnalysis";
import { useMarketCatalog, useMarketWeather } from "./useMarketWeather";
import { type Selection } from "./data";

function Preview() {
  const [value, setValue] = useState<Selection>(() => {
    const params = new URLSearchParams(location.search);
    return { venue: params.get("venue") ?? "kalshi", day: params.get("day") ?? "",
      token: params.get("token") ?? "" };
  });
  const catalog = useMarketCatalog(value.venue);
  const contract = catalog.data?.days.find(d => d.day === value.day)?.contracts
    .find(c => c.yes_token_id === value.token);
  const weather = useMarketWeather(value, !!contract?.active);
  const change = (next: Selection) => {
    setValue(next);
    history.replaceState(null, "", `?${new URLSearchParams(next)}`);
  };
  return <main style={{ maxWidth: 1300, margin: "20px auto", fontFamily: "system-ui" }}>
    <h1>市场与天气组件验收</h1>
    <p>独立本地宿主 · 无交易操作</p>
    <MarketSelector catalog={catalog.data} value={value} onChange={change}
      loading={catalog.loading} error={catalog.error} />
    <p>市场：{value.venue} / {value.day} / {value.token || "无 bin"}</p>
    {contract && <p>交易结束：{contract.close_time ?? "未知"} · 与观察窗口分别显示</p>}
    <WeatherAnalysis data={weather.weather} quotes={weather.quotes} token={value.token}
      loading={weather.loading} error={weather.error} priceReason={weather.priceReason} />
  </main>;
}
createRoot(document.getElementById("root")!).render(<Preview />);
