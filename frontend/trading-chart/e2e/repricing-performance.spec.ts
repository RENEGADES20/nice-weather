import {test, expect} from '@playwright/test';

test('repricing performance baseline and acceptance', async ({page}, info) => {
  test.setTimeout(180_000);
  const samples: Record<string, number[]> = {};
  const record = (key: string, value: number) => (samples[key] ||= []).push(value);
  await page.addInitScript(() => {
    const measures = {longTasks: [] as number[], messages: 0};
    Object.assign(window, {perfAudit: measures});
    new PerformanceObserver(list => list.getEntries().forEach(e => measures.longTasks.push(e.duration)))
      .observe({entryTypes: ['longtask']});
    window.addEventListener('message', e => {if(e.data?.type === 'streamlit:setComponentValue') measures.messages++;});
  });
  for (let run=0; run<3; run++) {
    const start = Date.now();
    await page.goto('/');
    await page.getByRole('tab', {name:'Repricing', exact:true}).click();
    const frame = page.frameLocator('iframe[title="nice_weather.trading_chart.nice_weather_trading_chart"]').first();
    await expect(frame.locator('#difference-select')).toBeVisible({timeout:40_000});
    record('firstLoadMs', Date.now()-start);
    const day = page.getByRole('combobox').first();
    const changeStart = Date.now();
    await day.click();
    await page.getByRole('option').filter({hasText:'2026-08-'}).first().click();
    await expect(frame.locator('#app')).toHaveAttribute('data-comparison-mode','as-of',{timeout:40_000});
    record('dayChangeMs',Date.now()-changeStart);
    const oldBin = await frame.locator('#app').getAttribute('data-selected-bin-id');
    const binStart = Date.now();
    await page.getByRole('radio').filter({visible:true}).evaluateAll(radios => {
      const target=radios.find(r => !(r as HTMLInputElement).checked) as HTMLInputElement;
      target.click();
    });
    if (oldBin) await expect(frame.locator('#app')).not.toHaveAttribute('data-selected-bin-id',oldBin,{timeout:40_000});
    record('binChangeMs',Date.now()-binStart);
    for(const value of ['metar-minus-forecast','price-minus-metar','price-minus-forecast','price-minus-weather-gov']) {
      const elapsed = await frame.locator('#difference-select').evaluate((select, value) => {
        const start = performance.now();
        (select as HTMLSelectElement).value=value;
        select.dispatchEvent(new Event('change',{bubbles:true}));
        return performance.now()-start;
      },value);
      record('differenceMs',elapsed);
    }
    await page.waitForTimeout(4500);
    const metrics = await frame.locator('#app').evaluate(root => ({...((root as HTMLElement).dataset)}));
    for(const key of ['queryMs','sqlMs','prepareMs','transportMs','renderMs','payloadBytes'])
      if(metrics[key]) record(key,Number(metrics[key]));
  }
  const frame = page.frameLocator('iframe[title="nice_weather.trading_chart.nice_weather_trading_chart"]').first();
  const session = await page.context().newCDPSession(page);
  await session.send('HeapProfiler.collectGarbage');
  const heapBefore = (await session.send('Runtime.getHeapUsage')).usedSize;
  const canvases = await frame.locator('canvas').count();
  for (let batch=0; batch<3; batch++) {
    for (let index=0; index<20; index++) {
      await frame.locator('#difference-select').selectOption(index%2 ? 'price-minus-metar' : 'metar-minus-forecast');
    }
    await session.send('HeapProfiler.collectGarbage');
    record('heapAfterSwitches', (await session.send('Runtime.getHeapUsage')).usedSize);
  }
  expect(await frame.locator('canvas').count()).toBe(canvases);
  expect(samples.heapAfterSwitches.at(-1)!-heapBefore).toBeLessThan(5_000_000);
  const selectedBin = await frame.locator('#app').getAttribute('data-selected-bin-id');
  await page.getByRole('tab',{name:'Overview',exact:true}).click();
  await expect(page.locator('iframe[title="nice_weather.trading_chart.nice_weather_trading_chart"]')).toHaveCount(0);
  await page.getByRole('tab',{name:'Repricing',exact:true}).click();
  await expect(frame.locator('#app')).toHaveAttribute('data-selected-bin-id',selectedBin!);
  const browser = await page.evaluate(() => (window as unknown as {perfAudit:unknown}).perfAudit);
  const summary=Object.fromEntries(Object.entries(samples).map(([key,values])=>[key,{median:[...values].sort((a,b)=>a-b)[Math.floor(values.length/2)],max:Math.max(...values),samples:values}]));
  console.log('REPRICING_PERF',info.project.name,JSON.stringify({summary,browser}));
  await info.attach('performance.json',{body:JSON.stringify({summary,browser},null,2),contentType:'application/json'});
});
