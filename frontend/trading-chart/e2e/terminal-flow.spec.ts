import { test, expect } from "@playwright/test";

test("real component → queue → native Paper: switch, buy, cash edit, close, reset", async ({page}) => {
  page.on("pageerror",e=>console.log("BROWSER_ERROR",String(e)));
  await page.goto("/");
  const frame = page.frameLocator("iframe").first();
  await expect(frame.locator("#t-status")).toContainText("Connected", {timeout: 30_000});
  await expect(frame.locator("#t-review")).toBeEnabled({timeout: 20_000});
  // Do not synthesize component messages: every action traverses Streamlit and SQLite.
  const timings: number[]=[];
  for(const token of ["3","5","7","1","3","1"]){
    const duration=await frame.locator("#t-bins").evaluate((container,token)=>{
      const start=performance.now();
      const button=container.querySelector<HTMLButtonElement>(`[data-token="${token}"]`)!;
      button.click();
      if(!container.querySelector(`[data-token="${token}"]`)?.classList.contains("active"))throw Error("Selection waited for server");
      return performance.now()-start;
    },token);
    timings.push(duration);
  }
  expect(Math.max(...timings)).toBeLessThan(200);
  await expect(frame.locator('[data-token="1"]')).toHaveClass(/active/);
  await expect(frame.locator("#t-book-status")).toContainText("Live depth", {timeout: 20_000});
  await frame.locator("#t-type").selectOption("Market");
  await frame.locator("#t-minimum").click();
  await expect(frame.locator("#t-review")).toBeEnabled();
  await frame.locator("#t-review").click();
  await frame.locator("#t-confirm").dblclick();
  await expect(frame.locator("#t-notices")).toContainText("order · completed", {timeout: 15_000});
  await expect(frame.locator("#t-table tbody tr").first().locator("td").nth(1)).toHaveText("5");
  await frame.locator("#t-balance").click();
  await frame.locator("#t-cash").fill("250");
  await frame.locator("#t-cash-form button[type=submit]").click();
  await frame.locator("#t-confirm").click();
  await expect(frame.locator("#t-funding")).toContainText("Cash $250.00", {timeout: 15_000});
  await frame.locator('[data-token="3"]').click();
  await frame.locator('[data-action="close"]').click();
  await frame.locator("#t-confirm").click();
  await expect(frame.locator("#t-table")).toContainText("No positions", {timeout: 15_000});
  await frame.locator("#t-reset").click();
  await frame.locator("#t-cash").fill("500");
  await frame.locator("#t-cash-form button[type=submit]").click();
  await frame.locator("#t-confirm").click();
  await expect(frame.locator("#t-funding")).toContainText("Cash $500.00 · Net funding $0.00", {timeout: 15_000});
  await expect(frame.locator("#t-metrics")).toContainText("Net PnL$0.00");
  await page.reload();
  await expect(frame.locator("#t-funding")).toContainText("Cash $500.00");
  await expect(frame.locator("#t-history-state")).not.toContainText("Loading", {timeout: 20_000});
  await page.getByText("Live", {exact:true}).click();
  await expect(frame.locator("#t-review")).toBeDisabled();
  await expect(frame.locator("#t-balance")).toBeDisabled();
  console.log(JSON.stringify({selectionMs:timings}));
});
