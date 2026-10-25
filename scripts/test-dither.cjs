/* Focused runtime checks for the isolated Canvas dither specimen. */
const assert = require("node:assert/strict");
const fs = require("node:fs/promises");
const path = require("node:path");
const { parseArgs } = require("node:util");
const { values } = parseArgs({ options: {
  "playwright-path": { type: "string", default: "playwright" },
  browser: { type: "string", default: "edge" },
  url: { type: "string", default: "http://127.0.0.1:3300/dither.html" },
} });
for (const name of ["HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy"]) process.env[name] = "";
process.env.NO_PROXY = "localhost,127.0.0.1,::1";
const pw = require(values["playwright-path"]);
const results = { browser: values.browser, url: values.url, cases: [] };
const output = path.resolve("artifacts/design");
let browser;

async function run(name, test) {
  try { const detail = await test(); results.cases.push({ name, status: "passed", detail }); console.log(`PASS ${name}`); }
  catch (error) { results.cases.push({ name, status: "failed", error: error.message }); console.log(`FAIL ${name}: ${error.message}`); }
  finally { for (const ctx of browser.contexts()) await ctx.close(); }
}

async function load(options = {}) {
  const ctx = await browser.newContext({ viewport: { width: 1440, height: 1000 }, ...options });
  const page = await ctx.newPage();
  page.setDefaultTimeout(12000);
  return { ctx, page };
}

async function bitmap(page) {
  return page.locator("#dither-stage canvas").evaluate(canvas => {
    const pixels = canvas.getContext("2d").getImageData(0, 0, canvas.width, canvas.height).data;
    const colors = new Set(); let checksum = 2166136261;
    for (let i = 0; i < pixels.length; i += 4) {
      colors.add([...pixels.slice(i, i + 4)].join(","));
      checksum = Math.imul(checksum ^ pixels[i], 16777619) >>> 0;
    }
    return { checksum, colors: [...colors].sort() };
  });
}

(async () => {
  await fs.mkdir(output, { recursive: true });
  browser = values.browser === "edge" ? await pw.chromium.launch({ channel: "msedge", headless: true }) : await pw[values.browser].launch({ headless: true });
  results.version = browser.version();
  await run("Bayer pixels, controls, static output and responsive layout", async () => {
    const { ctx, page } = await load();
    const errors = [], failures = [], remote = [];
    page.on("pageerror", error => errors.push(error.message));
    page.on("requestfailed", request => failures.push(request.url()));
    page.on("request", request => { if (new URL(request.url()).origin !== new URL(values.url).origin) remote.push(request.url()); });
    await page.goto(values.url);
    await page.waitForFunction(() => document.querySelector("#dither-stage").dataset.renderer === "canvas");
    const initial = await bitmap(page);
    assert.deepEqual(initial.colors, ["255,255,255,255", "5,5,5,255"]);
    assert.equal(await page.locator("#dither-size").inputValue(), "1");
    await page.getByLabel("Размер точки", { exact: true }).selectOption("2");
    assert.notEqual((await bitmap(page)).checksum, initial.checksum);
    await page.getByRole("button", { name: "Сбросить настройки", exact: true }).click();
    assert.equal(await page.locator("#dither-size").inputValue(), "1");
    assert.equal((await bitmap(page)).checksum, initial.checksum);
    await page.locator("#dither-threshold").fill("0.8");
    assert.notEqual((await bitmap(page)).checksum, initial.checksum);
    await page.getByRole("button", { name: "Сбросить настройки", exact: true }).click();
    const count = await page.locator("#dither-stage").getAttribute("data-render-count");
    await page.waitForTimeout(250);
    assert.equal(await page.locator("#dither-stage").getAttribute("data-render-count"), count);
    for (const width of [320, 360, 768, 1440]) {
      await page.setViewportSize({ width, height: width < 500 ? 800 : 1000 });
      assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > document.documentElement.clientWidth), false);
      assert(await page.locator("#dither-stage img").isVisible());
      if ([360, 1440].includes(width)) await page.screenshot({ path: path.join(output, `${values.browser}-dither-${width}.png`), fullPage: true });
    }
    assert.deepEqual(errors, []); assert.deepEqual(failures, []); assert.deepEqual(remote, []);
    await ctx.close(); return { initial, idleRenderCount: count };
  });
  await run("no JavaScript keeps source images and hides controls", async () => {
    const { ctx, page } = await load({ javaScriptEnabled: false });
    await page.goto(values.url);
    assert(await page.locator("#dither-stage img").isVisible());
    assert.equal(await page.locator("#dither-controls").isVisible(), false);
    assert(await page.locator("#dither-fallback").isVisible());
    await ctx.close();
  });
  await run("Canvas unavailable keeps the image and explanation", async () => {
    const { ctx, page } = await load();
    await page.addInitScript(() => { HTMLCanvasElement.prototype.getContext = () => null; });
    await page.goto(values.url);
    await page.locator("#dither-fallback").filter({ hasText: "Эффект недоступен" }).waitFor();
    assert(await page.locator("#dither-stage img").isVisible());
    assert.equal(await page.locator("#dither-controls").isVisible(), false);
    await ctx.close();
  });
  await run("reduced motion at DPR 2 remains a static monochrome image", async () => {
    const { ctx, page } = await load({ reducedMotion: "reduce", deviceScaleFactor: 2 });
    await page.goto(values.url);
    await page.waitForFunction(() => document.querySelector("#dither-stage").dataset.renderer === "canvas");
    assert.deepEqual((await bitmap(page)).colors, ["255,255,255,255", "5,5,5,255"]);
    assert.equal(await page.evaluate(() => document.getAnimations().length), 0);
    await ctx.close();
  });
  await browser.close(); browser = null;
  await fs.writeFile(path.join(output, `qa-dither-${values.browser}.json`), JSON.stringify(results, null, 2) + "\n");
  if (results.cases.some(test => test.status === "failed")) process.exitCode = 1;
})().catch(async error => { console.error(error.message); if (browser) await browser.close(); process.exitCode = 1; });
