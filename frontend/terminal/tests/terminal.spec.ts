import { test, expect, type WebSocketRoute } from "@playwright/test";

test("cached bin interaction stays local; trading, stale feed and responsive layout", async ({
  page,
}, testInfo) => {
  const now = Date.now() / 1000;
  const contracts = Array.from({ length: 6 }, (_, i) => ({
    venue: "kalshi",
    station_id: "KNYC",
    local_day: "2026-09-19",
    yes_token_id: `kalshi:test-${i}`,
    title: `${69 + 2 * i} to ${70 + 2 * i}`,
    settlement_source: "weather_company",
    parse_status: "parsed",
    fee_known: true,
    fee_rate: 0.07,
    minimum_order_size: 0.01,
    tick_size: "0.01",
  }));
  const books = Object.fromEntries(
    contracts.map((c, i) => [
      c.yes_token_id,
      {
        bids: [[0.1 + i * 0.1, 100]],
        asks: [[0.12 + i * 0.1, 100]],
        received_at: now,
        complete: true,
      },
    ]),
  );
  let commands = 0,
    historyRequests = 0;
  await page.route("**/api/session", (r) =>
    r.fulfill({ json: { csrf: "fixture-csrf" } }),
  );
  await page.route("**/api/snapshot", (r) =>
    r.fulfill({
      json: {
        cursor: 1,
        contracts,
        books,
        weather: {},
        health: {
          kalshi: {
            status: "connected",
            transport: "REST",
            interval_seconds: 2,
          },
        },
        accounts: [
          {
            account: "sandbox-kalshi-knyc",
            mode: "sandbox",
            status: "running",
            updated: now,
            snapshot: {
              cash: 100,
              equity: 100,
              available: 100,
              total_pnl: 0,
              orders: [],
              positions: [],
              fills: [],
            },
          },
        ],
        live: { enabled: false, reason: "Live acceptance pending" },
      },
    }),
  );
  await page.route("**/api/history?*", (r) => {
    historyRequests++;
    return r.fulfill({
      json: Array.from({ length: 1500 }, (_, i) => ({
        seq: i + 1,
        time: now - 1500 + i,
        bids: [[0.2, 10]],
        asks: [[0.22, 10]],
      })),
    });
  });
  await page.route("**/api/requests", (r) => r.fulfill({ json: [] }));
  await page.route("**/api/commands", (r) => {
    commands++;
    return r.fulfill({
      status: 202,
      json: { request_id: "fixture-command", status: "queued" },
    });
  });
  let wsMock: WebSocketRoute;
  await page.routeWebSocket("**/api/events*", (ws) => {
    wsMock = ws;
  });
  const errors: string[] = [];
  page.on("pageerror", (e) => errors.push(e.message));
  const started = Date.now();
  await page.goto("/");
  await expect(
    page.getByRole("heading", { name: "KNYC 每日最高温" }),
  ).toBeVisible();
  await expect(page.getByText("终端已连接")).toBeVisible();
  const firstPaint = Date.now() - started;
  const bins = page.locator(".bins button");
  for (let i = 0; i < 6; i++) {
    await bins.nth(i).click();
    await expect(page.getByText("历史加载中，其他操作可继续")).toHaveCount(0);
  }
  const warmed = historyRequests;
  const timings: number[] = [];
  for (let i = 0; i < 24; i++) {
    const index = i % 6;
    const elapsed = await page.evaluate(async (index) => {
      const start = performance.now();
      (
        document.querySelectorAll(".bins button")[index] as HTMLButtonElement
      ).click();
      await new Promise<void>((resolve) =>
        requestAnimationFrame(() => requestAnimationFrame(() => resolve())),
      );
      return performance.now() - start;
    }, index);
    await expect(bins.nth(index)).toHaveAttribute("aria-pressed", "true");
    timings.push(elapsed);
  }
  expect(historyRequests).toBe(warmed);
  const feedback: number[] = [],
    display: number[] = [];
  for (let i = 0; i < 24; i++) {
    feedback.push(
      await page.evaluate(async () => {
        const start = performance.now();
        (
          document.querySelector(".ticket button.primary") as HTMLButtonElement
        ).click();
        await new Promise<void>((resolve) =>
          requestAnimationFrame(() => requestAnimationFrame(() => resolve())),
        );
        if (
          !document
            .querySelector('.ticket [role="status"]')
            ?.textContent?.match(/提交中|已排队/)
        )
          throw new Error("Missing immediate command status");
        return performance.now() - start;
      }),
    );
    await expect(page.getByRole("status")).toContainText("已排队");
    const ask = 0.61 + i * 0.001;
    wsMock!.send(
      JSON.stringify({
        events: [
          {
            seq: 2 + i,
            kind: "book",
            key: "kalshi:test-5",
            data: {
              bids: [[0.6, 10]],
              asks: [[ask, 10]],
              received_at: Date.now() / 1000,
              complete: true,
            },
          },
        ],
      }),
    );
    await expect(
      page.locator(".bins button").nth(5).locator("strong"),
    ).toHaveText(`${(ask * 100).toFixed(1)}¢`);
    display.push(
      await page.evaluate(async () => {
        await new Promise<void>((resolve) =>
          requestAnimationFrame(() => resolve()),
        );
        return performance.getEntriesByName("market-event-display").at(-1)!
          .duration;
      }),
    );
  }
  expect(commands).toBe(24);
  await page.getByRole("button", { name: "实盘", exact: true }).click();
  await expect(
    page.getByRole("button", { name: "买入 YES", exact: true }),
  ).toBeDisabled();
  await page.getByRole("button", { name: "模拟盘", exact: true }).click();
  await page.screenshot({
    path: testInfo.outputPath("terminal-desktop.png"),
    fullPage: true,
  });
  await page.setViewportSize({ width: 390, height: 844 });
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth,
    ),
  ).toBeTruthy();
  await page.screenshot({
    path: testInfo.outputPath("terminal-mobile.png"),
    fullPage: true,
  });
  expect(errors).toEqual([]);
  const percentile = (a: number[]) =>
    a.toSorted((a, b) => a - b)[Math.ceil(a.length * 0.95) - 1];
  const p95 = percentile(timings),
    feedbackP95 = percentile(feedback),
    displayP95 = percentile(display);
  await testInfo.attach("local-performance.json", {
    body: JSON.stringify({
      environment:
        "Local Chromium, synthetic 6-bin fixture, 1500 history points per bin",
      firstPaintMs: firstPaint,
      cachedBinP95Ms: p95,
      commandFeedbackP95Ms: feedbackP95,
      receivedToDisplayP95Ms: displayP95,
      samples: timings,
      feedbackSamples: feedback,
      displaySamples: display,
      venueLatencyMeasured: false,
    }),
    contentType: "application/json",
  });
  expect(p95).toBeLessThanOrEqual(100);
  expect(firstPaint).toBeLessThanOrEqual(3000);
  expect(feedbackP95).toBeLessThanOrEqual(100);
  expect(displayP95).toBeLessThanOrEqual(100);
});
