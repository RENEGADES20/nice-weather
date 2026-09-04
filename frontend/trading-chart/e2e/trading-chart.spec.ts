import { expect, test } from "@playwright/test";

const base = 1_788_000_000;
const payload = {
  signature: "e2e",
  objectTimezone: "America/New_York",
  displayTimezone: "America/Chicago",
  threshold: "0.9",
  focusBinId: "bin-80",
  series: [
    {
      id: "running-tmax",
      name: "Running Tmax",
      group: "Weather",
      axis: "left",
      pane: "weather",
      format: "temperature",
      role: "primary",
      color: "#1f7a68",
      defaultVisible: true,
      points: [
        { time: base, value: 78 },
        { time: base + 300, value: 79 },
        { time: base + 600, value: 80 },
      ],
    },
    {
      id: "bin-80:mid",
      name: "80-81 F Mid",
      group: "Market",
      axis: "right",
      pane: "market",
      format: "probability",
      role: "primary",
      color: "#356ae6",
      defaultVisible: true,
      points: [
        { time: base, value: 0.2 },
        { time: base + 420, value: 0.45 },
        { time: base + 720, value: 0.92 },
      ],
    },
    {
      id: "bin-80:best_ask",
      name: "80-81 F Ask",
      group: "Market",
      axis: "right",
      pane: "market",
      format: "probability",
      role: "ask",
      color: "#356ae6",
      lineStyle: "dashed",
      defaultVisible: false,
      points: [
        { time: base, value: 0.22 },
        { time: base + 420, value: 0.48 },
      ],
    },
  ],
  events: [
    {
      id: "bin-entered",
      type: "bin_entered",
      title: "80-81 F entered",
      shortLabel: "80-81 F entered",
      displayPriority: 1,
      groupCount: 1,
      time: base + 300,
      object_time: new Date((base + 300) * 1000).toISOString(),
      received_at: new Date((base + 335) * 1000).toISOString(),
      first_market_move_at: new Date((base + 420) * 1000).toISOString(),
      source_latency_seconds: 35,
      tradable_lead_seconds: 85,
      threshold_times: { "0.9": new Date((base + 720) * 1000).toISOString() },
      bin_id: "bin-80",
      temperature_f: 79,
    },
  ],
};

async function render(page: Parameters<typeof test>[0]["page"], data = payload): Promise<void> {
  await page.goto("/");
  await page.evaluate((data) => {
    window.postMessage(
      {
        type: "streamlit:render",
        args: { payload: data },
        dfs: [],
        disabled: false,
        theme: {
          primaryColor: "#ff4b4b",
          backgroundColor: "#ffffff",
          secondaryBackgroundColor: "#f6f8fb",
          textColor: "#172033",
          font: "sans serif",
        },
      },
      "*",
    );
  }, data);
  await expect(page.locator("#main-chart canvas").first()).toBeVisible();
}

test("renders both charts and interactive layer controls", async ({ page }) => {
  await render(page);
  await expect(page.locator("#response-chart canvas").first()).toBeVisible();
  await expect(page.locator("#latency")).toContainText("System lead");
  await page.locator("#layers-button").click();
  await expect(page.locator("#layers")).toBeVisible();
  await expect(page.locator("[data-layer='running-tmax']")).toBeChecked();
  await expect(page.locator("[data-layer='bin-80:mid']")).toBeChecked();
  await expect(page.locator("[data-layer='bin-80:best_ask']")).not.toBeChecked();
  await expect(page.locator("[data-response='running-tmax']")).not.toBeChecked();
  await expect(page.locator("[data-response='bin-80:best_ask']")).not.toBeChecked();
  const nonBlank = await page.locator("#main-chart canvas").first().evaluate((canvas) => {
    const context = (canvas as HTMLCanvasElement).getContext("2d");
    if (!context) return false;
    const pixels = context.getImageData(0, 0, canvas.width, canvas.height).data;
    return pixels.some((value, index) => index % 4 !== 3 && value !== 0);
  });
  expect(nonBlank).toBe(true);
});

test("keeps a light stable shell through repeated Streamlit renders", async ({ page }) => {
  await render(page);
  await expect(page.locator("html")).toHaveCSS("background-color", "rgb(255, 255, 255)");
  await page.locator("#shell").evaluate((element) => { element.setAttribute("data-stable", "yes"); });
  await page.locator("#main-chart canvas").first().evaluate((element) => { element.setAttribute("data-canvas", "same"); });
  await page.locator("#layers-button").click();
  await page.locator("[data-response='running-tmax']").check();
  for (let index = 0; index < 5; index += 1) {
    await page.evaluate((data) => {
      window.postMessage({
        type: "streamlit:render",
        args: { payload: data },
        dfs: [],
        disabled: false,
        theme: {
          primaryColor: "#ff4b4b",
          backgroundColor: "#ffffff",
          secondaryBackgroundColor: "#f6f8fb",
          textColor: "#172033",
          font: "sans serif",
        },
      }, "*");
    }, { ...payload, series: payload.series.map((series) => ({ ...series, points: [...series.points] })) });
  }
  await expect(page.locator("#shell")).toHaveAttribute("data-stable", "yes");
  await expect(page.locator("#main-chart canvas").first()).toHaveAttribute("data-canvas", "same");
  await expect(page.locator("#shell")).toHaveCSS("background-color", "rgb(255, 255, 255)");
  await expect(page.locator("[data-response='running-tmax']")).toBeChecked();
});

test("fits the mobile viewport without horizontal overflow", async ({ page }) => {
  await render(page);
  const layout = await page.locator("#shell").evaluate((element) => ({
    width: element.getBoundingClientRect().width,
    viewport: document.documentElement.clientWidth,
    scrollWidth: document.documentElement.scrollWidth,
  }));
  expect(layout.width).toBeLessThanOrEqual(layout.viewport);
  expect(layout.scrollWidth).toBeLessThanOrEqual(layout.viewport);
});

test("keeps crowded revisions quiet and captures the light layout", async ({ page }, testInfo) => {
  const revisions = Array.from({ length: 50 }, (_, index) => ({
    ...payload.events[0],
    id: `revision-${index}`,
    type: "forecast_revised",
    title: `Forecast revised to ${80 + (index % 4)} F`,
    shortLabel: `Forecast ${80 + (index % 4)} F`,
    time: base + index * 600,
    object_time: new Date((base + index * 600) * 1000).toISOString(),
    received_at: new Date((base + index * 600 + 20) * 1000).toISOString(),
    temperature_f: 80 + (index % 4),
  }));
  const mids = Array.from({ length: 4 }, (_, index) => ({
    ...payload.series[1],
    id: `bin-${80 + index}:mid`,
    name: `${80 + index}-${81 + index} F Mid`,
    role: index === 0 ? "primary" : "context",
    color: ["#356ae6", "#1f7a68", "#8a6bb8", "#b7791f"][index],
  }));
  const data = {
    ...payload,
    signature: "crowded",
    series: [payload.series[0], ...mids],
    events: [payload.events[0], ...revisions],
  };
  await render(page, data);
  await expect(page.locator("#main-chart")).not.toContainText("Forecast revised");
  await page.screenshot({ path: testInfo.outputPath("dashboard-light.png"), fullPage: true });
  await page.locator("#layers-button").click();
  await expect(page.locator("[data-layer$=':mid']:checked")).toHaveCount(4);
});
