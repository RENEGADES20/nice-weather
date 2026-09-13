import { createChart, LineSeries, createSeriesMarkers, type IChartApi, type ISeriesApi, type Time } from "lightweight-charts";
import { Streamlit } from "streamlit-component-lib";
import { nonNullSegments } from "./difference";
import { formatAxisTime } from "./series";
import "./terminal.css";

type Market = { token: string; date: string; bin: string; outcome: string; condition: string; tick_size: number; fee_rate: number; fee_exponent: number; minimum_order_size: number; rejection?: string | null };
type Book = { valid: boolean; reason?: string | null; received_ns?: number; bids: [string, string][]; asks: [string, string][] };
type Row = Record<string, any>;
export type TerminalPayload = { mode: string; account: string; accountMode: string; connected: boolean; snapshot: Row; markets: Market[]; selectedToken: string; depth: Record<string, Book>; performance: {points: {time: number; value: number | null}[]; days: Row[]}; history: Row[] | null; historyReady?: boolean; historyError?: string | null; notices: Row[]; updated: number | null };
let data: TerminalPayload;
let mounted = false;
let desiredToken = "", pendingOrder = "", binsSignature = "", datesSignature = "";
const histories = new Map<string, Row[]>();
let pnlSignature = "", priceSignature: Row[] | null = null, lastPriceTime = 0, renderedRange = "";

let chart: IChartApi, line: ISeriesApi<"Line">, pnl: IChartApi, pnlLine: ISeriesApi<"Line">;
let markers: ReturnType<typeof createSeriesMarkers<Time>>;
const pnlSegments: ISeriesApi<"Line">[] = [];
let pendingClose: Row | null = null;
let initialPrice = false;
let chartToken = "", month = new Date().toLocaleDateString("en-CA", {timeZone: "America/New_York"}).slice(0, 7);
let table = "positions", selectedDay = "", range = "ALL", priceRange = "ALL";
let preview: {kind: string; payload: Row} | null = null;
const el = (id: string) => document.getElementById(id)!;
const input = (id: string) => el(id) as HTMLInputElement;
const escape = (v: unknown) => String(v ?? "").replace(/[&<>"']/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]!));
const money = (v: unknown) => v === null || v === undefined ? "—" : `${Number(v) < 0 ? "−" : ""}$${Math.abs(Number(v)).toFixed(2)}`;
const cents = (v: unknown) => v === null || v === undefined ? "—" : `${(Number(v) * 100).toFixed(1)}¢`;
const send = (kind: string, payload: Row = {}) => {const id=crypto.randomUUID();Streamlit.setComponentValue({id,kind,selectedToken:data?.selectedToken,...payload});return id;};
const market = () => data.markets.find(m => m.token === data.selectedToken);
const book = (): Book => data.depth[data.selectedToken] || {valid:false,bids:[],asks:[]};
const height = () => requestAnimationFrame(() => Streamlit.setFrameHeight(Math.ceil(el("terminal").getBoundingClientRect().height)+4));
const options = (items: string[], value: string) => items.map(i => `<option ${i === value ? "selected" : ""}>${escape(i)}</option>`).join("");
const draftFields=["t-size","t-unit","t-limit","t-slip","t-type","t-tif","t-expiry","t-post","t-side-value"];
const draftKey=()=>`terminal-draft-${data.accountMode}-${data.account}-${data.selectedToken}`;
function saveDraft(){localStorage.setItem(draftKey(),JSON.stringify(Object.fromEntries(draftFields.map(id=>[id,id==="t-post"?input(id).checked:input(id).value]))));}

function openDialog(id:string){
  const dialog=el(id) as HTMLDialogElement;
  const anchor=document.activeElement?.getBoundingClientRect().top||0;
  dialog.style.top=`${Math.max(0,anchor+window.scrollY-40)}px`;
  dialog.showModal();
  requestAnimationFrame(()=>dialog.scrollIntoView({block:"center"}));
}

function shell() {
  document.body.classList.add("terminal-body");
  el("app").innerHTML = `<div id="terminal">
    <div class="t-status"><span id="t-status"></span><span>KLGA · ET · pUSD simulated funds</span></div>
    <div class="t-metrics" id="t-metrics"></div><div class="t-toolbar"><button id="t-balance">Edit Paper cash</button><button id="t-reset">Reset Paper</button><span id="t-funding"></span></div>
    <details id="t-pnl" open><summary>Profit & loss <span>Net of fees · Bid valuation</span></summary>
      <div class="t-toolbar" id="t-ranges">${["1D","7D","30D","ALL"].map(v=>`<button data-range="${v}">${v}</button>`).join("")}</div><div id="t-pnl-chart"></div>
    </details>
    <div class="t-market-header"><div><h2 id="t-title">KLGA daily highest temperature</h2><span id="t-subtitle"></span></div><label>Market date<select id="t-date"></select></label><a href="https://polymarket.com/search?_q=highest%20temperature%20NYC" target="_blank" rel="noreferrer">Market rules ↗</a></div>
    <div class="t-grid">
      <aside class="t-outcomes"><h3>Temperature outcomes</h3><div class="t-bin-head"><span>Interval</span><span>YES / NO</span></div><div id="t-bins"></div></aside>
      <section class="t-market"><div class="t-toolbar"><strong id="t-price"></strong><select aria-label="Chart range" id="t-price-range">${options(["1H","6H","24H","ALL"],"ALL")}</select><button id="t-fit">Reset</button></div><div id="t-history-state"></div><div id="t-price-chart"></div>
        <div class="t-book-head"><h3>Order book</h3><span id="t-spread"></span><label><input type="checkbox" id="t-all-depth"> All levels</label></div>
        <div id="t-book-status"></div><div class="t-book-columns"><span>Price</span><span>Shares</span><span>Cumulative</span></div><div class="t-books"><div id="t-asks"></div><div id="t-bids"></div></div>
        <details><summary>Depth chart</summary><div id="t-depth"></div></details>
      </section>
      <section class="t-ticket"><h3>Trade <span id="t-outcome"></span></h3>
        <div class="t-segments" id="t-side"><button data-side="BUY" class="active">Buy</button><button data-side="SELL">Sell</button></div>
        <input id="t-side-value" type="hidden" value="BUY">
        <div class="t-segments" id="t-outcome-buttons"><button data-outcome="YES">YES</button><button data-outcome="NO">NO</button></div>
        <label>Order type<select id="t-type">${options(["Limit","Market"],"Limit")}</select></label>
        <div class="t-two"><label>Size in<select id="t-unit">${options(["Amount","Shares"],"Amount")}</select></label><label><span id="t-size-label">Amount (pUSD)</span><input id="t-size" type="number" min="0" step="0.01" value="1"></label></div>
        <button id="t-minimum">Use minimum shares</button><div class="t-percent">${[25,50,75,100].map(v=>`<button data-percent="${v}">${v}%</button>`).join("")}</div>
        <label id="t-limit-label">Limit price (¢)<input id="t-limit" type="number" min="0.1" max="99.9" step="0.1" value="40"></label>
        <label id="t-slip-label" hidden>Maximum slippage (¢)<input id="t-slip" type="number" min="0.1" max="99" step="0.1" value="1"></label>
        <details><summary>Advanced</summary><label>Time in force<select id="t-tif">${options(["GTC","GTD","IOC","FOK"],"GTC")}</select></label><label>Expiry (your local time)<input id="t-expiry" type="datetime-local"></label><label><input id="t-post" type="checkbox"> Post-only</label></details>
        <div id="t-estimate" class="t-estimate"></div><button id="t-review" class="t-primary">Review buy</button><p id="t-limit-note">$5 per outcome · $20 per airport day</p><div id="t-notices"></div>
      </section>
    </div>
    <section class="t-account"><div class="t-toolbar" id="t-table-tabs">${[["positions","Positions"],["open","Open orders"],["fills","Fills"],["orders","Order history"]].map(([k,v])=>`<button data-table="${k}">${v}</button>`).join("")}</div><div id="t-table"></div></section>
    <details id="t-strategy"><summary>Strategy controls</summary><p id="t-strategy-status"></p><button id="t-start">Start selected-market acceptance strategy</button><button id="t-stop">Stop strategy</button><p>Acceptance utility only; no validated weather strategy. Stop cancels strategy orders and retains positions.</p></details>
    <details id="t-calendar" open><summary>PnL calendar <span>New York calendar days</span></summary><div class="t-toolbar"><button id="t-prev">←</button><strong id="t-month"></strong><button id="t-next">→</button><button id="t-current">This month</button><span id="t-month-pnl"></span></div><div class="t-calendar" id="t-days"></div><div id="t-day-detail"></div></details>
    <dialog id="t-dialog"><h3>Confirm Paper order</h3><div id="t-review-summary"></div><div class="t-toolbar"><button id="t-dismiss">Back</button><button id="t-confirm" class="t-primary">Confirm</button></div></dialog>
    <dialog id="t-cash-dialog"><form id="t-cash-form"><h3 id="t-cash-title"></h3><label>Cash balance (pUSD)<input id="t-cash" type="number" min="0" max="1000000" step="0.000001" required></label><p id="t-cash-help"></p><button type="button" id="t-cash-back">Back</button><button type="submit">Review account change</button></form></dialog><dialog id="t-edit"><form id="t-edit-form"><h3>Edit limit order</h3><label>Limit price (¢)<input id="t-edit-price" type="number" min="0.1" max="99.9" required></label><label>Total shares, including filled<input id="t-edit-size" type="number" min="0.000001" step="0.000001" required></label><p>The replacement is submitted after cancellation is confirmed.</p><button type="button" id="t-edit-back">Back</button><button type="submit">Review change</button></form></dialog>
  </div>`;
  const settings = {layout:{background:{color:"#ffffff"},textColor:"#667085"},grid:{vertLines:{visible:false},horzLines:{color:"#f1f3f6"}},timeScale:{timeVisible:true,tickMarkFormatter:(time:Time,type:number)=>formatAxisTime(Number(time),type,"America/New_York")},localization:{timeFormatter:(time:Time)=>new Date(Number(time)*1000).toLocaleString("en-US",{timeZone:"America/New_York"})},autoSize:true};
  chart = createChart(el("t-price-chart"), {...settings,height:270});
  line = chart.addSeries(LineSeries,{color:"#2563eb",lineWidth:2,priceFormat:{type:"custom",formatter:(p:number)=>`${p.toFixed(1)}¢`}});
  markers = createSeriesMarkers(line, []);
  pnl = createChart(el("t-pnl-chart"),{...settings,height:200});
  pnlLine = pnl.addSeries(LineSeries,{color:"#16806a",lineWidth:2,priceFormat:{type:"custom",formatter:money}});
  pnlSegments.push(pnlLine);
  for (const id of ["t-pnl","t-calendar"]) {
    const d = el(id) as HTMLDetailsElement;
    d.open = localStorage.getItem(id) !== "closed";
    d.addEventListener("toggle",()=>{localStorage.setItem(id,d.open?"open":"closed");if(d.open){if(id==="t-pnl")renderPnl(true);else renderCalendar();}height();});
  }
  new ResizeObserver(height).observe(el("terminal"));
  el("t-date").onchange = () => selectToken(data.markets.find(m=>m.date===input("t-date").value)?.token);
  el("t-bins").onclick = e => { const target=(e.target as HTMLElement).closest<HTMLElement>("[data-token]"); if(target)selectToken(target.dataset.token); };
  el("t-outcome-buttons").onclick = e => {const out=(e.target as HTMLElement).dataset.outcome; if(out)selectToken(data.markets.find(m=>m.condition===market()?.condition&&m.outcome===out)?.token);};
  el("t-side").onclick = e => {const side=(e.target as HTMLElement).dataset.side;if(side){input("t-side-value").value=side;input("t-unit").value=side==="SELL"?"Shares":"Amount";estimate();}};
  for(const id of draftFields)el(id).oninput=()=>{initialPrice=false;saveDraft();estimate();};
  el("t-all-depth").onchange=renderBook;
  el("t-fit").onclick=()=>chart.timeScale().fitContent();
  el("t-price-range").onchange=()=>{priceRange=input("t-price-range").value;localStorage.setItem("terminal-price-range",priceRange);renderPrice(true);};
  el("t-ranges").onclick=e=>{const r=(e.target as HTMLElement).dataset.range;if(r){range=r;localStorage.setItem("terminal-pnl-range",range);renderPnl(true);}};
  range=localStorage.getItem("terminal-pnl-range")||"ALL";priceRange=localStorage.getItem("terminal-price-range")||"ALL";input("t-price-range").value=priceRange;
  el("t-review").onclick=()=>{try{review("order",draft());}catch(e){el("t-estimate").textContent=String(e);}};
  el("t-dismiss").onclick=()=>{(el("t-dialog") as HTMLDialogElement).close();preview=null;};
  el("t-edit-back").onclick=()=>{(el("t-edit") as HTMLDialogElement).close();preview=null;};
  el("t-edit-form").onsubmit=e=>{e.preventDefault();if(preview){const p={...preview.payload,price:Number(input("t-edit-price").value)/100,target_quantity:Number(input("t-edit-size").value)};(el("t-edit") as HTMLDialogElement).close();review("replace",p);}};
  el("t-confirm").onclick=()=>{if(preview&&data.accountMode==="Paper"&&data.connected){if(pendingOrder)return;pendingOrder=send(preview.kind,{payload:preview.payload});preview=null;estimate();(el("t-dialog") as HTMLDialogElement).close();}};
  el("t-table-tabs").onclick=e=>{const k=(e.target as HTMLElement).dataset.table;if(k){table=k;renderTable();}};
  document.querySelector(".t-percent")!.addEventListener("click",e=>{const p=Number((e.target as HTMLElement).dataset.percent);if(p){const sell=input("t-side-value").value==="SELL";input("t-unit").value=sell?"Shares":"Amount";input("t-size").value=String(Math.floor((sell?availableShares():buyingCapacity())*p/100*1e6)/1e6);estimate();}});
  const moveMonth=(delta:number)=>{const d=new Date(`${month}-01T12:00:00Z`);d.setUTCMonth(d.getUTCMonth()+delta);month=d.toISOString().slice(0,7);renderCalendar();};
  el("t-prev").onclick=()=>moveMonth(-1);el("t-next").onclick=()=>moveMonth(1);
  el("t-current").onclick=()=>{month=new Date().toLocaleDateString("en-CA",{timeZone:"America/New_York"}).slice(0,7);renderCalendar();};
  el("t-days").onclick=e=>{const d=(e.target as HTMLElement).closest<HTMLElement>("[data-day]")?.dataset.day;if(d){selectedDay=d;renderCalendar();}};
  el("t-start").onclick=()=>review("start",{strategy_id:"acceptance_roundtrip",tokens:[data.selectedToken],parameters:{quantity:market()?.minimum_order_size||1,exit_after_quotes:3,require_both:true}});
  el("t-stop").onclick=()=>review("stop",{});
  el("t-minimum").onclick=()=>{input("t-unit").value="Shares";input("t-size").value=String(market()?.minimum_order_size||1);estimate();};
  let cashKind="balance", cashRevision="";
  for(const kind of ["balance","reset"]){el(`t-${kind}`).onclick=()=>{
    cashKind=kind;cashRevision=data.snapshot.account_revision;
    el("t-cash-title").textContent=kind==="reset"?"Reset Paper account":"Edit Paper cash";
    el("t-cash-help").textContent=kind==="reset"?"Starts a new Paper period, clears positions and orders, stops the strategy and resets PnL to zero. Previous facts remain in read-only audit.":"Keeps positions and orders. The difference is recorded as funding; deposits and withdrawals are excluded from trading PnL.";
    input("t-cash").value=String(kind==="reset"?100:data.snapshot.cash);
    openDialog("t-cash-dialog");
  };}
  el("t-cash-back").onclick=()=>(el("t-cash-dialog") as HTMLDialogElement).close();
  el("t-cash-form").onsubmit=e=>{e.preventDefault();(el("t-cash-dialog") as HTMLDialogElement).close();review(cashKind,{cash:Number(input("t-cash").value),expected_revision:cashRevision});};
  mounted=true;
}

function selectToken(token?:string){if(token&&token!==data.selectedToken){saveDraft();localStorage.setItem(`terminal-selection-${data.account}`,token);preview=null;(el("t-dialog") as HTMLDialogElement).close();desiredToken=token;renderTerminal({...data,selectedToken:token,history:null,historyReady:histories.has(token)},true);send("select",{token});}}
function buyingCapacity(){
  const m=market();if(!m)return 0;
  let bin=0,day=0;
  for(const p of data.snapshot.positions||[]){const other=data.markets.find(x=>x.token===p.token);if(other?.condition===m.condition)bin+=p.cost;if(other?.date===m.date)day+=p.cost;}
  for(const o of data.snapshot.orders||[]){if(o.side!=="BUY"||!["ACCEPTED","PARTIALLY_FILLED","SUBMITTED","PENDING_CANCEL"].includes(o.status))continue;const other=data.markets.find(x=>x.token===o.token),cost=o.remaining*o.price;if(other?.condition===m.condition)bin+=cost;if(other?.date===m.date)day+=cost;}
  return Math.max(0,Math.min(Number(data.snapshot.available||0),5-bin,20-day));
}
function availableShares(){const q=(data.snapshot.positions||[]).filter((p:Row)=>p.token===data.selectedToken).reduce((a:number,p:Row)=>a+p.quantity,0);return Math.max(0,q-(data.snapshot.orders||[]).filter((o:Row)=>o.token===data.selectedToken&&o.side==="SELL"&&["ACCEPTED","PARTIALLY_FILLED","SUBMITTED"].includes(o.status)).reduce((a:number,o:Row)=>a+o.remaining,0));}
function draft():Row{
  const m=market(), b=book(), side=input("t-side-value").value;
  if(m?.rejection||b.reason)throw Error(m?.rejection||b.reason||"Order unavailable");
  if(!m||!b.valid)throw Error("Waiting for a fresh complete order book");
  const marketOrder=input("t-type").value==="Market", slip=Number(input("t-slip").value)/100;
  let price=Number(input("t-limit").value)/100;
  if(marketOrder){const levels=side==="BUY"?b.asks:b.bids;if(!levels.length)throw Error(`No ${side==="BUY"?"asks":"bids"} available for a market order; use a resting limit order.`);const best=Number(levels[0][0]);price=side==="BUY"?Math.ceil((best+slip)/m.tick_size-1e-9)*m.tick_size:Math.floor((best-slip)/m.tick_size+1e-9)*m.tick_size;price=Math.max(m.tick_size,Math.min(1-m.tick_size,price));}
  if(!(price>0&&price<1))throw Error("Enter a valid price");
  const size=Number(input("t-size").value);if(!(size>0))throw Error("Enter an amount or share quantity");
  const p:Row={token:m.token,side,price:Number(price.toFixed(6)),tif:marketOrder?"IOC":input("t-tif").value,post_only:!marketOrder&&input("t-post").checked};
  const amount=input("t-unit").value==="Amount"&&side==="BUY";
  p[amount?"amount":"quantity"]=size;
  if(p.tif==="GTD"){const expires=new Date(input("t-expiry").value);if(!Number.isFinite(expires.getTime()))throw Error("Choose an expiry");p.expire_time=expires.toISOString();}
  return p;
}
function estimate(){
  saveDraft();
  const locked=!!pendingOrder||!data.connected||data.accountMode!=="Paper";
  for(const id of ["t-confirm","t-balance","t-reset","t-start","t-stop"])(el(id) as HTMLButtonElement).disabled=locked;
  document.querySelectorAll<HTMLButtonElement>("[data-action]").forEach(button=>button.disabled=locked);
  const side=input("t-side-value").value, marketOrder=input("t-type").value==="Market";
  el("t-limit-label").hidden=marketOrder;el("t-slip-label").hidden=!marketOrder;
  el("t-size-label").textContent=input("t-unit").value==="Amount"&&side==="BUY"?"Amount (pUSD)":"Shares";
  el("t-review").textContent=`Review ${side==="BUY"?"buy":"sell"}`;
  document.querySelectorAll<HTMLElement>("[data-side]").forEach(b=>b.classList.toggle("active",b.dataset.side===side));
  (el("t-review") as HTMLButtonElement).disabled=!data.connected||data.accountMode!=="Paper"||!book().valid||!!pendingOrder;
  try{
    const p=draft(), m=market()!, qty=p.quantity??Math.floor(p.amount/(p.price+m.fee_rate*.25**m.fee_exponent)*1e6)/1e6;
    let remaining=qty,cost=0,fee=0;
    for(const [rawPrice,rawSize] of side==="BUY"?book().asks:book().bids){const price=Number(rawPrice);if(side==="BUY"?price>p.price:price<p.price)break;const n=Math.min(remaining,Number(rawSize));cost+=n*price;fee+=n*m.fee_rate*(price*(1-price))**m.fee_exponent;remaining-=n;if(remaining<=0)break;}
    const filled=qty-remaining;
    el("t-estimate").innerHTML=`<div><span>Shares</span><strong>${qty.toFixed(4)}</strong></div><div><span>Immediately executable</span><strong>${filled.toFixed(4)}</strong></div><div><span>Expected average</span><strong>${filled?cents(cost/filled):"Resting limit"}</strong></div><div><span>Worst price</span><strong>${cents(p.price)}</strong></div><div><span>Estimated fee</span><strong>${money(fee)}</strong></div><div><span>${side==="BUY"?"Estimated cost":"Estimated proceeds"}</span><strong>${money(side==="BUY"?cost+fee:cost-fee)}</strong></div>${side==="BUY"?`<div><span>Payout if filled shares win</span><strong>${money(filled)}</strong></div><div><span>Potential net profit</span><strong>${money(filled-cost-fee)}</strong></div>`:""}<div><span>${side==="BUY"?"Available":"Available shares"}</span><strong>${side==="BUY"?money(data.snapshot.available):availableShares().toFixed(4)}</strong></div>`;
    el("t-limit-note").textContent=`$5 per outcome · $20 per airport day · remaining buy budget ${money(buyingCapacity())}`;
    if(side==="BUY"&&qty*p.price>buyingCapacity()+1e-8)throw Error(`Remaining buy budget: ${money(buyingCapacity())}. Reduce size or close existing exposure.`);
    if(p.tif==="FOK"&&remaining>1e-6)throw Error("FOK requires enough depth for the entire order; reduce size or use IOC.");
    if(m.fee_rate===null||m.fee_exponent===null)throw Error("Fee unavailable; order blocked");
    if(qty<m.minimum_order_size)throw Error(`Minimum order: ${m.minimum_order_size} shares (about ${money(m.minimum_order_size*(p.price+m.fee_rate*.25**m.fee_exponent))}). Use minimum shares.`);
    if(side==="SELL"&&qty>availableShares())throw Error("Insufficient available shares");
    if(side==="BUY"&&qty*(p.price+m.fee_rate*.25**m.fee_exponent)>Number(data.snapshot.available))throw Error("Insufficient available funds");
  }catch(e){el("t-estimate").textContent=String(e).replace("Error: ","");(el("t-review") as HTMLButtonElement).disabled=true;}
}
function review(kind:string,payload:Row){
  if(data.accountMode!=="Paper"||!data.connected||pendingOrder)return;
  preview={kind,payload};
  const m=data.markets.find(m=>m.token===payload.token)||market();
  el("t-review-summary").innerHTML=`<p>Paper · ${escape(data.account)}</p><h4>${escape(m?.date)} · ${escape(m?.bin)} · ${escape(m?.outcome)}</h4><p>${escape(kind)} ${escape(payload.side||"")} ${payload.quantity!==undefined?`${escape(payload.quantity)} shares`:payload.amount!==undefined?money(payload.amount):""}${payload.price!==undefined?` · ${cents(payload.price)}`:""}</p><p>${escape(payload.tif||"")}</p>${kind==="order"?el("t-estimate").innerHTML:""}<p>Worker rechecks current liquidity, fees and account limits. Partial fills are possible.</p>`;
  if(kind==="balance"||kind==="reset")el("t-review-summary").innerHTML=`<p>Paper · ${escape(data.account)}</p><p>${kind==="reset"?"Reset account and PnL":"Set cash balance"}: <strong>${money(payload.cash)}</strong></p><p>${kind==="reset"?"Current positions and orders will be cleared. Previous facts remain in audit.":"Funding adjustment is excluded from trading PnL."}</p>`;
  openDialog("t-dialog");
}
function renderBook(){
  const b=book(), count=input("t-all-depth").checked?10000:10;
  el("t-book-status").textContent=b.reason|| (b.valid?"Live depth · memory only":"Waiting / stale · execution paused");
  el("t-spread").textContent=b.valid&&b.asks.length&&b.bids.length?`Spread ${cents(Number(b.asks[0][0])-Number(b.bids[0][0]))}`:"";
  for(const side of ["asks","bids"] as const){let cumulative=0;const levels=b[side].slice(0,count);const max=levels.reduce((n,l)=>n+Number(l[1]),0)||1;
    el(`t-${side}`).innerHTML=levels.map(([p,q])=>{cumulative+=Number(q);return `<button class="t-level ${side}" data-price="${escape(p)}" style="--depth:${cumulative/max*100}%"><span>${cents(p)}</span><span>${Number(q).toFixed(2)}</span><span>${cumulative.toFixed(2)}</span></button>`;}).join("");
    el(`t-${side}`).onclick=e=>{const p=(e.target as HTMLElement).closest<HTMLElement>("[data-price]")?.dataset.price;if(p){input("t-type").value="Limit";input("t-limit").value=String(Number(p)*100);estimate();}};
  }
  let max=1;const paths=["bids","asks"].map(side=>{let sum=0;const pts=b[side as "bids"|"asks"].map(([p,q])=>{sum+=Number(q);max=Math.max(max,sum);return [Number(p)*600,sum];});return pts;});
  el("t-depth").innerHTML=`<svg viewBox="0 0 600 130" role="img" aria-label="Cumulative order depth">${paths.map((p,i)=>`<polyline fill="none" stroke="${i?"#c64d63":"#16806a"}" stroke-width="2" points="${p.map(([x,y])=>`${x},${125-y/max*120}`).join(" ")}"/>`).join("")}</svg>`;
}
function renderPrice(fit=false){
  const changed=priceSignature!==data.history||renderedRange!==priceRange||fit;
  const b=book(),live=b.valid&&b.bids.length&&b.asks.length?{time:Math.floor((b.received_ns||0)/1e9) as Time,value:(Number(b.bids[0][0])+Number(b.asks[0][0]))*50}:null;
  if(changed){
    const points=new Map<number,number>();
    for(const r of data.history||[]){const time=Math.floor(Date.parse(r.received_at)/1000);if(r.mid!==null)points.set(time,r.mid*100);}
    if(live)points.set(Number(live.time),live.value);
    const seconds=priceRange==="1H"?3600:priceRange==="6H"?21600:priceRange==="24H"?86400:Infinity;
    const values=[...points].sort((a,b)=>a[0]-b[0]).filter(([t])=>t>=Date.now()/1000-seconds).map(([time,value])=>({time:time as Time,value}));
    line.setData(values);priceSignature=data.history;renderedRange=priceRange;lastPriceTime=Number(values.at(-1)?.time||0);
  }else if(live&&Number(live.time)>=lastPriceTime){line.update(live);lastPriceTime=Number(live.time);}
  el("t-history-state").textContent=data.historyError?"Price history temporarily unavailable; current order book is independent.":data.historyReady===false?"Loading price history…":"";
  markers.setMarkers((data.snapshot.fills||[]).filter((f:Row)=>f.token===data.selectedToken).map((f:Row)=>({time:Math.floor(f.ts/1e9) as Time,position:f.side==="BUY"?"belowBar" as const:"aboveBar" as const,color:f.side==="BUY"?"#16806a":"#c64d63",shape:f.side==="BUY"?"arrowUp" as const:"arrowDown" as const,text:f.side})).sort((a:Row,b:Row)=>Number(a.time)-Number(b.time)));
  el("t-price").textContent=b.valid?`${cents(b.bids[0]?.[0])} bid / ${cents(b.asks[0]?.[0])} ask`:"Price unavailable";
  if(fit)chart.timeScale().fitContent();
}
function renderPnl(fit=false){const seconds=range==="1D"?86400:range==="7D"?604800:range==="30D"?2592000:Infinity;
  const today=new Date().toLocaleDateString("en-CA",{timeZone:"America/New_York"});
  const map=new Map(data.performance.points.filter(p=>range==="1D"?new Date(p.time*1000).toLocaleDateString("en-CA",{timeZone:"America/New_York"})===today:p.time>=Date.now()/1000-seconds).map(p=>[p.time,p]));
  const segments=nonNullSegments([...map.values()]);
  while(pnlSegments.length>Math.max(1,segments.length))pnl.removeSeries(pnlSegments.pop()!);
  while(pnlSegments.length<segments.length)pnlSegments.push(pnl.addSeries(LineSeries,{color:"#16806a",lineWidth:2,priceFormat:{type:"custom",formatter:money}}));
  pnlSegments.forEach((api,i)=>api.setData((segments[i]||[]).map(p=>({time:p.time as Time,value:p.value!}))));
  el("t-pnl").dataset.segments=String(segments.length);
  if(fit)pnl.timeScale().fitContent();
  document.querySelectorAll<HTMLElement>("[data-range]").forEach(b=>b.classList.toggle("active",b.dataset.range===range));
}
function renderTable(){
  const rows:Row[]=table==="open"?(data.snapshot.orders||[]).filter((o:Row)=>["ACCEPTED","PARTIALLY_FILLED","SUBMITTED","PENDING_CANCEL"].includes(o.status)):data.snapshot[table]||[];
  const fields=table==="positions"?["quantity","cost","bid","unrealized_pnl"]:table==="fills"?["side","quantity","price","fee"]:["side","quantity","filled","remaining","price","status"];
  el("t-table").innerHTML=rows.length?`<table><thead><tr><th>Market / outcome</th>${fields.map(f=>`<th>${escape(f.replaceAll("_"," "))}</th>`).join("")}<th></th></tr></thead><tbody>${rows.map((r,i)=>{const m=data.markets.find(m=>m.token===r.token);return `<tr><td>${escape(m?.date)}<br><strong>${escape(m?.bin)} ${escape(m?.outcome)}</strong></td>${fields.map(f=>`<td>${["bid","price"].includes(f)?cents(r[f]):["cost","fee","unrealized_pnl"].includes(f)?money(r[f]):escape(r[f])}</td>`).join("")}<td>${table==="positions"?`<button data-row="${i}" data-action="sell">Sell</button><button data-row="${i}" data-action="close">Close all</button>`:table==="open"?`<button data-row="${i}" data-action="cancel">Cancel</button><button data-row="${i}" data-action="replace">Edit</button>`:""}</td></tr>`;}).join("")}</tbody></table>`:`<div class="t-empty">${data.accountMode==="Live"?"Live account is not connected":"No "+table.replace("open","open orders")}</div>`;
  el("t-table").onclick=e=>{const b=(e.target as HTMLElement).closest<HTMLElement>("[data-row]");if(!b)return;const r=rows[Number(b.dataset.row)];const action=b.dataset.action!;
    if(action==="cancel")review("cancel",{order_id:r.order_id});
    else if(action==="replace"){if(data.accountMode!=="Paper"||!data.connected)return;preview={kind:"replace",payload:{order_id:r.order_id,token:r.token,side:r.side,tif:"GTC"}};input("t-edit-price").value=String(r.price*100);input("t-edit-price").step=String((data.markets.find(m=>m.token===r.token)?.tick_size||.01)*100);input("t-edit-size").value=String(r.quantity);openDialog("t-edit");}
    else {selectToken(r.token);input("t-side-value").value="SELL";input("t-unit").value="Shares";input("t-size").value=String(r.quantity);input("t-type").value="Market";initialPrice=false;saveDraft();if(action==="close")pendingClose=r;finishClose();if(action!=="close")el("t-review").scrollIntoView({behavior:"smooth",block:"center"});estimate();}
  };
}
function renderCalendar(){
  el("t-month").textContent=month;const first=new Date(`${month}-01T12:00:00Z`),offset=(first.getUTCDay()+6)%7,last=new Date(first.getUTCFullYear(),first.getUTCMonth()+1,0).getDate();
  const byDay=new Map(data.performance.days.map(d=>[d.day,d]));const days=data.performance.days.filter(d=>d.day.startsWith(month));
  el("t-month-pnl").textContent=`${money(days.reduce((s,d)=>s+(d.pnl||0),0))}${days.some(d=>d.pnl===null)?" · incomplete":""}`;
  el("t-days").innerHTML=[...['Mon','Tue','Wed','Thu','Fri','Sat','Sun'].map(d=>`<small>${d}</small>`),...Array(offset).fill('<div></div>'),...Array.from({length:last},(_,i)=>{const day=`${month}-${String(i+1).padStart(2,"0")}`,d=byDay.get(day);return `<button data-day="${day}" class="${d?.pnl>0?"profit":d?.pnl<0?"loss":""} ${day===selectedDay?"selected":""}"><span>${i+1}</span><strong>${d?money(d.pnl):"—"}</strong><small>${d?`${d.fills} fills`:"No data"}</small></button>`;})].join("");
  const selected=byDay.get(selectedDay);
  const fills=(data.snapshot.fills||[]).filter((f:Row)=>new Date(f.ts/1e6).toLocaleDateString("en-CA",{timeZone:"America/New_York"})===selectedDay);
  el("t-day-detail").innerHTML=selected?`<p><strong>${selectedDay} · ${escape(selected.status)}</strong>　Net ${money(selected.pnl)}　Fees ${money(selected.fees)}　${selected.fills} fills</p><p>Realized change ${money(selected.realized_change)} · Unrealized change ${money(selected.unrealized_change)}</p>${fills.map((f:Row)=>{const m=data.markets.find(m=>m.token===f.token);return `<p>${new Date(f.ts/1e6).toLocaleTimeString("en-US",{timeZone:"America/New_York"})} · ${escape(m?.bin)} ${escape(m?.outcome)} · ${escape(f.side)} ${f.quantity} shares @ ${cents(f.price)} · fee ${money(f.fee)}</p>`;}).join("")}`:"";
}
function finishClose(){if(pendingClose?.token===data.selectedToken&&book().valid){const r=pendingClose;pendingClose=null;try{const p=draft();review("close",{token:r.token,quantity:r.quantity,price:p.price});}catch{estimate();}}}
export function renderTerminal(next:TerminalPayload,optimistic=false){
  if(!optimistic&&next.history!==null&&next.historyReady!==false){histories.set(next.selectedToken,next.history);while(histories.size>32)histories.delete(histories.keys().next().value!);}
  if(!optimistic&&!next.markets.some(m=>m.token===desiredToken))desiredToken="";
  if(desiredToken)next={...next,selectedToken:desiredToken,historyReady:histories.has(desiredToken)};
  next={...next,history:histories.get(next.selectedToken)||[]};
  if(pendingOrder&&next.notices.some(n=>n.request_id===pendingOrder&&n.status!=="queued"))pendingOrder="";
  const first=!mounted, changed=data?.selectedToken!==next.selectedToken;
  data=next;if(first)shell();
  el("t-status").textContent=data.accountMode==="Live"?"LIVE · Not connected · Execution disabled":`PAPER · ${data.connected?"Connected":"Disconnected / paused"}`;
  const s=data.snapshot,today=new Date().toLocaleDateString("en-CA",{timeZone:"America/New_York"});const daily=data.performance.days.find(d=>d.day===today);
  el("t-metrics").innerHTML=[["Net PnL",s.total_pnl],["Today",daily?.pnl],["Equity",s.equity],["Available",s.available]].map(([label,value])=>`<div><span>${label}</span><strong>${money(value)}</strong></div>`).join("");
  const m=market();el("t-title").textContent=`KLGA · ${m?.date||"Daily highest temperature"}`;el("t-subtitle").textContent=`${m?.bin||"Waiting for verified markets"} · ${m?.outcome||""}`;el("t-outcome").textContent=m?.outcome||"";
  const dates=[...new Set(data.markets.map(m=>m.date))].sort();const ds=JSON.stringify([dates,m?.date]);if(ds!==datesSignature){el("t-date").innerHTML=options(dates,m?.date||dates[0]);datesSignature=ds;}
  const bs=JSON.stringify([data.markets.map(x=>[x.token,x.date,x.bin,x.condition]),m?.token]);
  if(bs!==binsSignature){binsSignature=bs;el("t-bins").innerHTML=data.markets.filter(x=>x.date===m?.date&&x.outcome==="YES").map(y=>{const pair=data.markets.filter(x=>x.condition===y.condition);return `<div class="t-bin ${m?.condition===y.condition?"selected":""}"><strong>${escape(y.bin)}</strong><div>${pair.map(x=>`<button data-token="${x.token}" class="${x.token===m?.token?"active":""}">${x.outcome}<small>${data.depth[x.token]?.valid?cents(data.depth[x.token].asks[0]?.[0]):"—"}</small></button>`).join("")}</div></div>`;}).join("");
  }
  document.querySelectorAll<HTMLElement>("[data-token]").forEach(button=>{const token=button.dataset.token!,small=button.querySelector("small");const text=data.depth[token]?.valid?cents(data.depth[token].asks[0]?.[0]):"—";if(small&&small.textContent!==text)small.textContent=text;});
  document.querySelectorAll<HTMLElement>("[data-outcome]").forEach(b=>b.classList.toggle("active",b.dataset.outcome===m?.outcome));
  if(changed){preview=null;initialPrice=true;(el("t-dialog") as HTMLDialogElement).close();chartToken=data.selectedToken;try{const saved=JSON.parse(localStorage.getItem(draftKey())||"null");if(saved){for(const id of draftFields){if(id==="t-post")input(id).checked=!!saved[id];else input(id).value=String(saved[id]);}initialPrice=false;}}catch{}}
  if(initialPrice&&book().valid){const price=Number(book().asks[0]?.[0]||book().bids[0]?.[0]||.5);input("t-limit").value=String(price*100);input("t-size").value=String(Math.max(1,Math.ceil((m?.minimum_order_size||1)*(price+(m?.fee_rate||0)*.25**(m?.fee_exponent||1))*100)/100));initialPrice=false;}
  input("t-limit").step=String((m?.tick_size||.01)*100);
  renderBook();renderPrice(first||changed||chartToken!==data.selectedToken);
  const signature=JSON.stringify(data.performance);
  if(first||signature!==pnlSignature){if((el("t-pnl") as HTMLDetailsElement).open)renderPnl(first);if((el("t-calendar") as HTMLDetailsElement).open)renderCalendar();pnlSignature=signature;}
  renderTable();estimate();
  el("t-funding").textContent=`Cash ${money(s.cash)} · Net funding ${money(s.net_funding||0)}`;
  if(pendingOrder)el("t-estimate").textContent="Submitted · waiting for Worker confirmation";
  el("t-notices").innerHTML=data.notices.map(n=>`<p class="${n.status==="rejected"?"t-error":""}">${escape(n.kind)} · ${escape(n.status)} ${escape(n.error||"")}</p>`).join("");
  el("t-strategy-status").textContent=`Strategy ${s.strategy_enabled?"ON":"OFF"}`;
  finishClose();
  height();
  if(first){const saved=localStorage.getItem(`terminal-selection-${data.account}`);if(saved!==data.selectedToken&&data.markets.some(m=>m.token===saved))selectToken(saved!);else send("ready");}
}
