import { test, expect } from "@playwright/test";

const now = Math.floor(Date.now()/1000), day = new Date().toLocaleDateString("en-CA",{timeZone:"America/New_York"});
const markets = ["YES","NO"].map((outcome,i)=>({token:String(i+1),date:day,bin:"80–81°F",outcome,condition:"test",tick_size:.01,minimum_order_size:1,fee_rate:.02,fee_exponent:1}));
const payload = {mode:"terminal",account:"sandbox-fixture",accountMode:"Paper",connected:true,selectedToken:"1",markets,
  depth:{"1":{valid:true,received_ns:now*1e9,bids:[["0.39","5"]],asks:[["0.40","2"],["0.41","3"]]}},
  snapshot:{cash:100,available:100,equity:100,total_pnl:0,positions:[],orders:[],fills:[]},
  performance:{points:[{time:now-3600,value:0},{time:now,value:1.5}],days:[{day,pnl:1.5,fills:2,fees:.01,status:"In progress"}]},history:[],notices:[],updated:now};

test("terminal retains input and folds independently; depth click cannot submit",async({page},testInfo)=>{
  await page.goto("/");
  await page.getByRole("tab",{name:"Trading",exact:true}).click();
  const iframe=page.locator("iframe:visible").first();
  await expect(iframe).toBeVisible();
  const src=await iframe.getAttribute("src");
  await page.goto(new URL(src!,page.url()).href);
  await page.evaluate(p=>window.postMessage({type:"streamlit:render",args:{payload:p},disabled:false},"*"),payload);
  await expect(page.locator("#t-review")).toBeEnabled();
  await page.screenshot({path:`test-results/terminal-${testInfo.project.name}.png`,fullPage:true});
  await page.locator("#t-size").fill("2.5");
  await page.locator("#t-asks button").last().click();
  await expect(page.locator("#t-limit")).toHaveValue("41");
  await expect(page.locator("#t-dialog")).not.toBeVisible();
  await page.reload();
  await page.evaluate(p=>window.postMessage({type:"streamlit:render",args:{payload:p},disabled:false},"*"),payload);
  await expect(page.locator("#t-size")).toHaveValue("2.5");
  await expect(page.locator("#t-limit")).toHaveValue("41");
  await page.locator("#t-pnl summary").click();
  await expect(page.locator("#t-pnl")).not.toHaveAttribute("open");
  await expect(page.locator("#t-calendar")).toHaveAttribute("open","");
  await page.evaluate(p=>window.postMessage({type:"streamlit:render",args:{payload:p},disabled:false},"*"),payload);
  await expect(page.locator("#t-size")).toHaveValue("2.5");
  await expect(page.locator("#t-pnl")).not.toHaveAttribute("open");
  await page.locator("#t-review").click();
  await expect(page.locator("#t-dialog")).toBeVisible();
  await expect(page.locator("#t-review-summary")).toContainText("80–81°F");
  await page.locator("#t-dismiss").click();
  await page.evaluate(()=>{(window as any).selections=[];window.addEventListener("message",e=>{if(e.data?.type==="streamlit:setComponentValue")(window as any).selections.push(e.data.value);});});
  await page.locator('#t-bins [data-token="2"]').click();
  await expect.poll(()=>page.evaluate(()=>(window as any).selections.at(-1)?.token)).toBe("2");
  await page.evaluate(p=>window.postMessage({type:"streamlit:render",args:{payload:{...p,selectedToken:"2"}},disabled:false},"*"),payload);
  await page.locator("#t-size").fill("4.25");
  await page.reload();
  await page.evaluate(()=>{(window as any).selections=[];window.addEventListener("message",e=>{if(e.data?.type==="streamlit:setComponentValue")(window as any).selections.push(e.data.value);});});
  await page.evaluate(p=>window.postMessage({type:"streamlit:render",args:{payload:p},disabled:false},"*"),payload);
  await expect.poll(()=>page.evaluate(()=>(window as any).selections.at(-1)?.token)).toBe("2");
  await page.evaluate(p=>window.postMessage({type:"streamlit:render",args:{payload:{...p,selectedToken:"2"}},disabled:false},"*"),payload);
  await expect(page.locator("#t-size")).toHaveValue("4.25");
  await page.locator(`[data-day="${day}"]`).click();
  await expect(page.locator("#t-day-detail")).toContainText("2 fills");
  expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBeTruthy();
  await page.evaluate(p=>window.postMessage({type:"streamlit:render",args:{payload:{...p,accountMode:"Live",snapshot:{}}},disabled:false},"*"),payload);
  await expect(page.locator("#t-review")).toBeDisabled();
  await expect(page.locator("#t-status")).toContainText("Execution disabled");
});
