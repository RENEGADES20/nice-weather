import { expect, test, type FrameLocator, type Page } from "@playwright/test";

async function openRepricing(page: Page): Promise<FrameLocator> {
  let lastError: unknown;
  for (let attempt = 0; attempt < 3; attempt += 1) {
    try {
      await page.goto("/");
      await expect(page.getByText("Polymarket NYC / KLGA Trader Dashboard")).toBeVisible({
        timeout: 20_000,
      });
      await page.getByText("Repricing", { exact: true }).click();
      await page.getByRole("combobox").first().click();
      await page.getByRole("option").filter({ hasText: "2026-08-" }).first().click();
      const visibleFrame = page.locator("iframe:visible").first();
      await expect(visibleFrame).toBeVisible({ timeout: 20_000 });
      const frame = visibleFrame.contentFrame();
      await expect(frame.locator("#main-chart canvas").first()).toBeVisible({ timeout: 20_000 });
      await expect(frame.locator("#app")).toHaveAttribute("data-comparison-mode", "as-of", {timeout: 15_000});
      return frame;
    } catch (error) {
      lastError = error;
      await page.waitForTimeout(1_000);
    }
  }
  throw lastError;
}

test("renders 70000 audit points using exact step vertices", async ({page}) => {
  await page.addInitScript(() => window.addEventListener("message", (event) => {
    if (event.data?.type === "streamlit:render" && event.data.args?.payload?.gzip) {
      (window as unknown as {testWire: {gzip: string}}).testWire = event.data.args.payload;
    }
  }));
  const frame = await openRepricing(page);
  const started = Date.now();
  await frame.locator("#app").evaluate(async (root) => {
    const wire = (window as unknown as {testWire: {gzip: string}}).testWire;
    const bytes = Uint8Array.from(atob(wire.gzip), (char) => char.charCodeAt(0));
    const payload = await new Response(new Blob([bytes]).stream()
      .pipeThrough(new DecompressionStream("gzip"))).json();
    payload.signature = "large-history";
    (root as HTMLElement).dataset.expectedRange = `${payload.windowStart}:${payload.windowEnd}`;
    payload.sequence = Number((root as HTMLElement).dataset.sequence) + 1000;
    const price = payload.series.find((item: {id: string}) => item.id === "price");
    price.points = Array.from({length: 70000}, (_, index) => ({
      time: payload.windowStart + index / 10,
      value: Math.floor(index / 7) % 2 ? 0.002 : 0.05,
      receivedAt: new Date((payload.windowStart + index / 10) * 1000).toISOString(),
      binId: payload.selectedBinId,
    }));
    window.postMessage({type: "streamlit:render", args: {payload}, dfs: [], disabled: false}, "*");
  });
  await expect(frame.locator("#app")).toHaveAttribute("data-signature", "large-history");
  await expect(frame.locator("#app")).toHaveAttribute("data-price-point-count", "70000");
  expect(Number(await frame.locator("#app").getAttribute("data-time-basis-points"))).toBeLessThan(12000);
  expect(Date.now() - started).toBeLessThan(5000);
  await frame.locator("#reset-button").click();
  await expect.poll(() => frame.locator("#app").evaluate((root) => (
    root.dataset.mainRange === root.dataset.expectedRange
  ))).toBe(true);
  for (const id of ["#main-chart", "#difference-chart"]) {
    const chart = frame.locator(id);
    await chart.scrollIntoViewIfNeeded();
    const box = (await chart.boundingBox())!;
    await page.mouse.move(box.x + box.width / 2, box.y + 50);
    await page.mouse.wheel(0, -400);
    await expect(frame.locator("#follow-button")).toHaveAttribute("aria-pressed", "false");
    await expect.poll(async () => {
      const ranges = await frame.locator("#app").evaluate((root) => [
        root.dataset.mainRange, root.dataset.differenceRange,
      ]);
      return ranges[0] === ranges[1];
    }).toBe(true);
    await page.waitForTimeout(500);
    await expect(frame.locator("#app")).toHaveAttribute("data-mount-count", "1");
    await frame.locator("#events-button").hover();
    await expect(frame.locator("#app")).toHaveAttribute("data-main-crosshair", "");
    await expect(frame.locator("#app")).toHaveAttribute("data-difference-crosshair", "");
  }
});

async function canvasRatio(frame: FrameLocator): Promise<number> {
  return frame.locator("#main-chart canvas").evaluateAll((canvases) => {
    let bestRatio = 0;
    for (const canvas of canvases as HTMLCanvasElement[]) {
      const context = canvas.getContext("2d");
      if (!context || canvas.width === 0 || canvas.height === 0) continue;
      const pixels = context.getImageData(0, 0, canvas.width, canvas.height).data;
      let nonBackground = 0;
      let sampled = 0;
      for (let index = 0; index < pixels.length; index += 64) {
        sampled += 1;
        const red = pixels[index];
        const green = pixels[index + 1];
        const blue = pixels[index + 2];
        if (!(red > 246 && green > 246 && blue > 246) && !(red < 8 && green < 8 && blue < 8)) {
          nonBackground += 1;
        }
      }
      bestRatio = Math.max(bestRatio, nonBackground / sampled);
    }
    return bestRatio;
  });
}

function rangeValues(value: string | null): [number, number] {
  const [start, end] = (value || "0:0").split(":").map(Number);
  return [start, end];
}

test("keeps the real Streamlit chart stable for ten feed cycles", async ({ page }, testInfo) => {
  const pageErrors: string[] = [];
  page.on("pageerror", (error) => pageErrors.push(error.message));
  const frame = await openRepricing(page);
  const iframe = page.locator("iframe:visible").first();
  await iframe.evaluate((node) => node.setAttribute("data-identity", "stable-iframe"));
  const canvasCount = await frame.locator("#main-chart canvas").count();
  await frame.locator("#main-chart canvas").evaluateAll((nodes) => nodes.forEach(
    (node, index) => node.setAttribute("data-identity", `stable-canvas-${index}`),
  ));
  await frame.locator("#events-button").click();
  await expect(frame.locator("input[name='difference']")).toHaveCount(6);
  await expect(
    frame.locator("input[name='difference'][value='price-minus-forecast']"),
  ).toBeChecked();
  await expect(frame.locator("input[name='difference'][value='weather-gov-minus-metar']"))
    .not.toBeChecked();
  const differenceIds = [
    "metar-minus-forecast",
    "weather-gov-minus-forecast",
    "weather-gov-minus-metar",
    "price-minus-forecast",
    "price-minus-metar",
    "price-minus-weather-gov",
  ];
  for (const id of [
    "weather-gov-minus-forecast",
    "weather-gov-minus-metar",
    "price-minus-weather-gov",
  ]) {
    await frame.locator(`input[name='difference'][value='${id}']`).click();
    await expect(frame.locator(`input[name='difference'][value='${id}']`)).toBeChecked();
  }
  for (const id of differenceIds) {
    await frame.locator(`input[name='difference'][value='${id}']`).click();
    await expect(frame.locator(`input[name='difference'][value='${id}']`)).not.toBeChecked();
  }
  for (const id of [differenceIds[0], differenceIds[3], differenceIds[4]]) {
    await frame.locator(`input[name='difference'][value='${id}']`).click();
  }
  await frame.locator("#reset-button").click();
  await frame.locator("#main-chart").hover();
  await page.mouse.wheel(0, -500);
  await expect(frame.locator("#follow-button")).toHaveAttribute("aria-pressed", "false");
  await expect(frame.locator("#app")).toHaveAttribute("data-feed-paused", "false");
  await expect(frame.locator("#app")).toHaveAttribute("data-main-crosshair", /.+/);
  await expect(frame.locator("#app")).toHaveAttribute("data-difference-crosshair", /.+/);
  await expect.poll(async () => {
    const main = await frame.locator("#app").getAttribute("data-main-crosshair");
    const difference = await frame.locator("#app").getAttribute("data-difference-crosshair");
    return main !== "" && main === difference;
  }).toBe(true);
  await expect.poll(async () => canvasRatio(frame)).toBeGreaterThan(0.001);
  const initialRatio = await canvasRatio(frame);
  expect(initialRatio).toBeGreaterThan(0.001);
  await expect(frame.locator("#app")).toHaveAttribute("data-main-range", /:/);
  await expect(frame.locator("#app")).toHaveAttribute("data-difference-range", /:/);
  await expect.poll(async () => (
    await frame.locator("#app").getAttribute("data-main-range")
    === await frame.locator("#app").getAttribute("data-difference-range")
  )).toBe(true);
  const initialRange = await frame.locator("#app").getAttribute("data-main-range");
  const [initialStart, initialEnd] = rangeValues(initialRange);
  const initialSpan = initialEnd - initialStart;
  const feedTimings: number[] = [];
  const initialPricePoints = Number(
    await frame.locator("#app").getAttribute("data-price-point-count"),
  );

  for (let cycle = 0; cycle < 10; cycle += 1) {
    await page.waitForTimeout(2_100);
    feedTimings.push(Number(await frame.locator("#app").getAttribute("data-query-ms"))
      + Number(await frame.locator("#app").getAttribute("data-render-ms")));
    await expect(iframe).toHaveAttribute("data-identity", "stable-iframe");
    await expect(frame.locator("#app")).toHaveAttribute("data-mount-count", "1");
    await expect(frame.locator("#shell")).toHaveCSS("opacity", "1");
    await expect(frame.locator("#events-button")).toHaveAttribute("aria-pressed", "true");
    await expect(
      frame.locator("input[name='difference'][value='price-minus-forecast']"),
    ).toBeChecked();
    await expect(frame.locator("#app")).toHaveAttribute("data-feed-paused", "false");
    await expect(frame.locator("#app")).toHaveAttribute("data-main-crosshair", /.+/);
    await expect(frame.locator("#app")).toHaveAttribute("data-difference-crosshair", /.+/);
    const ratio = await canvasRatio(frame);
    expect(ratio).toBeGreaterThan(0.001);
    expect(ratio).toBeGreaterThan(initialRatio * 0.25);
    await expect.poll(() => frame.locator("#app").evaluate((root) => (
      root.dataset.mainRange === root.dataset.differenceRange
    ))).toBe(true);
    const currentRange = await frame.locator("#app").getAttribute("data-main-range");
    const [currentStart, currentEnd] = rangeValues(currentRange);
    expect(Math.abs((currentEnd - currentStart) - initialSpan)).toBeLessThan(initialSpan * 0.02);
    expect(Math.abs((currentStart + currentEnd) - (initialStart + initialEnd))).toBeLessThan(
      initialSpan * 0.04,
    );
  }
  expect(Number(await frame.locator("#app").getAttribute("data-price-point-count")))
    .toBeGreaterThan(initialPricePoints);
  await expect(frame.locator("#feed-warning")).toHaveClass(/hidden/);
  console.log("REPRICING_FEED_PERFORMANCE", testInfo.project.name, JSON.stringify(feedTimings));
  expect(pageErrors).toEqual([]);
  await page.screenshot({ path: testInfo.outputPath("dashboard-repricing.png"), fullPage: true });
});

test("keeps one bin state and fits the requested viewport", async ({ page }, testInfo) => {
  const frame = await openRepricing(page);
  await expect(frame.locator("#main-legend")).toContainText("ET");
  await expect(frame.locator("#main-legend")).toContainText("NWS Hourly Forecast");
  await expect(frame.locator(".info").first()).toHaveAttribute("title", /Hourly forecast/);
  await expect(page.locator("[data-tag]").first()).toHaveCSS("background-color", "rgb(234, 242, 255)");
  await expect(page.getByRole("tab", { name: "Repricing" })).toHaveCSS("color", "rgb(37, 99, 235)");
  const dimensions = await page.evaluate(() => ({
    viewport: document.documentElement.clientWidth,
    scroll: document.documentElement.scrollWidth,
  }));
  expect(dimensions.scroll).toBeLessThanOrEqual(dimensions.viewport);
  const componentDimensions = await frame.locator("html").evaluate((element) => ({
    viewport: element.clientWidth,
    scroll: element.scrollWidth,
    height: element.clientHeight,
    scrollHeight: element.scrollHeight,
  }));
  expect(componentDimensions.scroll).toBeLessThanOrEqual(componentDimensions.viewport);
  expect(componentDimensions.scrollHeight).toBeLessThanOrEqual(componentDimensions.height);
  await expect(frame.locator("#difference-chart canvas").first()).toBeVisible();
  await expect(frame.locator("input[name='reference']")).toHaveCount(0);
  const targetRadio = page.getByTestId("stRadioGroup").getByRole("radio", { name: "68-69°F" });
  const targetBin = page.getByTestId("stRadioGroup").getByText("68-69°F", { exact: true });
  if (await targetRadio.isChecked()) {
    await page.getByTestId("stRadioGroup").getByText("70-71°F", { exact: true }).click();
    await expect(targetRadio).not.toBeChecked();
  }
  const firstBin = await frame.locator("#app").getAttribute("data-selected-bin-id");
  await targetBin.click();
  await expect.poll(async () => frame.locator("#app").getAttribute("data-selected-bin-id"))
    .not.toBe(firstBin);
  await expect(frame.locator("#price-readout")).toContainText("unavailable");
  await expect(frame.locator("#app")).toHaveAttribute("data-price-point-count", "0");
  await page.screenshot({ path: testInfo.outputPath("dashboard-layout.png"), fullPage: true });
});


test("future market uses a current price snapshot and one difference", async ({ page }, testInfo) => {
  const frame = await openRepricing(page);
  await page.getByRole("combobox").first().click();
  await page.getByRole("option").filter({ hasText: "Future fixture" }).click();
  await expect(frame.locator("#app")).toHaveAttribute("data-comparison-mode", "future-snapshot");
  await expect(frame.locator("#mode-notice")).toContainText("Current snapshot comparison");
  await expect(frame.locator("input[name='difference']")).toHaveCount(1);
  await expect(frame.locator("#price-readout")).toContainText("20.0%");
  await expect(frame.locator("#main-legend")).not.toContainText("METAR");
  const latencies: number[] = [];
  for (let i = 0; i < 10; i++) {
    const before = await frame.locator("#app").getAttribute("data-price-received-at");
    await expect.poll(() => frame.locator("#app").getAttribute("data-price-received-at"),
      {intervals: [100], timeout: 8_000}).not.toBe(before);
    latencies.push(Number(await frame.locator("#app").getAttribute("data-received-to-visible-ms")));
  }
  console.log("REPRICING_RECEIPT_TO_VISIBLE", testInfo.project.name, JSON.stringify(latencies));

  await expect(frame.locator("#app")).toHaveAttribute("data-mount-count", "1");
});

test("all bins switch without stale messages and feeds recover after disconnect", async ({ page }, testInfo) => {
  const frame = await openRepricing(page);
  const latencies: number[] = [];
  const queryTimes: number[] = [];
  const renderTimes: number[] = [];
  const transportTimes: number[] = [];
  const labels = await page.getByTestId("stRadioGroup").locator("label").allTextContents();
  for (const label of [...labels, ...labels]) {
    const before = await frame.locator("#app").getAttribute("data-selected-bin-id");
    const radio = page.getByTestId("stRadioGroup").getByRole("radio", { name: label.trim(), exact: true });
    if (await radio.isChecked()) continue;
    const started = Date.now();
    await page.getByTestId("stRadioGroup").getByText(label.trim(), { exact: true }).click();
    await expect.poll(() => frame.locator("#app").getAttribute("data-selected-bin-id"),
      { intervals: [50, 100], timeout: 10_000 }).not.toBe(before);
    latencies.push(Date.now() - started);
    queryTimes.push(Number(await frame.locator("#app").getAttribute("data-query-ms")));
    renderTimes.push(Number(await frame.locator("#app").getAttribute("data-render-ms")));
    transportTimes.push(Number(await frame.locator("#app").getAttribute("data-transport-ms")));
  }
  await expect(frame.locator("#app")).toHaveAttribute("data-mount-count", "1");
  const p95 = (values: number[]) => [...values].sort((a, b) => a - b)[Math.ceil(values.length * .95) - 1];
  console.log("REPRICING_PERFORMANCE", testInfo.project.name, JSON.stringify({binSwitchP95Ms: p95(latencies), queryP95Ms: p95(queryTimes), renderP95Ms: p95(renderTimes), transportP95Ms: p95(transportTimes), loadedBinSwitchP95Ms: p95(latencies.slice(labels.length)), samples: latencies}));
  await testInfo.attach("performance.json", {body: JSON.stringify({binSwitchP95Ms: p95(latencies),
    queryP95Ms: p95(queryTimes), renderP95Ms: p95(renderTimes), transportP95Ms: p95(transportTimes), loadedBinSwitchP95Ms: p95(latencies.slice(labels.length)), samples: latencies}), contentType: "application/json"});
  const sequence = Number(await frame.locator("#app").getAttribute("data-sequence"));
  await page.context().setOffline(true);
  await expect(frame.locator("#feed-warning")).toContainText("interrupted", {timeout: 18_000});
  await page.context().setOffline(false);
  await expect.poll(async () => Number(await frame.locator("#app").getAttribute("data-sequence")),
    {timeout: 20_000}).toBeGreaterThan(sequence);
  await expect(frame.locator("#feed-warning")).toHaveClass(/hidden/);
});
