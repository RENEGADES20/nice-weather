import { test, expect, type Page } from "@playwright/test";
import { displaySamples, sampleAt } from "../src/ProbabilityChart";
import { marketProbability, priceMinutes } from "../src/market-weather/data";

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

test("position mark updates do not invalidate Paper sell preview; reconnect keeps selection",async({page})=>{
  await setup(page);
  let connection = 0, mark = 0;
  await page.routeWebSocket("**/api/events*",socket=>{
    connection++;
    const timer=setInterval(()=>socket.send(JSON.stringify({accounts:[{
      account:"sandbox-poly_us-knyc",run_id:"sandbox-poly_us-knyc",mode:"sandbox",
      updated:Date.now()/1000,snapshot:{cash:99.99,available:99.99,equity:100+mark/100,
        positions:[{token:"poly_us:2026-09-19:0",quantity:1,unrealized_pnl:mark++/100}],orders:[]},
    }]})),100);
    socket.onClose(()=>clearInterval(timer));
    if(connection===1)setTimeout(()=>{clearInterval(timer);socket.close();},700);
  });
  await page.route("**/api/paper/preview",async r=>{
    await new Promise(resolve=>setTimeout(resolve,2500));
    await r.fulfill({json:{available:true,reason:"可用"}});
  });
  await page.goto("/?venue=poly_us&day=2026-09-19");
  await expect(page.getByLabel("温度档位")).toHaveValue("poly_us:2026-09-19:0");
  await page.getByLabel("方向").selectOption("SELL");
  await page.getByLabel("限价",{exact:true}).fill("0.5");
  await expect(page.getByRole("button",{name:"模拟卖出",exact:true})).toBeEnabled({timeout:15000});
  await expect.poll(()=>connection).toBe(2);
  await expect(page.getByLabel("市场日",{exact:true})).toHaveValue("2026-09-19");
  await expect(page.getByRole("button",{name:"模拟卖出",exact:true})).toBeEnabled();
});

test("a focused fill cannot crash an empty future market",async({page})=>{
  await setup(page); const errors:string[]=[]; page.on("pageerror",e=>errors.push(e.message));
  await page.route("**/api/account-events?*",r=>r.fulfill({json:{next:1,more:false,events:[{
    id:"realistic-fill",time:Date.now()/1000,stage:"fill",strategy:"manual",price:.5,quantity:1,
    venue:"poly_us",day:"2026-09-22",token:"poly_us:2026-09-22:0",
  }]}}));
  await page.route("**/api/history?*",r=>r.fulfill({json:{points:[],next_before:null}}));
  await page.route("**/api/market-quote?*",r=>r.fulfill({json:{quote:null,reason:"NO_MARKET_PRICE"}}));
  await page.goto("/?venue=poly_us&day=2026-09-22");
  await page.getByText("所选市场日信号与交易记录",{exact:true}).click();
  await page.locator('[data-event-id="realistic-fill"]').click();
  await expect(page.getByRole("img",{name:"市场赔率与策略标记"})).toHaveAttribute("data-focus","realistic-fill");
  await page.getByLabel("已挂牌日期").selectOption("2026-09-23");
  await expect(page.getByLabel("市场日",{exact:true})).toHaveValue("2026-09-23");
  await expect(page.getByRole("img",{name:"市场赔率与策略标记"})).toHaveAttribute("data-focus","");
  expect(errors).toEqual([]);
});

test("nearby fills without market probability remain individually clickable",async({page})=>{
  await setup(page);const now=Date.now()/1000;let fillCount=4;
  await page.route("**/api/history?*",r=>r.fulfill({json:{points:[],next_before:null}}));
  await page.route("**/api/account-events?*",r=>r.fulfill({json:{next:fillCount,more:false,
    events:Array.from({length:fillCount},(_,i)=>({id:`missing-probability-fill-${i}`,time:now-60+i/10,
      stage:"fill",strategy:"manual",price:.5,quantity:1,venue:"poly_us",day:"2026-09-22",
      token:"poly_us:2026-09-22:0"}))}}));
  await page.goto("/?venue=poly_us&day=2026-09-22");
  const markers=page.locator(".probability-marker.is-fill");
  const graph=page.getByRole("img",{name:"市场赔率与策略标记"});
  for(const {width,height,count} of [{width:1440,height:1100,count:4},{width:390,height:844,count:4},
    {width:1440,height:1100,count:6},{width:390,height:844,count:6}]) {
    if(fillCount!==count) { fillCount=count;await page.reload(); }
    await page.setViewportSize({width,height});
    await expect(markers).toHaveCount(count);
    for(let i=0;i<count;i++) {
      await markers.nth(i).click({timeout:3000});
      await expect(graph).toHaveAttribute("data-focus",`missing-probability-fill-${i}`);
      await expect(page.locator(".probability-event")).toContainText("同期市场概率：—");
      await expect(markers.nth(i)).toHaveAttribute("title",/同期市场概率缺失/);
    }
    const layout=await page.evaluate(()=>({
      boxes:[...document.querySelectorAll(".probability-marker.is-fill")].map(node=>{
        const {top,bottom}=node.getBoundingClientRect();return {top,bottom};
      }),
      chartBottom:document.querySelector(".probability-plot svg")!.getBoundingClientRect().bottom,
      controlsTop:document.querySelector(".probability-controls")!.getBoundingClientRect().top,
    }));
    expect(layout.boxes[0].top).toBeGreaterThan(layout.chartBottom);
    for(let i=1;i<layout.boxes.length;i++)expect(layout.boxes[i].top).toBeGreaterThan(layout.boxes[i-1].bottom);
    expect(layout.controlsTop).toBeGreaterThan(layout.boxes.at(-1)!.bottom);
  }
});

test("adjacent dense groups without probability open their own original events",async({page})=>{
  await setup(page);const now=Math.floor(Date.now()/1000), counts=[51,73,107,129];
  let release!:(events:any[])=>void;
  const pending=new Promise<any[]>(resolve=>{release=resolve;});
  await page.route("**/api/history?*",r=>r.fulfill({json:{
    ...Object.fromEntries(new URL(r.request().url()).searchParams),points:[{seq:1,time:now-7200,
      received_at:now-7200,probability:null,bids:[],asks:[]}],next_before:null}}));
  await page.route("**/api/account-events?*",async r=>r.fulfill({json:{next:360,more:false,events:await pending}}));
  try {
    await page.goto("/?venue=poly_us&day=2026-09-22");
    await expect(page.getByLabel("温度档位",{exact:true})).toHaveValue("poly_us:2026-09-22:0");
    await page.getByRole("button",{name:"All",exact:true}).click();
    const graph=page.getByRole("img",{name:"市场赔率与策略标记"});
    await expect(graph).toHaveAttribute("data-start",String(now-7200));
    const layout=await graph.evaluate(node=>({start:Number(node.getAttribute("data-start")),
      end:Number(node.getAttribute("data-end")),width:Number(node.getAttribute("viewBox")!.split(" ")[2])}));
    const columns=Math.floor((layout.width-56)/24), first=Math.floor(columns/3);
    release(counts.flatMap((count,g)=>Array.from({length:count},(_,i)=>({id:`dense-group-${g}-event-${i}`,
      time:layout.start+(layout.end-layout.start)*(first+g+.5)/columns,stage:"warning",strategy:"S2",
      venue:"poly_us",day:"2026-09-22",token:"poly_us:2026-09-22:0"}))));
    const markers=page.locator(".probability-marker");
    await expect(markers).toHaveCount(4);
    const boxes=await markers.evaluateAll(nodes=>nodes.map(node=>{
      const {left,width}=node.getBoundingClientRect();return {left,width};
    }));
    expect(boxes[1].left-boxes[0].left).toBeLessThan(boxes[0].width);
    for(let g=0;g<counts.length;g++) {
      const marker=page.locator(`.probability-marker[data-event-count="${counts[g]}"]`);
      const label=await marker.getAttribute("aria-label");
      await page.getByRole("button",{name:label!,exact:true}).click({timeout:3000});
      const group=page.getByRole("region",{name:"图表分组事件",exact:true});
      await expect(group).toContainText(`共 ${counts[g]} 条，匹配 ${counts[g]} 条`);
      await expect(group.locator(".event-list button").first()).toHaveAttribute("data-event-id",`dense-group-${g}-event-0`);
    }
  } finally { release([]); }
});

test("high probability groups remain clickable across strategies and adjacent repeated stages",async({page})=>{
  await setup(page);
  const specs=[{strategy:"S1",stage:"trigger",count:31,column:0},
    {strategy:"S1",stage:"rejection",count:37,column:0},{strategy:"S2",stage:"warning",count:41,column:0},
    {strategy:"S3",stage:"trigger",count:43,column:0},{strategy:"S3",stage:"rejection",count:47,column:0},
    {strategy:"S2",stage:"warning",count:53,column:1}];
  let release!:(data:{events:any[];points:any[]})=>void;
  let pending:Promise<{events:any[];points:any[]}>;
  await page.route("**/api/history?*",async r=>r.fulfill({json:{
    ...Object.fromEntries(new URL(r.request().url()).searchParams),points:(await pending).points,next_before:null}}));
  await page.route("**/api/account-events?*",async r=>r.fulfill({json:{next:253,more:false,events:(await pending).events}}));
  for(const viewport of [{width:1440,height:1100},{width:390,height:844}]) {
    pending=new Promise(resolve=>{release=resolve;});
    try {
      await page.setViewportSize(viewport);
      await page.goto("/?venue=poly_us&day=2026-09-22");
      await expect(page.getByLabel("温度档位",{exact:true})).toHaveValue("poly_us:2026-09-22:0");
      await page.getByRole("button",{name:"1H",exact:true}).click();
      const graph=page.getByRole("img",{name:"市场赔率与策略标记"});
      const layout=await graph.evaluate(node=>({start:Number(node.getAttribute("data-start")),
        end:Number(node.getAttribute("data-end")),width:Number(node.getAttribute("viewBox")!.split(" ")[2])}));
      const columns=Math.floor((layout.width-56)/24), first=Math.floor(columns/2);
      const stamp=(column:number)=>layout.start+(layout.end-layout.start)*(first+column+.5)/columns;
      const events=specs.flatMap((spec,g)=>Array.from({length:spec.count},(_,i)=>({
        id:`high-group-${g}-event-${i}`,time:stamp(spec.column)+i/1000,stage:spec.stage,strategy:spec.strategy,
        venue:"poly_us",day:"2026-09-22",token:"poly_us:2026-09-22:0"})));
      events.push({id:"shared-missing-fill",time:stamp(0)+1,stage:"fill",strategy:"manual",
        venue:"poly_us",day:"2026-09-22",token:"poly_us:2026-09-22:0"});
      release({events,points:[{time:layout.start,probability:.99},{time:stamp(0),probability:.99},
        {time:stamp(0)+1,probability:null},{time:stamp(1),probability:.99}].map((p,i)=>({
          ...p,seq:i+1,received_at:p.time,bids:[],asks:[]}))});
      await expect(page.locator(".probability-marker")).toHaveCount(7);
      for(let g=0;g<specs.length;g++) {
        const marker=page.locator(`.probability-marker[data-event-count="${specs[g].count}"]`);
        const label=await marker.getAttribute("aria-label");
        await page.getByRole("button",{name:label!,exact:true}).click({timeout:3000});
        const group=page.getByRole("region",{name:"图表分组事件",exact:true});
        await expect(group).toContainText(`共 ${specs[g].count} 条，匹配 ${specs[g].count} 条`);
        await group.locator(`[data-event-id="high-group-${g}-event-0"]`).click();
        await expect(graph).toHaveAttribute("data-focus",`high-group-${g}-event-0`);
        await expect(page.locator(".probability-event")).toContainText("同期市场概率：99.0%");
      }
      const geometry=await graph.evaluate((node,times)=>{
        const chart=node.closest(".probability-chart")!;
        const grid=[...node.querySelectorAll(".probability-grid")];
        const zero=Number(grid[0].getAttribute("y1")),one=Number(grid.at(-1)!.getAttribute("y1"));
        const left=Number(grid[0].getAttribute("x1")),right=Number(grid[0].getAttribute("x2"));
        const start=Number(node.getAttribute("data-start")),end=Number(node.getAttribute("data-end"));
        return {anchors:[...node.querySelectorAll(".probability-marker-anchor")].map(anchor=>({
          id:anchor.getAttribute("data-event-id"),
          dx:Number(anchor.getAttribute("cx"))-(left+(times[anchor.getAttribute("data-event-id")!]-start)/(end-start)*(right-left)),
          dy:Number(anchor.getAttribute("cy"))-(zero-.99*(zero-one))})),
          guideErrors:[...chart.querySelectorAll(".probability-marker-guide")].map(guide=>{
            const line=guide.getBoundingClientRect(),label=guide.nextElementSibling!.getBoundingClientRect();
            return Math.abs(line.bottom-(label.top+label.height/2));
          }),clearance:chart.querySelector(".probability-controls")!.getBoundingClientRect().top-
            Math.max(...[...chart.querySelectorAll(".probability-marker")].map(marker=>marker.getBoundingClientRect().bottom))};
      },Object.fromEntries(events.map(event=>[event.id,event.time])));
      expect(geometry.anchors.length).toBeGreaterThan(0);
      expect(geometry.anchors.every(anchor=>anchor.id!=="shared-missing-fill"&&Math.abs(anchor.dx)<.01&&Math.abs(anchor.dy)<.01)).toBe(true);
      expect(geometry.guideErrors.every(error=>error<1)).toBe(true);
      expect(geometry.clearance).toBeGreaterThan(0);
      await page.locator(".probability-marker.is-fill").click({timeout:3000});
      await expect(graph).toHaveAttribute("data-focus","shared-missing-fill");
      await expect(page.locator(".probability-event")).toContainText("同期市场概率：—");
    } finally { release({events:[],points:[]}); }
  }
});

test("account event first page remains visible while later pages wait or fail",async({page})=>{
  await setup(page);const now=Date.now()/1000;
  let release!:()=>void, pending=false;
  const later=new Promise<void>(resolve=>{release=resolve;});
  await page.route("**/api/account-events?*",async r=>{
    const after=new URL(r.request().url()).searchParams.get("after");
    if(after==="0")return r.fulfill({json:{next:1000,more:true,events:[
      {id:"first-fill",time:now-20,stage:"fill",strategy:"manual",price:.5,quantity:1,
        venue:"poly_us",day:"2026-09-22",token:"poly_us:2026-09-22:0"},
      {id:"first-candidate",time:now-30,stage:"candidate",strategy:"S3",reason:"首页候选",
        venue:"poly_us",day:"2026-09-22",token:"poly_us:2026-09-22:0"},
    ]}});
    pending=true;await later;
    await r.fulfill({status:503,json:{detail:"later history unavailable"}}).catch(()=>{});
  });
  try {
    await page.goto("/?venue=poly_us&day=2026-09-22");
    await expect.poll(()=>pending).toBe(true);
    const fill=page.locator(".probability-marker.is-fill");
    const candidate=page.locator(".probability-marker").filter({hasText:"S3"});
    await expect(fill).toBeVisible();await expect(candidate).toBeVisible();
    await fill.click();
    await expect(page.getByRole("img",{name:"市场赔率与策略标记"})).toHaveAttribute("data-focus","first-fill");
    release();
    await expect(page.locator("#market-chart")).toContainText("信号记录读取失败 (503)");
    await expect(fill).toBeVisible();await expect(candidate).toBeVisible();
  } finally { release(); }
});

test("a late account event page cannot replace the newly selected market",async({page})=>{
  await setup(page);const now=Date.now()/1000;
  let release!:()=>void, pending=false, finished=false;
  const later=new Promise<void>(resolve=>{release=resolve;});
  await page.route("**/api/account-events?*",async r=>{
    const query=new URL(r.request().url()).searchParams, venue=query.get("venue")!;
    if(venue==="poly_us" && query.get("after")==="1000") {
      pending=true;await later;
      await r.fulfill({json:{next:2000,more:false,events:[{id:"late-old-fill",time:now-10,
        stage:"fill",strategy:"manual",venue,day:"2026-09-22",token:`${venue}:2026-09-22:0`}]}}).catch(()=>{});
      finished=true;return;
    }
    return r.fulfill({json:{next:1000,more:venue==="poly_us",events:[{id:`${venue}-candidate`,
      time:now-30,stage:"candidate",strategy:venue==="poly_us"?"S1":"S2",
      venue,day:"2026-09-22",token:`${venue}:2026-09-22:0`}]}});
  });
  try {
    await page.goto("/?venue=poly_us&day=2026-09-22");
    await expect.poll(()=>pending).toBe(true);
    await expect(page.locator(".probability-marker")).toHaveText(/S1/);
    await page.getByLabel("平台",{exact:true}).selectOption("kalshi");
    await expect(page.getByLabel("温度档位",{exact:true})).toHaveValue("kalshi:2026-09-22:0");
    await expect(page.locator(".probability-marker")).toHaveText(/S2/);
    release();await expect.poll(()=>finished).toBe(true);
    await page.evaluate(()=>new Promise<void>(resolve=>requestAnimationFrame(()=>requestAnimationFrame(()=>resolve()))));
    await expect(page.locator(".probability-marker")).toHaveCount(1);
    await expect(page.locator(".probability-marker")).toHaveText(/S2/);
    await expect(page.locator(".probability-marker.is-fill")).toHaveCount(0);
  } finally { release(); }
});

test("large event history mounts only the open page and locates first and last originals",async({page})=>{
  await setup(page);const now=Date.now()/1000;
  const events=Array.from({length:10000},(_,i)=>({id:`history-event-${String(i).padStart(5,"0")}`,
    time:now-20000+i,stage:i===0?"fill":"rejection",strategy:i===0?"manual":"S1",
    venue:"poly_us",day:"2026-09-22",token:i===0||i===9999?"poly_us:2026-09-22:0":null,
    price:.5,quantity:1,order_id:`order-${i}`}));
  await page.route("**/api/account-events?*",r=>r.fulfill({json:{next:10000,more:false,events}}));
  await page.goto("/?venue=poly_us&day=2026-09-22");
  const summary=page.getByText("所选市场日信号与交易记录",{exact:true});
  await expect(summary.locator("..")).toContainText("10000 条");
  await expect(page.locator(".event-list button")).toHaveCount(0);
  await expect(page.locator(".probability-marker")).toHaveCount(2);
  await summary.click();
  const history=page.getByRole("region",{name:"历史信号与交易记录",exact:true});
  await expect(history.locator(".event-list button")).toHaveCount(50);
  await history.locator('[data-event-id="history-event-00000"]').click();
  const graph=page.getByRole("img",{name:"市场赔率与策略标记"});
  await expect(graph).toHaveAttribute("data-focus","history-event-00000");
  await history.getByLabel("事件页码",{exact:true}).fill("200");
  await expect(history.locator(".event-list button")).toHaveCount(50);
  await history.locator('[data-event-id="history-event-09999"]').click();
  await expect(graph).toHaveAttribute("data-focus","history-event-09999");
  await history.getByLabel("按事件或订单 ID 检索",{exact:true}).fill("order-0");
  await expect(history.locator(".event-list button")).toHaveCount(1);
  await history.locator('[data-event-id="history-event-00000"]').click();
  await expect(graph).toHaveAttribute("data-focus","history-event-00000");
  await summary.click();
  await expect(page.locator(".event-list button")).toHaveCount(0);
});

test("dense marker groups keep original fills and both strategy legs accessible",async({page})=>{
  await setup(page);const now=Date.now()/1000;
  const common={venue:"poly_us",day:"2026-09-22",token:"poly_us:2026-09-22:0"};
  const events=[...Array.from({length:10000},(_,i)=>({...common,id:`warning-${i}`,time:now-120+i/1000,
    stage:"warning",strategy:"S2",reason:"天气证据"})),
    ...Array.from({length:51},(_,i)=>({...common,id:`native-fill-${i}`,time:now-110+i/1000,
      stage:"fill",strategy:"manual",price:.8,quantity:1})),
    ...[0,1].map(i=>({...common,id:`basket-leg-${i}`,group_id:"basket-original",time:now-110,
      token:`poly_us:2026-09-22:${i}`,stage:"candidate",strategy:"S1",probability:.95,quantity:1}))];
  await page.route("**/api/account-events?*",r=>{
    const q=new URL(r.request().url()).searchParams;
    return r.fulfill({json:{next:10053,more:false,events:q.get("day")==="2026-09-22"?events:[]}});
  });
  await page.route("**/api/history?*",r=>{const q=new URL(r.request().url()).searchParams;
    return r.fulfill({json:{...Object.fromEntries(q),points:[{seq:1,time:now-150,received_at:now-150,
      probability:.42,probability_source:"poly_us",bids:[],asks:[]}],next_before:null}});});
  await page.goto("/?venue=poly_us&day=2026-09-22");
  const markers=page.locator(".probability-marker");
  await expect(page.locator('.probability-marker[data-event-count="10000"]')).toBeVisible();
  expect(await markers.count()).toBeLessThan(100);
  expect(await markers.evaluateAll(nodes=>nodes.reduce((sum,node)=>sum+Number(node.getAttribute("data-event-count")),0))).toBe(10052);
  await page.locator('.probability-marker.is-fill[data-event-count="51"]').click();
  const group=page.getByRole("region",{name:"图表分组事件",exact:true});
  await expect(group.locator(".event-list button")).toHaveCount(50);
  await group.getByRole("button",{name:"下一页",exact:true}).click();
  await expect(group.locator(".event-list button")).toHaveCount(1);
  await group.locator('[data-event-id="native-fill-50"]').click();
  const graph=page.getByRole("img",{name:"市场赔率与策略标记"});
  await expect(graph).toHaveAttribute("data-focus","native-fill-50");
  await expect(page.locator(".probability-event")).toContainText("同期市场概率：42.0%");
  await expect(page.locator(".probability-event")).toContainText("价格 US$0.80");
  await expect(page.locator(".probability-marker.is-focused")).toHaveCount(1);
  await page.getByText("所选市场日信号与交易记录",{exact:true}).click();
  const history=page.getByRole("region",{name:"历史信号与交易记录",exact:true});
  await history.getByLabel("按事件或订单 ID 检索",{exact:true}).fill("basket-original");
  await expect(history.locator(".event-list button")).toHaveCount(2);
  await history.locator('[data-event-id="basket-leg-0"]').click();
  await expect(graph).toHaveAttribute("data-focus","basket-leg-0");
  await history.locator('[data-event-id="basket-leg-1"]').click();
  await expect(page.getByLabel("温度档位",{exact:true})).toHaveValue("poly_us:2026-09-22:1");
  await expect(graph).toHaveAttribute("data-focus","basket-leg-1");
  await expect(page.locator(".probability-group")).toHaveCount(0);
  await expect(markers).toHaveCount(1);
  await page.getByLabel("已挂牌日期").selectOption("2026-09-23");
  await expect(page.getByLabel("市场日",{exact:true})).toHaveValue("2026-09-23");
  await expect(graph).toHaveAttribute("data-focus","");
  await expect(page.locator(".event-list button")).toHaveCount(0);
  await expect(markers).toHaveCount(0);
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

test("weather failure does not suppress market price history",async({page})=>{
  await setup(page);
  await page.route("**/api/history?*",r=>{const q=new URL(r.request().url()).searchParams;const now=Date.now()/1000;
    return r.fulfill({json:{...Object.fromEntries(q),points:[{seq:1,time:now-60,received_at:now-60,
      probability:.45,probability_source:"kalshi_last_trade",bids:[],asks:[[.8,1]]}],next_before:null}});});
  await page.route("**/api/weather-history?*",r=>r.fulfill({status:503,json:{detail:"weather unavailable"}}));
  await page.goto("/?venue=kalshi&day=2026-09-19");
  await expect(page.getByRole("region",{name:"天气分析"})).toContainText("天气数据：Error: weather-history 读取失败 (503)");
  await expect(page.locator("#market-chart").getByRole("alert")).toHaveCount(0);
  await expect(page.getByRole("region",{name:"天气分析"})).toContainText("45.00%");
  await expect(page.getByTestId("probability-line")).toHaveCount(1);
});

test("history timeout stays by probability while weather remains available",async({page})=>{
  await setup(page);
  await page.addInitScript(()=>{
    const fetch=window.fetch.bind(window);
    window.fetch=(input,init)=>{
      const url=new URL(input instanceof Request?input.url:String(input),location.href);
      if(url.pathname!=="/api/history")return fetch(input,init);
      const signals=[AbortSignal.timeout(100)];
      if(init?.signal)signals.push(init.signal);
      return fetch(input,{...init,signal:AbortSignal.any(signals)});
    };
  });
  await page.route("**/api/history?*",async r=>{
    await new Promise(resolve=>setTimeout(resolve,300));
    await r.fulfill({status:503,json:{detail:"cold history page"}}).catch(()=>{});
  });
  await page.route("**/api/weather-history?*",r=>{
    const q=new URL(r.request().url()).searchParams, start=Date.parse(q.get("day")+"T05:00:00Z")/1000;
    return r.fulfill({json:{venue:q.get("venue"),day:q.get("day"),start,end:start+86400,
      as_of:start+3600,window_source:"contract",sources:{hourly_temp:[{time:start,value:70,
        received_at:start,source:"hourly_temp",version:"h1",issued_at:null}]},missing_sources:[],cli:[]}});
  });
  await page.goto("/?venue=kalshi&day=2026-09-19");
  await expect(page.locator("#market-chart").getByRole("alert"))
    .toHaveText("行情历史读取超时，请稍后重试。");
  const weather=page.getByRole("region",{name:"天气分析"});
  await expect(weather.getByRole("alert")).toHaveCount(0);
  await expect(weather).not.toContainText("TimeoutError");
  await expect(weather).not.toContainText("行情历史");
  await expect(weather).toContainText("已采集官方小时最高温：70.0 °F");
  await expect(weather.getByLabel("天气与价格交互图").locator("canvas").first()).toBeVisible();
});

test("probability uses platform values and keeps source timing, extrema and gaps",()=>{
  const quote={time:100,received_at:100,probability:.27,probability_received_at:180,bids:[],asks:[[.99,1]]};
  expect(marketProbability(quote)).toBe(.27);
  expect(marketProbability({...quote,probability:null})).toBeNull();
  expect(marketProbability({...quote,probability:0})).toBe(0);
  const minutes=priceMinutes({venue:"kalshi",day:"2026-09-19",start:120,end:400,as_of:400,
    window_source:"contract",sources:{},missing_sources:[],cli:[]},[quote],"a");
  expect(minutes[0].value).toBeNull();expect(minutes[1].value).toBe(27);
  const samples=[.2,.8,.1,.3].map((value,i)=>({time:100+i,value,quote}));
  expect(displaySamples(samples,0,1000000).map(p=>p.value)).toEqual([.2,.8,.1,.3]);
  expect(sampleAt(samples,99)).toBeUndefined();expect(sampleAt(samples,704)).toBeUndefined();
  expect(sampleAt([...samples,{time:104,value:null,quote}],105)?.value).toBeNull();
});

test("probability ranges retain bins and S1 signal legs stay scoped on the curve",async({page})=>{
  await setup(page);const now=Date.parse("2026-09-19T19:00:00Z")/1000;let requests=0;
  await page.route("**/api/markets?*",r=>r.fulfill({json:{venue:"kalshi",days:[{day:"2026-09-19",contracts:[0,1].map(i=>({
    venue:"kalshi",local_day:"2026-09-19",yes_token_id:`kalshi:2026-09-19:${i}`,title:`档位${i}`,
    no_token_id:`no${i}`,condition_id:`market${i}`,active:false,tick_size:"0.01",minimum_order_size:1,
    close_time:"2026-09-19T19:00:00Z"}))}]}}));
  await page.route("**/api/history?*",async r=>{
    requests++;const q=new URL(r.request().url()).searchParams;const offset=q.get("token")!.endsWith(":1")?.2:0;
    return r.fulfill({json:{...Object.fromEntries(q),points:[{seq:0,time:now-7230,received_at:now-7230,
      probability:.3,probability_source:"kalshi_last_trade",bids:[],asks:[]}, ...[0,1,2].map(i=>({seq:i+1,time:now-120+i*60,
      received_at:now-120+i*60,probability:.4+offset+i*.01,probability_source:"kalshi_last_trade",
      probability_time:now-120+i*60,probability_received_at:now-120+i*60,bids:[],asks:[[.99,1]]}))],next_before:null}});
  });
  await page.route("**/api/account-events?*",r=>r.fulfill({json:{next:3,more:false,events:[{id:"early-s2",
    time:now-7200,stage:"candidate",strategy:"S2",reason:"较早信号",venue:"kalshi",day:"2026-09-19",
    token:"kalshi:2026-09-19:0"}, ...[0,1].map(i=>({
    id:`s1-leg-${i}`,group_id:"s1-basket",time:now-30,stage:"candidate",strategy:"S1",probability:.95,
    venue:"kalshi",day:"2026-09-19",token:`kalshi:2026-09-19:${i}`,quantity:1,
  }))]}}));
  await page.goto("/?venue=kalshi&day=2026-09-19");
  const graph=page.getByRole("img",{name:"市场赔率与策略标记"});
  await expect(page.getByTestId("probability-line")).toHaveCount(2);
  await expect(page.locator(".probability-summary")).toContainText("42.0%");
  await page.getByRole("button",{name:"1H",exact:true}).click();
  await expect(graph).toHaveAttribute("data-start",String(now-3600));
  await expect(graph).toHaveAttribute("data-end",String(now));
  expect(requests).toBe(1);
  await page.locator(".probability-marker").click();
  await expect(page.locator(".probability-event")).toContainText("同期市场概率：41.0%");
  await expect(graph).toHaveAttribute("data-range","1H");
  await page.getByLabel("温度档位",{exact:true}).selectOption("kalshi:2026-09-19:1");
  await expect(page.locator(".probability-summary")).toContainText("62.0%");
  await expect(graph).toHaveAttribute("data-start",String(now-3600));
  await expect(page.locator(".probability-marker")).toHaveCount(1);
  await page.locator(".probability-marker").click();
  await expect(page.locator(".probability-event")).toContainText("同期市场概率：61.0%");
  await page.getByLabel("温度档位",{exact:true}).selectOption("kalshi:2026-09-19:0");
  await expect(page.locator(".probability-summary")).toContainText("42.0%");
  expect(requests).toBe(2);
  await page.getByText("所选市场日信号与交易记录",{exact:true}).click();
  await page.locator(".event-list button").filter({hasText:"较早信号"}).click();
  await expect(graph).toHaveAttribute("data-range","ALL");
  await expect(page.locator(".probability-event")).toContainText("同期市场概率：30.0%");
  await expect(graph).toHaveAttribute("data-start",String(now-7230));
  await page.screenshot({path:test.info().outputPath("probability-signals-desktop.png"),fullPage:true});
  await graph.focus();await graph.press("Home");
  await expect(page.locator(".probability-summary")).toContainText("30.0%");
  await page.setViewportSize({width:390,height:844});
  expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBeTruthy();
  await page.screenshot({path:test.info().outputPath("probability-signals-mobile.png"),fullPage:true});
});

test("Paper limit price stays separate from native fills and survives refresh",async({page})=>{
  await setup(page);
  const snapshot={cash:99.9597,equity:100,available:99.9597,positions:[],
    orders:[{order_id:"paper-request-1",token:"poly_us:2026-09-19:0",side:"BUY",quantity:3,filled:3,remaining:0,price:.02,status:"FILLED",owner:"manual"}],
    fills:[
      {order_id:"paper-request-1",token:"poly_us:2026-09-19:0",side:"BUY",quantity:2,price:.01,fee:.0001,ts:Date.parse("2026-09-19T18:00:00Z")*1e6},
      {order_id:"paper-request-1",token:"poly_us:2026-09-19:0",side:"BUY",quantity:1,price:.02,fee:.0002,ts:Date.parse("2026-09-19T18:00:00Z")*1e6},
    ]};
  await page.route("**/api/snapshot",r=>r.fulfill({json:{cursor:1,contracts:[],books:{},weather:{},health:{},accounts:[{account:"sandbox-poly_us-knyc",mode:"sandbox",run_id:"paper",snapshot}]}}));
  await page.goto("/?venue=poly_us&day=2026-09-19");
  const orders=page.getByRole("table",{name:"模拟订单",exact:true});
  const fills=page.getByRole("table",{name:"模拟成交明细",exact:true});
  await expect(orders).toContainText("委托限价");
  await expect(orders).toContainText("2.0¢");
  await expect(fills.getByRole("row")).toHaveCount(3);
  await expect(fills).toContainText("1.0¢");
  await expect(fills).toContainText("$0.0001");
  await expect(fills).toContainText("$0.0002");
  await expect(fills).toContainText("14:00:00");
  await page.reload();
  await expect(fills.getByRole("row")).toHaveCount(3);
  await expect(fills).toContainText("paper-request-1");
  snapshot.fills=[];
  await page.reload();
  await expect(fills).toContainText("已成交订单缺少成交明细，成交价与费用待核验");
  await expect(fills).not.toContainText("2.0¢");
});

test("backtest entrypoint replaces the selected result",async({page})=>{
  await setup(page);const run=(id:string)=>({run_id:id,status:"completed",updated:1,config:{venue:"kalshi",start:Date.parse("2026-09-19T05:00:00Z")/1000,end:Date.parse("2026-09-20T05:00:00Z")/1000,strategy:"S3",cash:100},snapshot:{equity:id==="first"?101:102},curve:[],events:[],history_complete:false});
  await page.route("**/api/backtests",r=>r.fulfill({json:[run("first"),run("second")]}));
  await page.route("**/api/backtests/*",r=>r.fulfill({json:run(r.request().url().split("/").at(-1)!)}));
  await page.route("**/api/market-day?*",r=>r.fulfill({json:{venue:"kalshi",day:"2026-09-19",contracts:[]}}));
  await page.goto("/");await page.getByRole("button",{name:"回测",exact:true}).click();await expect(page.locator(".bt-result")).toHaveAttribute("data-run-id","first");
  await page.getByLabel("历史运行",{exact:true}).selectOption("second");await expect(page.locator(".bt-result")).toHaveCount(1);await expect(page.locator(".bt-result")).toHaveAttribute("data-run-id","second");
});
