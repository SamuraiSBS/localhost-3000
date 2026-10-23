/* Isolated Aceternity Liquid Glass specimen; no user browser profile. */
const assert = require("node:assert/strict");
const fs = require("node:fs/promises");
const path = require("node:path");
const { parseArgs } = require("node:util");
const { values } = parseArgs({ options: {
  "playwright-path": { type: "string", default: "playwright" },
  browser: { type: "string", default: "edge" },
  url: { type: "string", default: "http://127.0.0.1:3300/liquid-glass.html" },
} });
for (const name of ["HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy"]) process.env[name] = "";
process.env.NO_PROXY = "localhost,127.0.0.1,::1";
const pw = require(values["playwright-path"]);
const preset = { shape: "circle", scale: 50, aberration: 20, bevelDepth: 60, frost: 4.8, tint: 0.05 };
const results = { browser: values.browser, url: values.url, cases: [] };
const output = path.resolve("artifacts/design");
let browser;
async function run(name, test) {
  try { const detail = await test(); results.cases.push({ name, status: "passed", detail }); console.log(`PASS ${name}`); }
  catch (error) { results.cases.push({ name, status: "failed", error: error.message }); console.log(`FAIL ${name}: ${error.message}`); }
  finally { for (const ctx of browser.contexts()) await ctx.close(); }
}
async function context(options = {}) {
  const ctx = await browser.newContext({ viewport: { width: 1440, height: 1100 }, ...options });
  const page = await ctx.newPage(); page.setDefaultTimeout(12000); page.setDefaultNavigationTimeout(30000); return { ctx, page };
}
async function ready(page, scale = 50, aberration = 20) {
  await page.waitForFunction(({ scale, aberration }) => {
    const el = document.querySelector("#liquid-lens");
    const applied = JSON.parse(el.dataset.applied || "{}");
    return el.dataset.ready === "true" && applied.scale === scale && applied.aberration === aberration;
  }, { scale, aberration });
}
(async () => {
  await fs.mkdir(output, { recursive: true });
  browser = values.browser === "edge" ? await pw.chromium.launch({ channel: "msedge", headless: true }) : await pw[values.browser].launch({ headless: true });
  results.version = browser.version();
  await run("Hero placement, preset propagation, readable controls and painted refraction", async () => {
    const { page } = await context();
    await page.goto(values.url);
    const waitHero = () => page.waitForFunction(() => [...document.querySelectorAll('.hero-glass')].every(el => el.dataset.ready === 'true' && JSON.parse(el.dataset.applied || '{}').scale === Number(document.querySelector('#glass-scale').value)));
    await ready(page); await waitHero();
    assert.equal(await page.locator('.hero-glass').count(), 3);
    const bar = page.locator('.hero-glass--bar');
    const original = await bar.screenshot();
    await page.locator('#glass-scale').fill('0');
    await ready(page, 0, 20); await waitHero();
    if (values.browser === 'edge') assert.notDeepEqual(await bar.screenshot(), original, 'Hero refraction did not change painted pixels');
    await page.getByRole('button', { name: 'Вернуть выбранные настройки', exact: true }).click();
    await ready(page); await waitHero();
    for (const width of [320, 360, 768, 1440]) {
      await page.setViewportSize({ width, height: 1000 }); await waitHero();
      assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false);
      const controls = await page.locator('.glass-hero__actions .ds-button').evaluateAll(els => els.map(el => {
        const s = getComputedStyle(el); return { height: el.getBoundingClientRect().height, radius: s.borderRadius, duration: s.transitionDuration, font: parseFloat(s.fontSize) };
      }));
      assert(controls.every(c => c.height >= 48 && c.radius === '2px' && /^0s(, 0s)*$/.test(c.duration) && c.font >= 16));
      if ([360, 1440].includes(width)) await page.locator('.glass-hero').screenshot({ path: path.join(output, `${values.browser}-glass-hero-${width}.png`) });
    }
    await page.locator('.hero-glass--action').focus();
    await page.keyboard.press('Enter');
    assert(new URL(page.url()).hash === '#glass-stage');
    return { surfaces: 3, widths: [320, 360, 768, 1440], keyboardAnchor: true };
  });
  await run("exact preset, reset, real paint and responsive layout", async () => {
    const { ctx, page } = await context(); const errors = [], failures = [], remote = [];
    page.on("pageerror", error => errors.push(error.message));
    page.on("requestfailed", request => failures.push(request.url()));
    page.on("request", request => { const url = new URL(request.url()); if (["http:", "https:"].includes(url.protocol) && url.origin !== new URL(values.url).origin) remote.push(request.url()); });
    await page.goto(values.url); await ready(page);
    const lens = page.locator("#liquid-lens");
    assert.deepEqual(JSON.parse(await lens.getAttribute("data-applied")), preset);
    assert.equal(await page.locator("#glass-shape").inputValue(), "circle");
    const mode = await lens.getAttribute("data-renderer");
    assert.equal(mode, values.browser === "edge" ? "svg" : "frosted");
    const bounds = await lens.boundingBox(); assert.equal(Math.round(bounds.width), Math.round(bounds.height));
    if (mode === "svg") {
      const exactPaint = await lens.screenshot();
      await page.locator("#glass-scale").fill("0"); await page.locator("#glass-aberration").fill("0"); await ready(page, 0, 0);
      assert.notDeepEqual(await lens.screenshot(), exactPaint, "Refraction did not change actual painted pixels");
    } else { await page.locator("#glass-scale").fill("0"); await ready(page, 0, 20); }
    await page.getByRole("button", { name: "Вернуть выбранные настройки", exact: true }).click(); await ready(page);
    assert.deepEqual(JSON.parse(await lens.getAttribute("data-applied")), preset);
    await page.getByLabel("Форма", { exact: true }).selectOption("pill");
    await page.getByRole("button", { name: "Вернуть выбранные настройки", exact: true }).click(); await ready(page);
    for (const width of [320, 360, 768, 1440]) {
      await page.setViewportSize({ width, height: width < 500 ? 900 : 1100 }); await ready(page);
      assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > document.documentElement.clientWidth), false);
      if ([360, 1440].includes(width)) await page.screenshot({ path: path.join(output, `${values.browser}-liquid-glass-${width}.png`), fullPage: true });
    }
    assert.deepEqual(errors, []); assert.deepEqual(failures, []); assert.deepEqual(remote, []);
    await ctx.close(); return { mode, preset, paintedRefractionChecked: mode === "svg" };
  });
  await run("no JavaScript has image, frosted fallback and no dead controls", async () => {
    const { ctx, page } = await context({ javaScriptEnabled: false }); await page.goto(values.url);
    assert(await page.locator(".glass-stage > img").isVisible());
    assert.equal(await page.locator("#glass-controls").isVisible(), false);
    assert.equal(await page.locator("#glass-reset").isVisible(), false);
    assert(await page.locator("#glass-fallback").isVisible()); await ctx.close();
  });
  await run("reduced motion at DPR 2", async () => {
    const { ctx, page } = await context({ reducedMotion: "reduce", deviceScaleFactor: 2 });
    await page.goto(values.url); await ready(page);
    assert.equal(await page.evaluate(() => document.getAnimations().length), 0); await ctx.close();
  });
  if (values.browser === "edge") {
    await run("Canvas map failure falls back to frosted glass", async () => {
      const { ctx, page } = await context();
      await page.addInitScript(() => { HTMLCanvasElement.prototype.getContext = () => null; });
      await page.goto(values.url); await ready(page);
      assert.equal(await page.locator("#liquid-lens").getAttribute("data-renderer"), "frosted"); await ctx.close();
    });
    await run("reduced transparency and forced colors are opaque", async () => {
      const { ctx, page } = await context(); const cdp = await ctx.newCDPSession(page);
      await cdp.send("Emulation.setEmulatedMedia", { features: [{ name: "prefers-reduced-transparency", value: "reduce" }] });
      await page.goto(values.url); await ready(page);
      assert.equal(await page.locator("#liquid-lens").getAttribute("data-renderer"), "opaque");
      assert.equal(await page.locator("#liquid-lens").evaluate(el => getComputedStyle(el).backdropFilter), "none");
      await cdp.send("Emulation.setEmulatedMedia", { features: [{ name: "forced-colors", value: "active" }] });
      await ready(page);
      assert.equal(await page.locator("#liquid-lens").getAttribute("data-renderer"), "opaque"); await ctx.close();
    });
  }
  await browser.close(); browser = null;
  await fs.writeFile(path.join(output, `qa-liquid-glass-${values.browser}.json`), JSON.stringify(results, null, 2) + "\n");
  if (results.cases.some(test => test.status === "failed")) process.exitCode = 1;
})().catch(async error => { console.error(error.message); if (browser) await browser.close(); process.exitCode = 1; });
