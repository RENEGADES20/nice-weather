import { test, expect } from "@playwright/test";
import { analysis, weatherMinutes, type WeatherHistory } from "../src/market-weather/data";

function weather(day = "2026-09-19", venue = "kalshi"): WeatherHistory {
  const start = Date.parse(`${day}T05:00:00Z`) / 1000;
  return { venue, day, start, end: start + 86400, as_of: start + 600, window_source: "contract",
    sources: { metar: [{ time: start, value: 68, received_at: start, source: "metar",
      version: "m1", issued_at: null }], hourly_temp: [], nws_observations: [], nws_forecast: [],
      hrrr: [
        { time: start, value: 70, received_at: start - 100, source: "hrrr", version: "v1", issued_at: start - 3600 },
        { time: start, value: 72, received_at: start + 60, source: "hrrr", version: "v2", issued_at: start - 1800 },
        { time: start + 3600, value: 90, received_at: start + 60, source: "hrrr", version: "v2", issued_at: start - 1800 },
      ] }, cli: [], missing_sources: ["hourly_temp", "nws_forecast"] };
}

test("minute changes compare the same valid time, preserve gaps and percentage points", () => {
  const data = weather(), start = data.start;
  data.as_of = start + 900;
  const lines = analysis(data, [
    { time: start, received_at: start, probability: .4, probability_source: "kalshi_last_trade", bids: [[.39, 1]], asks: [[.41, 1]] },
    { time: start + 60, received_at: start + 60, probability: .45, probability_source: "kalshi_last_trade", bids: [[.44, 1]], asks: [[.46, 1]] },
  ], "bin", "hrrr", 3);
  expect(lines[0].points[1].value).toBeCloseTo(5);
  expect(lines[1].points[1].value).toBe(2);
  expect(lines[0].points[12].value).toBeNull();
  expect(weatherMinutes({ ...data, sources: { ...data.sources, metar: [
    { ...data.sources.metar[0], received_at: null },
  ] } }, "metar").every(p => p.value === null)).toBe(true);
});

test("standalone components switch days, bins, sources and reject delayed responses", async ({ page }) => {
  const errors: string[] = [];
  page.on("pageerror", error => errors.push(error.message));
  await page.route("**/api/markets?*", async route => {
    const venue = new URL(route.request().url()).searchParams.get("venue")!;
    await route.fulfill({ json: { venue, days: ["2026-09-19", "2026-09-22", "2026-09-23"].map(day => ({
      day, has_prices: true, status: "open", contracts: [0, 1].map(i => ({ venue, local_day: day,
        yes_token_id: `${venue}:${day}:${i}`, lower: i ? 70 : null, title: i ? "70–71°F" : "69°F 以下",
        active: false, close_time: day + "T23:00:00Z" })) })) } });
  });
  await page.route("**/api/weather-history?*", async route => {
    const params = new URL(route.request().url()).searchParams;
    const day = params.get("day")!, venue = params.get("venue")!;
    if (day === "2026-09-22") await new Promise(resolve => setTimeout(resolve, 350));
    await route.fulfill({ json: weather(day, venue) });
  });
  await page.route("**/api/history?*", async route => {
    const params = new URL(route.request().url()).searchParams;
    await route.fulfill({ json: { ...Object.fromEntries(params), points: [], next_before: null, reason: "NO_PRICE_HISTORY" } });
  });
  await page.goto("/market-weather.html?venue=kalshi&day=2026-09-19");
  await expect(page.getByRole("heading", { level: 2 })).toContainText("2026-09-19");
  await page.getByLabel("温度档位", { exact: true }).selectOption("kalshi:2026-09-19:1");
  await page.getByLabel("已挂牌日期").selectOption("2026-09-22");
  await page.getByLabel("已挂牌日期").selectOption("2026-09-23");
  await expect(page.getByRole("heading", { level: 2 })).toContainText("2026-09-23");
  await page.waitForTimeout(450);
  await expect(page.getByRole("heading", { level: 2 })).toContainText("2026-09-23");
  await page.getByLabel("已挂牌日期").selectOption("2026-09-19");
  await expect(page.getByLabel("温度档位", { exact: true })).toHaveValue("kalshi:2026-09-19:1");
  await page.getByLabel("预报来源").selectOption("nws_forecast");
  await page.getByLabel("分钟分析").selectOption("0");
  await expect(page.getByText("所选分析暂无可比较数据。")).toBeVisible();
  await page.getByLabel("市场日", { exact: true }).fill("2026-09-21");
  await expect(page.getByText("所选日期未采集合约，不切换日期。")).toBeVisible();
  await expect(page.getByLabel("市场日", { exact: true })).toHaveValue("2026-09-21");
  await page.getByLabel("平台", { exact: true }).selectOption("poly_us");
  await page.getByLabel("已挂牌日期").selectOption("2026-09-19");
  await expect(page.getByRole("heading", { level: 2 })).toContainText("poly_us");
  await page.reload();
  await expect(page.getByLabel("平台", { exact: true })).toHaveValue("poly_us");
  await expect(page.getByLabel("市场日", { exact: true })).toHaveValue("2026-09-19");
  const chart = page.getByLabel("天气与价格交互图");
  await expect(chart.locator("canvas").first()).toBeVisible();
  await chart.hover();
  await page.mouse.wheel(0, -180);
  const box = (await chart.boundingBox())!;
  await page.mouse.move(box.x + 300, box.y + 100);
  await page.mouse.down();
  await page.mouse.move(box.x + 450, box.y + 100, { steps: 5 });
  await page.mouse.up();
  await page.getByRole("button", { name: "重置市场日视野" }).click();
  expect(errors).toEqual([]);
});


test("history merge keeps newer probability facts and deduplicates captures", async () => {
  const { mergeQuotes } = await import("../src/market-weather/useMarketWeather");
  const base = { received_at: 10, bids: [], asks: [], probability_source: "native_last_trade" };
  const live = { ...base, time: 30, probability: .7, probability_received_at: 30 };
  const history = { ...base, seq: 2, time: 20, probability: .4, probability_received_at: 20 };
  expect(mergeQuotes([history, live], [history, { ...base, seq: 1, time: 10, probability: .2 }]))
    .toEqual([{ ...base, seq: 1, time: 10, probability: .2 }, history, live]);
});

test("bin switches share an in-flight weather read and pages preserve live probability", async ({ page }) => {
  const day = "2026-09-19", venue = "kalshi", start = weather().start;
  const token = (i: number) => `${venue}:${day}:${i}`;
  let releaseWeather!: () => void, releaseHistory!: () => void, releaseQuote!: () => void;
  const weatherGate = new Promise<void>(resolve => { releaseWeather = resolve; });
  const historyGate = new Promise<void>(resolve => { releaseHistory = resolve; });
  const quoteGate = new Promise<void>(resolve => { releaseQuote = resolve; });
  let weatherReads = 0, oldPages = 0, firstPages = 0, quotes = 0;
  const point = (value: number, time: number, seq?: number) => ({ time, received_at: time,
    bids: [], asks: [], probability: value, probability_source: "native_last_trade",
    probability_time: time, probability_received_at: time, seq });
  await page.route("**/api/markets?*", route => route.fulfill({ json: { venue, days: [{
    day, has_prices: true, contracts: [0, 1].map(i => ({ venue, local_day: day,
      yes_token_id: token(i), lower: i ? 70 : null, title: i ? "70–71°F" : "69°F 以下",
      active: true, close_time: day + "T23:00:00Z" })) }] } }));
  await page.route("**/api/weather-history?*", async route => {
    weatherReads++;
    expect(new URL(route.request().url()).searchParams.has("token")).toBe(false);
    await weatherGate;
    await route.fulfill({ json: weather(day, venue) }).catch(() => {});
  });
  await page.route("**/api/history?*", async route => {
    const params = new URL(route.request().url()).searchParams;
    const selection = { venue, day, token: params.get("token") };
    if (selection.token === token(1)) {
      await route.fulfill({ json: { ...selection, points: [point(.6, start + 90, 30)], next_before: null } });
    } else if (params.has("before")) {
      oldPages++;
      await historyGate;
      await route.fulfill({ json: { ...selection, points: [point(.2, start, 10)], next_before: null } });
    } else {
      firstPages++;
      if (firstPages > 1) await weatherGate; // Returning to a bin must show its cached values now.
      await route.fulfill({ json: { ...selection, points: [point(.3, start + 60, 20)], next_before: 20 } });
    }
  });
  await page.route("**/api/market-quote?*", async route => {
    const params = new URL(route.request().url()).searchParams;
    const selected = params.get("token");
    quotes++;
    if (quotes > 1) await weatherGate; else await quoteGate;
    await route.fulfill({ json: { venue, day, token: selected,
      quote: point(selected === token(0) ? .7 : .6, start + 120), reason: null } }).catch(() => {});
  });
  await page.goto(`/market-weather.html?venue=${venue}&day=${day}&token=${token(0)}`);
  await expect(page.getByText(/平台市场概率：30.00%/)).toBeVisible();
  await expect.poll(() => oldPages).toBe(1);
  expect(weatherReads).toBe(1);
  releaseQuote();
  await expect(page.getByText(/平台市场概率：70.00%/)).toBeVisible();
  const older = page.waitForResponse(response => response.url().includes("before=20"));
  releaseHistory(); await older;
  await page.evaluate(() => new Promise<void>(resolve => requestAnimationFrame(() => resolve())));
  await expect(page.getByText(/平台市场概率：70.00%/)).toBeVisible();
  await page.getByLabel("温度档位", { exact: true }).selectOption(token(1));
  await expect(page.getByText(/平台市场概率：60.00%/)).toBeVisible();
  await page.getByLabel("温度档位", { exact: true }).selectOption(token(0));
  await expect(page.getByText(/平台市场概率：70.00%/)).toBeVisible();
  expect(weatherReads).toBe(1);
  releaseWeather();
  await expect(page.getByRole("heading", { level: 2 })).toContainText(day);
  expect(weatherReads).toBe(1);
});
