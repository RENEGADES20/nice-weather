import { expect, test } from "@playwright/test";

test("paper ticket ignores a previous platform preview and shows a concrete refusal", async ({ page }) => {
  // Component sample only; native ledger acceptance is tests/trading/test_paper_approximation.py.
  const contracts = ["kalshi", "poly_us"].map(venue => ({
    venue, local_day: "2026-09-22", yes_token_id: `${venue}-sample`,
    no_token_id: `${venue}-sample:NO`, label: "开发样例", tick_size: ".01",
    quantity_step: ".01", minimum_order_size: .01,
  }));
  await page.route("**/api/login", route => route.fulfill({ json: { csrf: "test" } }));
  await page.route("**/api/snapshot", route => route.fulfill({ json: { contracts,
    accounts: contracts.map(c => ({ account: `sandbox-${c.venue}-knyc`, snapshot: {
      account_revision: c.venue, cash: 100, equity: 100, fees: 0, total_pnl: 0,
      simulation: { estimated_fee_rate: .01, slippage_pp: 0 },
    } })),
  } }));
  let release: (() => void) | undefined;
  await page.route("**/api/paper/preview", async route => {
    if (route.request().postDataJSON().venue === "kalshi") {
      await new Promise<void>(resolve => { release = resolve; });
      await route.fulfill({ json: { available: true, selected_price: { price: .4,
        price_source: "ask", age_seconds: 100 }, estimated_fee: .01 } });
    } else await route.fulfill({ json: { available: false, reason: "NO_MARKET_PRICE" } });
  });
  await page.goto("/tests/paper-ticket.html");
  await page.getByLabel("限价", { exact: true }).fill(".5");
  await expect.poll(() => Boolean(release)).toBe(true);
  await page.getByLabel("平台", { exact: true }).selectOption("poly_us");
  await expect(page.getByLabel("限价", { exact: true })).toHaveValue("");
  release!();
  await page.getByLabel("限价", { exact: true }).fill(".5");
  await expect(page.getByRole("status")).toContainText("NO_MARKET_PRICE");
  await expect(page.getByRole("button", { name: "模拟买入", exact: true })).toBeDisabled();
  await page.unroute("**/api/paper/preview");
  await page.route("**/api/paper/preview", route => route.fulfill({ json: { available: true } }));
  const requests: string[] = [];
  await page.route("**/api/commands", async route => {
    requests.push(route.request().postDataJSON().request_id);
    if (requests.length === 1) await route.abort("failed");
    else await route.fulfill({ json: { status: "queued" } });
  });
  await page.route("**/api/requests/*", route => route.fulfill({ json: { status: "accepted" } }));
  const buy = page.getByRole("button", { name: "模拟买入", exact: true });
  await expect(buy).toBeEnabled();
  await buy.click();
  await expect(page.getByRole("button", { name: "查询或重试原请求" })).toBeVisible();
  await expect(buy).toBeDisabled();
  await page.getByRole("button", { name: "查询或重试原请求" }).click();
  await expect(page.getByRole("status")).toContainText("已处理 order");
  expect(requests).toHaveLength(2);
  expect(requests[0]).toBe(requests[1]);
});
