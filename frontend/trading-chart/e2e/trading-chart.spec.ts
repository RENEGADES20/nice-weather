import { expect, test } from "@playwright/test";

const base = 1_788_000_000;
const payload = {
  signature: "e2e",
  objectTimezone: "America/New_York",
  displayTimezone: "America/Chicago",
  threshold: "0.9",
  series: [
    {
      id: "running-tmax",
      name: "Running Tmax",
      group: "Weather",
      axis: "left",
      color: "#f2f5f8",
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
      color: "#2dd4bf",
      defaultVisible: true,
      points: [
        { time: base, value: 0.2 },
        { time: base + 420, value: 0.45 },
        { time: base + 720, value: 0.92 },
      ],
    },
  ],
  events: [
    {
      id: "bin-entered",
      type: "bin_entered",
      title: "80-81 F entered",
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

async function render(page: Parameters<typeof test>[0]["page"]): Promise<void> {
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
          backgroundColor: "#0b0e11",
          secondaryBackgroundColor: "#151a20",
          textColor: "#d8dee9",
          font: "sans serif",
        },
      },
      "*",
    );
  }, payload);
  await expect(page.locator("#main-chart canvas").first()).toBeVisible();
}

test("renders both charts and interactive layer controls", async ({ page }) => {
  await render(page);
  await expect(page.locator("#response-chart canvas").first()).toBeVisible();
  await expect(page.locator("#latency")).toContainText("Tradable lead");
  await page.locator("#layers-button").click();
  await expect(page.locator("#layers")).toBeVisible();
  await expect(page.locator("[data-layer='running-tmax']")).toBeChecked();
  const nonBlank = await page.locator("#main-chart canvas").first().evaluate((canvas) => {
    const context = (canvas as HTMLCanvasElement).getContext("2d");
    if (!context) return false;
    const pixels = context.getImageData(0, 0, canvas.width, canvas.height).data;
    return pixels.some((value, index) => index % 4 !== 3 && value !== 0);
  });
  expect(nonBlank).toBe(true);
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
