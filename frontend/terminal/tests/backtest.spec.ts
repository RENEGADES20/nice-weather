import { test, expect, type Page } from "@playwright/test";
import type { RunDetail } from "../src/backtest/types";

// Explicit development data; never used as evidence of historical profitability.
const start = Date.parse("2026-09-20T05:00:00Z") / 1000;
function result(id = "development-a", venue: "kalshi" | "poly_us" = "kalshi"): RunDetail {
  return { run_id: id, status: "completed", updated: start, config: {
    venue, start, end: start + 172800, start_day: "2026-09-20", end_day: "2026-09-21",
    strategy_id: "S1_S2_S3", cash: 100, simulation: { estimated_fee_rate: .01, slippage_pp: 0 },
  }, snapshot: { cash: 98, market_value: 2.2, equity: 100.2, total_pnl: .2, fees: .02 },
  settlement: "pending", history_complete: true,
  curve: [{ time: start, cash: 100, market_value: 0, equity: 100, total_pnl: 0 },
    { time: start + 90000, cash: 98, market_value: null, equity: null, total_pnl: null },
    { time: start + 93600, cash: 98, market_value: 2.2, equity: 100.2, total_pnl: .2 }],
  events: ["a", "b"].map((token, i) => ({ id: `leg-${token}`, group_id: "basket-1", venue,
    day: "2026-09-21", token: `${venue}:${token}`, strategy: "S1", stage: "fill",
    time: start + 93600, price: .4 + i * .1, quantity: 1, probability: .95, fee: .01, cost: .41 + i * .1 })),
  };
}
async function setup(page: Page) {
  const runs = [result(), result("development-b", "poly_us")];
  const errors: string[] = [];
  page.on("pageerror", e => errors.push(e.message));
  await page.route("**/api/session", r => r.fulfill({ json: { csrf: "development" } }));
  await page.route("**/api/backtests", r => r.fulfill({ json: runs }));
  await page.route("**/api/backtests/*", r => r.fulfill({ json: runs.find(x => r.request().url().endsWith(x.run_id)) ?? runs[0] }));
  await page.route("**/api/market-day?*", async r => {
    const q = new URL(r.request().url()).searchParams;
    await r.fulfill({ json: { venue: q.get("venue"), day: q.get("day"), contracts: ["a", "b"].map(x => ({
      yes_token_id: `${q.get("venue")}:${x}`, title: `开发样例档 ${x}`,
    })), observation_start: `${q.get("day")}T05:00:00Z`, observation_end: "2026-09-22T05:00:00Z" } });
  });
  await page.route("**/api/history?*", async r => {
    const q = new URL(r.request().url()).searchParams;
    await r.fulfill({ json: { venue: q.get("venue"), day: q.get("day"), token: q.get("token"),
      points: [0, 60, 93600, 94000].map((delta, i) => ({ seq: i, time: start + delta,
        bids: [[.3, 10]], asks: [[.5, 10]] })), next_before: null } });
  });
  return { runs, errors };
}

test("development examples: curves, S1 legs, locating trades and historical run replacement", async ({ page }) => {
  const { errors } = await setup(page);
  await page.goto("/backtest-preview.html");
  await expect(page.locator(".bt-result")).toHaveAttribute("data-run-id", "development-a");
  await expect(page.locator(".bt-metrics")).toContainText("100.20");
  await page.getByRole("combobox", { name: "曲线", exact: true }).selectOption("total_pnl");
  await expect(page.getByRole("img", { name: "PnL曲线" })).toBeVisible();
  await page.getByRole("button", { name: "定位 leg-b", exact: true }).click();
  await expect(page.getByRole("combobox", { name: "图表市场日" })).toHaveValue("2026-09-21");
  await expect(page.getByRole("button", { name: "开发样例档 b" })).toHaveAttribute("aria-pressed", "true");
  await expect(page.getByRole("img", { name: "市场赔率图" })).toHaveAttribute("data-focus", "leg-b");
  await expect(page.locator(".bt-focus")).toContainText("95.00%");
  const box = await page.getByRole("img", { name: "市场赔率图" }).boundingBox();
  await page.mouse.move(box!.x + box!.width / 2, box!.y + 140);
  await page.mouse.wheel(0, -200);
  await page.mouse.down(); await page.mouse.move(box!.x + box!.width / 2 + 90, box!.y + 140); await page.mouse.up();
  await page.getByLabel("历史运行").selectOption("development-b");
  await expect(page.locator(".bt-result")).toHaveAttribute("data-run-id", "development-b");
  await expect(page.locator(".bt-result")).toHaveCount(1);
  await expect(page.locator(".bt-table")).not.toContainText("kalshi:");
  expect(errors).toEqual([]);
});

test("rapid selections cannot restore an old result; empty, failed and missing data remain explicit", async ({ page }) => {
  const { runs, errors } = await setup(page);
  runs[1] = { ...runs[1], status: "failed", error: "开发样例：历史输入不足", events: [], curve: [], history_complete: false };
  await page.route("**/api/backtests/development-a", async r => {
    await new Promise(resolve => setTimeout(resolve, 500));
    await r.fulfill({ json: runs[0] }).catch(() => {});
  });
  await page.goto("/backtest-preview.html");
  await expect(page.getByLabel("历史运行")).toHaveValue("development-a");
  await page.getByLabel("历史运行").selectOption("development-b");
  await expect(page.locator(".bt-result")).toHaveAttribute("data-run-id", "development-b");
  await expect(page.getByRole("alert")).toContainText("历史输入不足");
  await expect(page.getByText(/此运行未保存完整账户曲线/)).toBeVisible();
  await expect(page.getByText(/本次无成交/)).toBeVisible();
  await page.waitForTimeout(600); // Deliberately cross the delayed response, not a timing benchmark.
  await expect(page.locator(".bt-result")).toHaveAttribute("data-run-id", "development-b");
  expect(errors).toEqual([]);
});

test("all execution controls reach the command; new run replaces the result", async ({ page }) => {
  const { runs, errors } = await setup(page);
  let submitted: any;
  await page.route("**/api/commands", async r => {
    submitted = r.request().postDataJSON();
    runs.unshift({ ...result(submitted.request_id, submitted.venue), status: "queued", events: [], curve: [] });
    await r.fulfill({ status: 202, json: { request_id: submitted.request_id, status: "queued" } });
  });
  await page.goto("/backtest-preview.html");
  await page.getByRole("combobox", { name: "平台", exact: true }).selectOption("poly_us");
  await page.getByLabel("开始市场日").fill("2026-09-20");
  await page.getByLabel("结束市场日").fill("2026-09-21");
  await page.getByRole("combobox", { name: "策略", exact: true }).selectOption("S2");
  await page.getByLabel("初始资金（$）").fill("250");
  await page.getByLabel("未知费用估算（%）").fill("2");
  await page.getByLabel("滑点（百分点）").fill("0.5");
  await page.getByRole("button", { name: "运行回测", exact: true }).click();
  await expect.poll(() => submitted?.payload).toEqual({ start_day: "2026-09-20", end_day: "2026-09-21", strategy: "S2", cash: 250,
    simulation: { estimated_fee_rate: .02, slippage_pp: .5 } });
  await expect(page.locator(".bt-result")).toHaveAttribute("data-run-id", submitted.request_id);
  expect(errors).toEqual([]);
});

test("late empty quotes from the old bin cannot erase the current price chart", async ({ page }) => {
  const { errors } = await setup(page);
  let requestedA = false;
  await page.route("**/api/history?*", async r => {
    const q = new URL(r.request().url()).searchParams;
    const old = q.get("token") === "kalshi:a";
    if (old) { requestedA = true; await new Promise(resolve => setTimeout(resolve, 500)); }
    await r.fulfill({ json: { venue: q.get("venue"), day: q.get("day"), token: q.get("token"),
      points: old ? [] : [{ seq: 1, time: start + 60, bids: [[.4, 10]], asks: [[.6, 10]] }],
      next_before: null } }).catch(() => {});
  });
  await page.goto("/backtest-preview.html");
  await expect.poll(() => requestedA).toBe(true);
  await page.getByRole("button", { name: "开发样例档 b" }).click();
  await expect(page.getByRole("button", { name: "开发样例档 b" })).toHaveAttribute("aria-pressed", "true");
  await page.waitForTimeout(600);
  await expect(page.getByText("该档在本次回测区间没有有效赔率")).toHaveCount(0);
  expect(errors).toEqual([]);
});
