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
    { time: start, received_at: start, bids: [[.39, 1]], asks: [[.41, 1]] },
    { time: start + 60, received_at: start + 60, bids: [[.44, 1]], asks: [[.46, 1]] },
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
