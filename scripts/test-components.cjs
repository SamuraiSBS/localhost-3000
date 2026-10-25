/* Independent design specimens, never a product journey. */
const assert = require('node:assert/strict');
const fs = require('node:fs/promises');
const path = require('node:path');
const { parseArgs } = require('node:util');
const { values } = parseArgs({ options: {
  'playwright-path': { type: 'string', default: 'playwright' },
  browser: { type: 'string', default: 'edge' },
} });
for (const name of ['HTTP_PROXY','HTTPS_PROXY','ALL_PROXY','http_proxy','https_proxy','all_proxy']) process.env[name] = '';
process.env.NO_PROXY = 'localhost,127.0.0.1,::1';
const pw = require(values['playwright-path']);
const url = 'http://127.0.0.1:3300/preview.html';
const out = path.resolve('artifacts/design');
const results = { browser: values.browser, cases: [] };
let browser;
async function run(name, fn) {
  try { await fn(); results.cases.push({name, status:'passed'}); console.log('PASS '+name); }
  catch(e) { results.cases.push({name, status:'failed', error:e.message}); console.log('FAIL '+name+': '+e.message); }
  finally { for (const ctx of browser.contexts()) await ctx.close(); }
}
async function context(options={}) {
  const ctx = await browser.newContext({viewport:{width:1440,height:1000},...options});
  const page = await ctx.newPage(); page.setDefaultTimeout(12000); page.setDefaultNavigationTimeout(30000);
  await page.goto(url); await page.evaluate(()=>document.fonts.ready);
  return {ctx,page};
}
(async()=>{
  await fs.mkdir(out,{recursive:true});
  browser = values.browser==='edge' ? await pw.chromium.launch({channel:'msedge',headless:true}) : await pw[values.browser].launch({headless:true});
  await run('native selection, tooltip keyboard, icon state, stable busy button and error recovery',async()=>{
    const {page}=await context();
    const checkbox=page.locator('#sample-checkbox'); await checkbox.focus(); await page.keyboard.press('Space'); assert(await checkbox.isChecked());
    await page.keyboard.press('Space'); assert(!await checkbox.isChecked());
    assert(await page.getByLabel('Недоступный чекбокс',{exact:true}).isDisabled());
    const trigger=page.locator('#tooltip-trigger'), tooltip=page.locator('#sample-tooltip');
    await trigger.focus(); assert(await tooltip.isVisible());
    await page.keyboard.press('Escape'); assert(!await tooltip.isVisible());
    await trigger.hover(); assert(await tooltip.isVisible());
    await page.mouse.move(0,0); await page.keyboard.press('Tab'); assert(!await tooltip.isVisible());
    await page.locator('#icon-toggle').click(); assert.equal(await page.locator('#icon-toggle').getAttribute('aria-pressed'),'true');
    assert(await page.locator('#icon-toggle .ds-icon--info-solid').count());
    const busy=page.locator('#busy-button'), before=await busy.boundingBox();
    await page.locator('#busy-toggle').check(); const during=await busy.boundingBox();
    assert.equal(before.width,during.width); assert.equal(before.height,during.height);
    assert(await busy.isDisabled()); assert.equal(await busy.getAttribute('aria-busy'),'true'); assert.equal(await busy.innerText(),'Применение…');
    await page.locator('#busy-toggle').uncheck(); assert(await busy.isEnabled());
    await page.locator('#apply-button').click(); assert.equal(await page.locator('#specimen-name').getAttribute('aria-invalid'),'true');
    assert(await page.locator('#name-error .ds-icon').isVisible());
    await page.locator('#specimen-name').fill('Ёж и цифровая архитектура');
    await page.locator('#apply-button').click(); assert.equal(await page.locator('#sample-text').innerText(),'Ёж и цифровая архитектура');
    assert(!await page.locator('#name-error').isVisible());
    await page.locator('#open-dialog').click(); assert.equal(await page.locator('#sample-dialog').evaluate(el=>getComputedStyle(el).animationDuration),'0.15s');
    await page.keyboard.press('Escape'); assert(await page.locator('#open-dialog').evaluate(el=>document.activeElement===el));
  });
  await run('320–1440 layout, centered form, mobile actions and 200 percent text',async()=>{
    const {page}=await context();
    for(const width of [320,360,768,1440]) {
      await page.setViewportSize({width,height:1000});
      assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false);
      const form=await page.locator('#specimen-form').boundingBox(); assert(Math.abs(form.x+form.width/2-width/2)<2);
      const h1=await page.locator('h1').evaluate(el=>getComputedStyle(el).fontSize); assert.equal(h1,'32px');
      if(width===360||width===1440) {
        await page.locator('#states').screenshot({path:path.join(out,`${values.browser}-components-${width}.png`)});
        await page.locator('#elements').screenshot({path:path.join(out,`${values.browser}-form-${width}.png`)});
      }
    }
    await page.addStyleTag({content:'html { font-size: 200%; }'});
    await page.setViewportSize({width:360,height:1000}); assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false);
  });
  await run('no JS, reduced motion and opaque tooltip',async()=>{
    const {page}=await context({javaScriptEnabled:false});
    assert(await page.locator('#sample-checkbox').isVisible()); assert(!await page.locator('#interactive-samples').isVisible());
    assert(!await page.locator('#loading-sample').isVisible());
    const reduced=await context({reducedMotion:'reduce'});
    await reduced.page.locator('#open-dialog').click();
    assert.equal(await reduced.page.locator('#sample-dialog').evaluate(el=>getComputedStyle(el).animationDuration),'0s');
    await reduced.page.keyboard.press('Escape'); await reduced.page.locator('#tooltip-trigger').focus();
    assert.equal(await reduced.page.locator('#sample-tooltip').evaluate(el=>getComputedStyle(el).backgroundColor),'rgb(20, 20, 20)');
  });
  await browser.close(); browser=null;
  await fs.writeFile(path.join(out,`qa-components-${values.browser}.json`),JSON.stringify(results,null,2)+'\n');
  if(results.cases.some(c=>c.status==='failed'))process.exitCode=1;
})().catch(async e=>{console.error(e.message);if(browser)await browser.close();process.exitCode=1;});
