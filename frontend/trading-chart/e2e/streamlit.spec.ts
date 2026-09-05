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
      const visibleFrame = page.locator("iframe:visible").first();
      await expect(visibleFrame).toBeVisible({ timeout: 20_000 });
      const frame = visibleFrame.contentFrame();
      await expect(frame.locator("#main-chart canvas").first()).toBeVisible({ timeout: 20_000 });
      return frame;
    } catch (error) {
      lastError = error;
      await page.waitForTimeout(1_000);
    }
  }
  throw lastError;
}

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
  await expect(frame.locator("#app")).toHaveAttribute("data-feed-paused", "true");
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
  const initialPricePoints = Number(
    await frame.locator("#app").getAttribute("data-price-point-count"),
  );

  for (let cycle = 0; cycle < 10; cycle += 1) {
    await page.waitForTimeout(2_100);
    await expect(iframe).toHaveAttribute("data-identity", "stable-iframe");
    await expect(frame.locator("#main-chart canvas")).toHaveCount(canvasCount);
    expect(await frame.locator("#main-chart canvas").evaluateAll((nodes) => nodes.map(
      (node) => node.getAttribute("data-identity"),
    ))).toEqual(Array.from({ length: canvasCount }, (_, index) => `stable-canvas-${index}`));
    await expect(frame.locator("#shell")).toHaveCSS("opacity", "1");
    await expect(frame.locator("#events-button")).toHaveAttribute("aria-pressed", "true");
    await expect(
      frame.locator("input[name='difference'][value='price-minus-forecast']"),
    ).toBeChecked();
    await expect(frame.locator("#app")).toHaveAttribute("data-feed-paused", "true");
    await expect(frame.locator("#app")).toHaveAttribute("data-main-crosshair", /.+/);
    await expect(frame.locator("#app")).toHaveAttribute("data-difference-crosshair", /.+/);
    const ratio = await canvasRatio(frame);
    expect(ratio).toBeGreaterThan(0.001);
    expect(ratio).toBeGreaterThan(initialRatio * 0.25);
    const currentRange = await frame.locator("#app").getAttribute("data-main-range");
    expect(await frame.locator("#app").getAttribute("data-difference-range")).toBe(currentRange);
    const [currentStart, currentEnd] = rangeValues(currentRange);
    expect(Math.abs((currentEnd - currentStart) - initialSpan)).toBeLessThan(initialSpan * 0.02);
    expect(Math.abs((currentStart + currentEnd) - (initialStart + initialEnd))).toBeLessThan(
      initialSpan * 0.04,
    );
  }
  const pendingRevision = await frame.locator("#app").getAttribute("data-pending-revision");
  expect(pendingRevision).toMatch(/.+/);
  expect(await frame.locator("#app").getAttribute("data-applied-revision")).not.toBe(
    pendingRevision,
  );
  await frame.locator("#main-legend").hover();
  await expect(frame.locator("#app")).toHaveAttribute("data-feed-paused", "false");
  await expect(frame.locator("#app")).toHaveAttribute("data-flushed-revision", /.+/);
  await expect(frame.locator("#app")).toHaveAttribute("data-pending-revision", "");
  expect(Number(await frame.locator("#app").getAttribute("data-price-point-count")))
    .toBeGreaterThan(initialPricePoints);
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
  await expect(frame.locator("#price-readout")).toContainText("Unavailable");
  await expect(frame.locator("#app")).toHaveAttribute("data-price-point-count", "0");
  await page.screenshot({ path: testInfo.outputPath("dashboard-layout.png"), fullPage: true });
});
