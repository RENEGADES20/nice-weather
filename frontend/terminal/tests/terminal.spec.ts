import { test, expect, type WebSocketRoute } from "@playwright/test";

test("cached bin interaction stays local; trading, stale feed and responsive layout", async ({
  page,
}, testInfo) => {
  test.setTimeout(60000);
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
    historyRequests = 0,
    failSnapshot = false,
    csrfValue = "fixture-csrf",
    snapshotRequests = 0,
    socketConnections = 0;
  await page.route("**/api/auth", (r) => r.fulfill({ json: { mode: "cloudflare" } }));
  await page.route("**/api/session", (r) =>
    r.fulfill({ json: { csrf: csrfValue } }),
  );
  await page.route("**/api/snapshot", (r) => {
    snapshotRequests++;
    if (failSnapshot) {
      failSnapshot = false;
      return r.fulfill({ status: 502, json: { detail: "Restarting (502)" }, headers: { "Cache-Control": "no-store" } });
    }
    return r.fulfill({
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
            updated: Date.now() / 1000,
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
    });
  });
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
  await page.route("**/api/requests/*", (r) =>
    r.fulfill({
      json: {
        account: "sandbox-kalshi-knyc",
        kind: "order",
        status: "accepted",
      },
    }),
  );
  await page.route("**/api/commands", (r) => {
    expect(r.request().headers()["x-csrf-token"]).toBe(csrfValue);
    commands++;
    return r.fulfill({
      status: 202,
      json: {
        request_id: r.request().postDataJSON().request_id,
        status: "queued",
      },
    });
  });
  let wsMock: WebSocketRoute;
  await page.routeWebSocket("**/api/events*", (ws) => {
    socketConnections++;
    wsMock = ws;
  });
  const errors: string[] = [];
  page.on("console", (m) => {
    if (m.text().includes("error occurred in")) errors.push(m.text());
  });
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
    await expect(page.locator(".ticket button.primary")).toBeEnabled();
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
            ?.textContent?.match(/提交中|已排队|accepted/)
        )
          throw new Error("Missing immediate command status");
        return performance.now() - start;
      }),
    );
    await expect(page.getByRole("status")).toContainText(/已排队|accepted/);
    const ask = 0.61 + i * 0.001;
    wsMock!.send(
      JSON.stringify({
        accounts: [
          {
            account: "sandbox-kalshi-knyc",
            mode: "sandbox",
            status: "running",
            updated: Date.now() / 1000,
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
  // Keep the page open through a server restart; identity survives but CSRF rotates.
  csrfValue = "after-restart-csrf";
  for (const book of Object.values(books)) book.received_at = now - 60;
  failSnapshot = true;
  await wsMock!.close({ code: 1012, reason: "Service restart" });
  await expect(page.getByRole("status")).toContainText("502");
  await expect.poll(async () => ({
    connection: await page.locator(".connection").innerText(), errors,
    snapshotRequests, socketConnections,
  }), { timeout: 10000 }).toEqual({
    connection: expect.stringContaining("终端已连接"), errors: [],
    snapshotRequests: 3, socketConnections: 2,
  });
  await expect(page.getByRole("status")).not.toContainText("502");
  await expect(page.locator(".ticket button.primary")).toBeDisabled();
  wsMock!.send(JSON.stringify({ events: [{
    seq: 26, kind: "book", key: "kalshi:test-5",
    data: { bids: [[0.6, 10]], asks: [[0.65, 10]],
      received_at: Date.now() / 1000, complete: true },
  }] }));
  await expect(page.locator(".ticket button.primary")).toBeEnabled();
  await page.locator(".ticket button.primary").click();
  await expect.poll(() => commands).toBe(25);
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

test("unknown command survives reload and retries only the original payload and ID", async ({
  page,
  context,
}) => {
  const now = Date.now() / 1000;
  await context.route("**/api/auth", (r) => r.fulfill({ json: { mode: "cloudflare" } }));
  await context.route("**/api/session", (r) =>
    r.fulfill({ json: { csrf: "fixture" } }),
  );
  await context.route("**/api/snapshot", (r) =>
    r.fulfill({
      json: {
        cursor: 1,
        contracts: [
          {
            venue: "kalshi",
            station_id: "KNYC",
            local_day: "2026-09-19",
            yes_token_id: "kalshi:test",
            title: "69 to 70",
            parse_status: "parsed",
            fee_known: true,
            fee_rate: 0.07,
            minimum_order_size: 0.01,
            tick_size: "0.01",
          },
        ],
        books: {
          "kalshi:test": {
            bids: [[0.4, 10]],
            asks: [[0.5, 10]],
            complete: true,
            received_at: now,
          },
        },
        weather: {},
        health: {},
        live: { enabled: false, reason: "test" },
        accounts: [
          {
            account: "sandbox-kalshi-knyc",
            status: "running",
            mode: "sandbox",
            updated: now,
            snapshot: {
              cash: 100,
              orders: [],
              positions: [],
              strategy_enabled: true,
            },
          },
        ],
      },
    }),
  );
  await context.route("**/api/history?*", (r) => r.fulfill({ json: [] }));
  await context.route("**/api/requests", (r) => r.fulfill({ json: [] }));
  await context.routeWebSocket("**/api/events*", () => {});
  let visibleReceipt = false;
  const bodies: Record<string, any>[] = [];
  await context.route("**/api/requests/*", (r) =>
    visibleReceipt
      ? r.fulfill({
          json: {
            account: "sandbox-kalshi-knyc",
            kind: "order",
            status: "accepted",
          },
        })
      : r.fulfill({ status: 404, json: { detail: "not recorded" } }),
  );
  await context.route("**/api/commands", async (r) => {
    bodies.push(r.request().postDataJSON());
    if (bodies.length === 1) return r.abort("failed");
    return r.fulfill({
      status: 202,
      json: { request_id: bodies.at(-1)!.request_id, status: "queued" },
    });
  });
  await page.goto("/");
  const buy = page.getByRole("button", { name: "买入 YES", exact: true });
  await expect(buy).toBeEnabled();
  await buy.click();
  await expect(page.getByRole("status")).toContainText("请求未确认");
  await expect(buy).toBeDisabled();
  await expect(
    page.getByRole("button", { name: "停止策略", exact: true }),
  ).toBeEnabled();
  const second = await context.newPage();
  await second.goto("/");
  await expect(
    second.getByRole("button", { name: "买入 YES", exact: true }),
  ).toBeDisabled();
  await expect(second.getByText(/待核验 · kalshi/)).toBeVisible();
  await page.reload();
  await expect(page.getByText(/待核验 · kalshi/)).toBeVisible();
  await expect(buy).toBeDisabled();
  expect(bodies).toHaveLength(1); // Reload and background lookup never submit.
  await page.getByLabel("限价（美元）").fill("0.60");
  await page.getByRole("button", { name: "查询回执 / 原 ID 重试" }).click();
  await expect.poll(() => bodies.length).toBe(2);
  expect(bodies[1]).toEqual(bodies[0]);
  expect(bodies[1].payload.price).toBe(0.5);
  await expect(buy).toBeDisabled(); // A queue acknowledgement is not execution confirmation.
  visibleReceipt = true;
  await page.reload();
  await expect(buy).toBeEnabled();
  expect(bodies).toHaveLength(2);
  await expect(
    second.getByRole("button", { name: "买入 YES", exact: true }),
  ).toBeEnabled();
  await second.close();
  await expect(page.getByText(/待核验 · kalshi/)).toHaveCount(0);
});
