/* Isolated development browsers only. No connection to a user's browser profile. */
const assert = require("node:assert/strict");
const fs = require("node:fs/promises");
const path = require("node:path");
const { parseArgs } = require("node:util");
const { values } = parseArgs({ options: {
  "playwright-path": { type: "string", default: "playwright" },
  browser: { type: "string", default: "edge" },
  url: { type: "string", default: "http://127.0.0.1:3300/" },
  case: { type: "string" },
} });
// Only loopback is under test. Do not inherit a machine's external proxy route.
for (const name of ["HTTP_PROXY","HTTPS_PROXY","ALL_PROXY","http_proxy","https_proxy","all_proxy"]) process.env[name] = "";
process.env.NO_PROXY = "localhost,127.0.0.1,::1";
const pw = require(values["playwright-path"]);
const output = path.resolve("artifacts/design");
const results = { browser: values.browser, url: values.url, cases: [] };
let browser;

async function run(name, test) {
  if (values.case && !new RegExp(values.case).test(name)) return;
  try { const detail = await test(); const status = detail?.notRun ? "not run" : "passed"; results.cases.push({ name, status, detail }); console.log(`${status === "passed" ? "PASS" : "NOT RUN"} ${name}`); }
  catch (error) { results.cases.push({ name, status: "failed", error: error.message }); console.log(`FAIL ${name}: ${error.message}`); }
  finally { for (const ctx of browser.contexts()) await ctx.close(); }
}

async function context(options = {}) {
  const ctx = await browser.newContext({ viewport: { width: 1440, height: 1000 }, ...options });
  const page = await ctx.newPage();
  page.setDefaultTimeout(12000);
  return { ctx, page };
}

async function load(page) { await page.goto(values.url); await page.evaluate(() => document.fonts.ready); }
async function enter(page) {
  await page.getByRole("link", { name: "ENTER SYSTEM", exact: true }).click();
  await page.waitForFunction(() => !document.querySelector("#workspace").hidden && !document.querySelector(".enter-button").hasAttribute("aria-disabled"));
}
async function material(page) {
  await enter(page);
  await page.getByRole("button", { name: "Посмотреть Prism Glass", exact: true }).click();
  await page.waitForFunction(() => document.querySelector("#material-dialog").open);
}

(async () => {
  await fs.mkdir(output, { recursive: true });
  if (values.browser === "edge") browser = await pw.chromium.launch({ channel: "msedge", headless: true });
  else browser = await pw[values.browser].launch({ headless: true });
  results.version = browser.version();

  await run("fonts, assets, layout and stable loading", async () => {
    const { ctx, page } = await context();
    const errors = [], failed = [], remote = [];
    page.on("pageerror", error => errors.push(error.message));
    page.on("requestfailed", request => failed.push(request.url()));
    page.on("request", request => { if (!request.url().startsWith(values.url)) remote.push(request.url()); });
    await page.addInitScript(() => {
      window.designCLS = 0;
      if (PerformanceObserver.supportedEntryTypes.includes("layout-shift")) {
        new PerformanceObserver(list => list.getEntries().forEach(entry => { if (!entry.hadRecentInput) window.designCLS += entry.value; })).observe({ type: "layout-shift", buffered: true });
      }
    });
    await load(page);
    const fonts = await page.evaluate(() => [...document.fonts].map(font => ({ family: font.family, status: font.status })));
    assert(fonts.some(font => font.family.includes("JetBrains Mono") && font.status === "loaded"));
    const layout = [];
    for (const width of [320, 360, 768, 1440]) {
      await page.setViewportSize({ width, height: width < 500 ? 800 : 1000 });
      const data = await page.evaluate(() => ({ width: innerWidth, overflow: document.documentElement.scrollWidth > document.documentElement.clientWidth,
        minimumText: Math.min(...[...document.querySelectorAll("a,button,p,label,input")].filter(el => el.getClientRects().length && getComputedStyle(el).clipPath === "none").map(el => parseFloat(getComputedStyle(el).fontSize))) }));
      assert.equal(data.overflow, false); assert(data.minimumText >= 16);
      layout.push(data);
      if ([360,1440].includes(width)) await page.screenshot({ path: path.join(output, `${values.browser}-scene-${width}.jpg`) });
    }
    assert.deepEqual(errors, []); assert.deepEqual(failed, []); assert.deepEqual(remote, []);
    const cls = await page.evaluate(() => window.designCLS);
    await ctx.close();
    return { fonts, layout, cls };
  });

  await run("entry, keyboard focus, history and dialog", async () => {
    const { ctx, page } = await context(); await load(page);
    await page.getByRole("link", { name: "ENTER SYSTEM", exact: true }).focus();
    const start = Date.now(); await page.keyboard.press("Enter");
    await page.waitForFunction(() => !document.querySelector(".enter-button").hasAttribute("aria-disabled"));
    assert.equal(await page.evaluate(() => document.activeElement.id), "workspace-title");
    const transitionMs = Date.now()-start;
    await page.goBack(); await page.locator("#entry").waitFor({ state: "visible" });
    assert.equal(await page.evaluate(() => document.activeElement.id), "entry-title");
    await material(page);
    await page.getByRole("button", { name: "Закрыть", exact: true }).focus();
    await page.keyboard.press("Shift+Tab");
    assert.equal(await page.evaluate(() => document.activeElement.id), "refraction-toggle");
    await page.keyboard.press("Tab");
    assert.equal(await page.getByRole("button", { name: "Закрыть", exact: true }).evaluate(el => el === document.activeElement), true);
    const mode = await page.locator("#prism-stage").getAttribute("data-renderer");
    const withEffect = await page.locator("#prism-stage").screenshot();
    await page.getByLabel("Оптическое преломление", { exact: true }).uncheck();
    assert.equal(await page.locator("#prism-stage").getAttribute("data-renderer"), "fallback");
    const withoutEffect = await page.locator("#prism-stage").screenshot();
    const visualDifference = !withEffect.equals(withoutEffect);
    if (mode === "webgl") assert(visualDifference, "Optical view must visibly differ from opaque fallback");
    await page.getByLabel("Оптическое преломление", { exact: true }).check();
    await page.screenshot({ path: path.join(output, `${values.browser}-prism.jpg`) });
    await page.keyboard.press("Escape");
    await page.waitForFunction(() => document.activeElement.id === "open-material");
    await ctx.close(); return { transitionMs, mode, visualDifference };
  });

  await run("reduced motion", async () => {
    const { ctx, page } = await context({ reducedMotion: "reduce" }); await load(page);
    assert.equal(await page.evaluate(() => matchMedia("(prefers-reduced-motion: reduce)").matches), true);
    assert.equal(await page.evaluate(() => getComputedStyle(document.documentElement).getPropertyValue("--motion-enter").trim()), "0ms");
    const start = Date.now(); await enter(page); const elapsedMs = Date.now()-start;
    assert.equal(await page.evaluate(() => document.activeElement.id), "workspace-title");
    await ctx.close(); return { elapsedMs };
  });

  await run("no JavaScript", async () => {
    const { ctx, page } = await context({ javaScriptEnabled: false }); await page.goto(values.url);
    assert(await page.locator("#entry").isVisible()); assert(await page.locator("#workspace").isVisible());
    await page.getByRole("link", { name: "ENTER SYSTEM", exact: true }).click();
    assert(page.url().endsWith("#workspace"));
    assert.equal(await page.locator("#open-material").isVisible(), false);
    await ctx.close();
  });

  await run("font download failure", async () => {
    const { ctx, page } = await context();
    await ctx.route("**/*.woff2", route => route.abort()); await page.goto(values.url); await enter(page);
    assert(await page.locator("#workspace-title").isVisible());
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > document.documentElement.clientWidth), false);
    await ctx.close();
  });

  await run("WebGL unavailable and mobile dialog", async () => {
    const { ctx, page } = await context({ viewport: { width: 360, height: 800 } });
    await page.addInitScript(() => {
      const original = HTMLCanvasElement.prototype.getContext;
      HTMLCanvasElement.prototype.getContext = function(type, ...args) { return type.includes("webgl") ? null : original.call(this, type, ...args); };
    });
    await load(page); await material(page);
    assert.equal(await page.locator("#prism-stage").getAttribute("data-renderer"), "fallback");
    assert(await page.locator("#material-fallback").isVisible());
    assert.equal(await page.locator("#material-dialog").evaluate(el => el.scrollWidth > el.clientWidth), false);
    await page.getByRole("button", { name: "Закрыть", exact: true }).press("Escape");
    await ctx.close();
  });

  await run("forced colors", async () => {
    const { ctx, page } = await context({ forcedColors: "active" }); await load(page);
    const supported = await page.evaluate(() => matchMedia("(forced-colors: active)").matches);
    if (supported) assert(await page.locator("#entry .forced-wordmark").isVisible());
    await material(page);
    if (supported) assert.equal(await page.locator("#prism-stage").getAttribute("data-renderer"), "fallback");
    await ctx.close(); return supported ? { emulated: true } : { notRun: "This engine did not emulate forced-colors" };
  });

  await run("CSS zoom 200 percent", async () => {
    const { ctx, page } = await context(); await load(page);
    await page.evaluate(() => { document.documentElement.style.zoom = "2"; });
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > document.documentElement.clientWidth), false);
    await material(page);
    assert.equal(await page.locator("#material-dialog").evaluate(el => el.scrollWidth > el.clientWidth), false);
    await page.getByRole("button", { name: "Закрыть", exact: true }).press("Escape");
    await ctx.close(); return "CSS zoom; not an OS/browser toolbar zoom or a physical-device test";
  });

  if (values.browser === "edge") {
    await run("reduced transparency and throttled CPU", async () => {
      const { ctx, page } = await context(); const cdp = await ctx.newCDPSession(page);
      await cdp.send("Emulation.setEmulatedMedia", { features: [{ name: "prefers-reduced-transparency", value: "reduce" }] });
      await cdp.send("Emulation.setCPUThrottlingRate", { rate: 6 });
      await load(page); await material(page);
      assert.equal(await page.evaluate(() => matchMedia("(prefers-reduced-transparency: reduce)").matches), true);
      assert.equal(await page.locator("#prism-stage").getAttribute("data-renderer"), "fallback");
      await page.getByRole("button", { name: "Закрыть", exact: true }).press("Escape");
      await ctx.close(); return "CDP media emulation and 6x CPU throttle; not a physical weak GPU";
    });
    await run("WebGL context loss", async () => {
      const { ctx, page } = await context(); await load(page); await material(page);
      const mode = await page.locator("#prism-stage").getAttribute("data-renderer");
      if (mode === "webgl") {
        const available = await page.evaluate(() => {
          const extension = document.querySelector("canvas").getContext("webgl").getExtension("WEBGL_lose_context");
          if (!extension) return false; extension.loseContext(); return true;
        });
        if (available) await page.waitForFunction(() => document.querySelector("#prism-stage").dataset.renderer === "fallback");
      }
      await ctx.close(); return mode === "webgl" ? { initialMode: mode } : { notRun: "WebGL unavailable in this test browser" };
    });
  }

  await browser.close(); browser = null;
  await fs.writeFile(path.join(output, `qa-${values.browser}.json`), JSON.stringify(results, null, 2)+"\n");
  if (results.cases.some(test => test.status === "failed")) process.exitCode = 1;
})().catch(async error => {
  console.error(error.message);
  if (browser) await browser.close();
  process.exitCode = 1;
});
