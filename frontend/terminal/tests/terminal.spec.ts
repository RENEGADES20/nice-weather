import { test, expect, type Page } from "@playwright/test";

async function setup(page:Page) {
  const now=Date.now()/1000;
  await page.route("**/api/auth",r=>r.fulfill({json:{mode:"cloudflare"}}));
  await page.route("**/api/session",r=>r.fulfill({json:{csrf:"fixture"}}));
  await page.route("**/api/requests",r=>r.fulfill({json:[]}));
  await page.routeWebSocket("**/api/events*",()=>{});
  await page.route("**/api/snapshot",r=>r.fulfill({json:{cursor:1,contracts:[],books:{},weather:{},health:{},accounts:[
    ...["kalshi","poly_us"].map(venue=>({account:`sandbox-${venue}-knyc`,run_id:`sandbox-${venue}-knyc`,mode:"sandbox",status:"running",updated:now,snapshot:{cash:100,equity:100,available:100,orders:[],positions:[],fills:[],strategy_enabled:true}})),
    ...["kalshi","poly_us"].map(venue=>({account:`live-${venue}-knyc`,mode:"live",status:"running",updated:now,snapshot:{binding:venue,authenticated:true,reconciled:true,received_at:now,
      config:{revision:0,binding:null,manual_enabled:false,strategies:{S1:false,S2:false,S3:false},whitelist:[],order_limit:"5",position_limit:"5",daily_loss_limit:"5"},funds:{cash:venue==="kalshi"?"12.34":"23.45"},budget:{limit:"5",spent:"0",reserved:"0",remaining:"5"},availability:{order:{allowed:false}},market_rules:{},orders:[],fills:[],positions:[]}}))]}}));
  await page.route("**/api/markets?*",r=>{const venue=new URL(r.request().url()).searchParams.get("venue");return r.fulfill({json:{venue,days:["2026-09-19","2026-09-22","2026-09-23"].map(day=>({day,has_prices:true,contracts:[0,1].map(i=>({venue,local_day:day,yes_token_id:`${venue}:${day}:${i}`,no_token_id:`${venue}:${day}:${i}:NO`,condition_id:`${day}:${i}`,title:`开发样例 ${i}`,lower:70+i*2,upper:71+i*2,active:false,tick_size:"0.01",minimum_order_size:1}))}))}});});
  await page.route("**/api/weather-history?*",r=>{const q=new URL(r.request().url()).searchParams;const start=Date.parse(q.get("day")+"T05:00:00Z")/1000;return r.fulfill({json:{venue:q.get("venue"),day:q.get("day"),start,end:start+86400,as_of:start+3600,sources:{},missing_sources:[],cli:[]}});});
  await page.route("**/api/history?*",r=>{const q=new URL(r.request().url()).searchParams;return r.fulfill({json:{venue:q.get("venue"),day:q.get("day"),token:q.get("token"),points:[{seq:1,time:now-60,received_at:now-60,bids:[[.4,1]],asks:[[.5,1]]}],next_before:null}});});
  await page.route("**/api/account-events?*",r=>r.fulfill({json:{next:0,more:false,events:[]}}));
  await page.route("**/api/paper/equity?*",r=>r.fulfill({json:{points:[],next:null}}));
  await page.route("**/api/paper/preview",r=>r.fulfill({json:{available:true,reason:"可用",estimated_fee:.01,selected_price:{price:.5,price_source:"ask",age_seconds:1,received_at:now}}}));
}

test("cross-day selection, Live account isolation and refresh",async({page})=>{
  await setup(page);const errors:string[]=[];page.on("pageerror",e=>errors.push(e.message));
  await page.goto("/?venue=kalshi&day=2026-09-19");
  await expect(page.getByLabel("温度档位")).toHaveValue("kalshi:2026-09-19:0");
  await page.getByLabel("温度档位").selectOption("kalshi:2026-09-19:1");
  await page.getByLabel("市场日",{exact:true}).fill("2026-09-23");await expect(page).toHaveURL(/day=2026-09-23/);
  await page.getByLabel("市场日",{exact:true}).fill("2026-09-19");await expect(page.getByLabel("温度档位")).toHaveValue("kalshi:2026-09-19:1");
  await page.reload();await expect(page.getByLabel("温度档位")).toHaveValue("kalshi:2026-09-19:1");
  await page.getByLabel("市场日",{exact:true}).fill("2020-01-01");await expect(page.getByText("所选日期未采集合约，不切换日期。")).toBeVisible();
  await page.getByRole("button",{name:"实盘",exact:true}).click();await expect(page.getByRole("region",{name:"实盘账户"})).toContainText("$12.34");
  await expect(page.getByRole("button",{name:"提交 Live 订单"})).toBeDisabled();
  await page.getByLabel("平台",{exact:true}).selectOption("poly_us");await expect(page.getByRole("region",{name:"实盘账户"})).toContainText("$23.45");
  await page.setViewportSize({width:390,height:844});expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBeTruthy();expect(errors).toEqual([]);
});

test("account heartbeats do not invalidate Paper preview; reconnect keeps selection",async({page})=>{
  await setup(page);
  let connection = 0;
  await page.routeWebSocket("**/api/events*",socket=>{
    connection++;
    const timer=setInterval(()=>socket.send(JSON.stringify({accounts:[{
      account:"sandbox-poly_us-knyc",run_id:"sandbox-poly_us-knyc",mode:"sandbox",
      updated:Date.now()/1000,snapshot:{cash:100,available:100,equity:100,positions:[],orders:[]},
    }]})),100);
    socket.onClose(()=>clearInterval(timer));
    if(connection===1)setTimeout(()=>{clearInterval(timer);socket.close();},700);
  });
  await page.route("**/api/paper/preview",async r=>{
    await new Promise(resolve=>setTimeout(resolve,350));
    await r.fulfill({json:{available:true,reason:"可用"}});
  });
  await page.goto("/?venue=poly_us&day=2026-09-19");
  await expect(page.getByLabel("温度档位")).toHaveValue("poly_us:2026-09-19:0");
  await page.getByLabel("限价",{exact:true}).fill("0.5");
  await expect(page.getByRole("button",{name:"模拟买入",exact:true})).toBeEnabled();
  await expect.poll(()=>connection).toBe(2);
  await expect(page.getByLabel("市场日",{exact:true})).toHaveValue("2026-09-19");
  await expect(page.getByRole("button",{name:"模拟买入",exact:true})).toBeEnabled();
});

test("unknown Paper request retains original ID across reload and retry",async({page})=>{
  await setup(page);let visible=false;const bodies:any[]=[];
  await page.route("**/api/requests/*",r=>visible?r.fulfill({json:{account:"sandbox-kalshi-knyc",kind:"order",status:"accepted"}}):r.fulfill({status:404,json:{detail:"not found"}}));
  await page.route("**/api/commands",r=>{bodies.push(r.request().postDataJSON());return r.abort();});
  await page.goto("/?venue=kalshi&day=2026-09-19");await page.getByLabel("限价",{exact:true}).fill("0.5");await page.getByRole("button",{name:"模拟买入",exact:true}).click();
  await expect(page.getByText(/待核验 · kalshi/)).toBeVisible();await page.reload();await expect(page.getByText(/待核验 · kalshi/)).toBeVisible();expect(bodies).toHaveLength(1);
  await page.getByRole("button",{name:"查询回执 / 原 ID 重试"}).click();await expect.poll(()=>bodies.length).toBe(2);expect(bodies[1]).toEqual(bodies[0]);
  visible=true;await expect(page.getByText(/待核验 · kalshi/)).toHaveCount(0);await page.reload();expect(bodies).toHaveLength(2);
});

test("backtest entrypoint replaces the selected result",async({page})=>{
  await setup(page);const run=(id:string)=>({run_id:id,status:"completed",updated:1,config:{venue:"kalshi",start:Date.parse("2026-09-19T05:00:00Z")/1000,end:Date.parse("2026-09-20T05:00:00Z")/1000,strategy:"S3",cash:100},snapshot:{equity:id==="first"?101:102},curve:[],events:[],history_complete:false});
  await page.route("**/api/backtests",r=>r.fulfill({json:[run("first"),run("second")]}));
  await page.route("**/api/backtests/*",r=>r.fulfill({json:run(r.request().url().split("/").at(-1)!)}));
  await page.route("**/api/market-day?*",r=>r.fulfill({json:{venue:"kalshi",day:"2026-09-19",contracts:[]}}));
  await page.goto("/");await page.getByRole("button",{name:"回测",exact:true}).click();await expect(page.locator(".bt-result")).toHaveAttribute("data-run-id","first");
  await page.getByLabel("历史运行",{exact:true}).selectOption("second");await expect(page.locator(".bt-result")).toHaveCount(1);await expect(page.locator(".bt-result")).toHaveAttribute("data-run-id","second");
});
