// Record a demo of the full pipeline, driven like a person would use it.
//
//   node scripts/record_demo.mjs <out_dir> [--dry]
//
// Capture -> Process -> Segment (exclude BX-06) -> Reconstruct (Regenerate:
// a real Tripo batch) -> SimReady -> Stage (assemble + headless physics).
// --dry uses the mesh cache instead of Tripo, to rehearse without spending.
//
// Records in Edge (it decodes the phone's HEVC video) at 1920x1080, with a
// visible cursor so viewers can follow the clicks.
import { chromium } from "playwright";
import { mkdirSync, renameSync } from "node:fs";
import { join } from "node:path";

const out = process.argv[2] ?? "recordings";
const DRY = process.argv.includes("--dry");
const STAGE_ONLY = process.argv.includes("--stage-only");   // just the finale: assemble + physics test
const VIDEO = "20261105_121402.mp4";
const EXCLUDE = "06";           // the under-desk box whose outline swallowed a dark object
const EXPECT_BOXES = 4;
const W = 1920, H = 1080;
mkdirSync(out, { recursive: true });

const t0 = Date.now();
const log = (...a) => console.log(`[${((Date.now() - t0) / 1000).toFixed(0).padStart(4)}s]`, ...a);

const browser = await chromium.launch({ channel: "msedge", args: ["--enable-gpu", "--ignore-gpu-blocklist"] });
const ctx = await browser.newContext({ viewport: { width: W, height: H }, recordVideo: { dir: out, size: { width: W, height: H } } });
// A visible cursor: headless recordings don't draw the real one.
await ctx.addInitScript(() => {
  addEventListener("DOMContentLoaded", () => {
    const c = document.createElement("div");
    c.id = "demo-cursor";
    c.style.cssText = "position:fixed;left:0;top:0;width:22px;height:22px;margin:-11px 0 0 -11px;border-radius:50%;" +
      "background:rgba(255,255,255,.85);border:2px solid #f05a28;box-shadow:0 0 0 4px rgba(240,90,40,.25);" +
      "pointer-events:none;z-index:2147483647;transition:transform .12s ease;transform:translate(960px,540px)";
    document.body.appendChild(c);
    let x = 960, y = 540;
    addEventListener("mousemove", (e) => { x = e.clientX; y = e.clientY; c.style.transform = `translate(${x}px,${y}px)`; }, true);
    addEventListener("mousedown", () => { c.style.transform = `translate(${x}px,${y}px) scale(.7)`; }, true);
    addEventListener("mouseup", () => { c.style.transform = `translate(${x}px,${y}px) scale(1)`; }, true);
  });
});
const page = await ctx.newPage();
page.on("pageerror", (e) => log("PAGE ERROR:", e.message));

let mx = W / 2, my = H / 2;
async function glide(x, y, ms = 650) {
  const steps = Math.max(8, Math.round(ms / 16));
  for (let i = 1; i <= steps; i++) {
    const k = i / steps, e = k < 0.5 ? 2 * k * k : 1 - (-2 * k + 2) ** 2 / 2;  // ease in-out
    await page.mouse.move(mx + (x - mx) * e, my + (y - my) * e);
    await page.waitForTimeout(16);
  }
  mx = x; my = y;
}
async function click(locator, { ms = 650, pause = 350 } = {}) {
  await locator.scrollIntoViewIfNeeded();
  const b = await locator.boundingBox();
  await glide(b.x + b.width / 2, b.y + b.height / 2, ms);
  await page.waitForTimeout(pause);
  await page.mouse.down(); await page.waitForTimeout(90); await page.mouse.up();
}
async function orbit(locator, dx = 160) {
  // Never drag an empty viewer: wait until its model has actually loaded.
  await locator.evaluate((el) => el.loaded || new Promise((r) => el.addEventListener("load", r, { once: true })));
  await page.waitForTimeout(600);
  const b = await locator.boundingBox();
  const sx = b.x + b.width * 0.38, sy = b.y + b.height * 0.42;   // off-centre (pan-target dot sits dead centre)
  await glide(sx, sy, 500);
  await page.mouse.down();
  for (let i = 1; i <= 40; i++) { await page.mouse.move(sx + (dx * i) / 40, sy + (14 * i) / 40); await page.waitForTimeout(20); }
  await page.mouse.up();
  mx = sx + dx; my = sy + 14;
}
const job = async () => (await page.request.get("http://127.0.0.1:8000/api/job")).json();
async function waitStage(stage, label, limitS = 1800) {
  const end = Date.now() + limitS * 1000;
  let seenWorking = false;
  while (Date.now() < end) {
    const s = (await job()).stages[stage].status;
    if (s === "working") seenWorking = true;
    if (seenWorking && ["done", "attention", "failed"].includes(s)) { log(`${label}: ${s}`); return s; }
    await page.waitForTimeout(1000);
  }
  throw new Error(`timed out waiting for ${label}`);
}
const tab = (re) => page.getByRole("button", { name: re });

try {
  if (STAGE_ONLY) {
    await page.goto("http://localhost:5173/", { waitUntil: "networkidle" });
    await page.waitForTimeout(1500);
    await click(tab(/05 Stage/i));
    await page.waitForTimeout(2500);
    await click(page.getByRole("button", { name: /assemble stage/i }));
    await waitStage("stage", "assemble", 300);
    await page.waitForTimeout(3000);
    await orbit(page.locator(".stage-view model-viewer"), 220);
    await page.waitForTimeout(1500);
    await click(page.getByRole("button", { name: /Test physics/ }));
    const s = await waitStage("stage", "physics test", 900);
    await page.waitForTimeout(2000);
    await click(page.getByRole("button", { name: /^Checks$/ }).last());
    await page.waitForTimeout(5000);
    if (s !== "done") throw new Error(`physics test ended '${s}'`);
    log("done");
    throw "__finished__";
  }
  // ---------- 01 Capture ----------
  await page.goto("http://localhost:5173/", { waitUntil: "networkidle" });
  await page.waitForTimeout(1500);
  await click(tab(/01 Capture/i));
  await page.waitForTimeout(1200);
  await click(page.getByRole("button", { name: VIDEO }));
  await page.waitForTimeout(1200);
  const video = page.locator(".video-frame video");
  if (await video.count()) {
    await video.evaluate((v) => { v.currentTime = 0; v.play(); });
    log("preview playing");
    await page.waitForTimeout(7500);                      // let the whole 7 s walkthrough play
  }
  await click(page.getByRole("button", { name: /^Process/ }));
  log("process");
  await waitStage("segment", "segment", 300);
  await page.waitForTimeout(2500);

  // ---------- 02 Segment: the artist excludes a bad detection ----------
  const bad = page.locator(".strip-item").filter({ hasText: EXCLUDE });
  await glide(W * 0.45, H * 0.55, 900);
  await page.waitForTimeout(800);
  await click(bad, { ms: 900, pause: 700 });
  log(`excluded BX-${EXCLUDE}`);
  await page.waitForTimeout(2500);

  // ---------- 03 Reconstruct ----------
  await click(page.getByRole("button", { name: /Next · Reconstruct/i }));
  await page.waitForTimeout(3000);
  const plan = await (await page.request.get("http://127.0.0.1:8000/api/reconstruct/plan")).json();
  log("plan", JSON.stringify({ boxes: plan.boxes, fresh: plan.fresh_credits, balance: plan.balance }));
  if (plan.boxes !== EXPECT_BOXES) throw new Error(`expected ${EXPECT_BOXES} kept boxes, got ${plan.boxes}: not spending credits`);
  if (DRY) {
    await click(page.getByRole("button", { name: /Rebuild from cache|Generate/ }).last());
  } else {
    if (plan.fresh_credits !== EXPECT_BOXES * 30) throw new Error(`unexpected cost ${plan.fresh_credits}: stopping`);
    await click(page.getByRole("button", { name: /Regenerate/ }), { pause: 900 });
    log(`regenerate: ${plan.fresh_credits} credits`);
  }
  await waitStage("reconstruct", "reconstruct", 1500);
  await page.waitForTimeout(4000);                        // raw meshes load
  await orbit(page.locator("section:visible model-viewer").first(), 180);
  await page.waitForTimeout(1500);
  await click(page.getByRole("button", { name: /Inspect/ }).first());
  await page.waitForTimeout(3500);
  await orbit(page.locator(".inspector-view model-viewer"), 240);
  await page.waitForTimeout(2000);
  await page.keyboard.press("Escape");
  await page.waitForTimeout(1200);

  // ---------- 04 SimReady ----------
  await click(page.getByRole("button", { name: /Next · SimReady/i }));
  await page.waitForTimeout(2000);
  await click(page.getByRole("button", { name: /SimReady on|Re-run SimReady/ }));
  log("simready");
  await waitStage("simready", "simready", 1500);
  await page.waitForTimeout(3500);
  await orbit(page.locator("section:visible model-viewer").first(), 150);
  await page.waitForTimeout(1500);
  await click(page.getByRole("button", { name: /Compare/ }).first());
  await page.waitForTimeout(4000);
  await page.locator(".inspector").evaluate((el) => el.scrollTo({ top: el.scrollHeight * 0.45, behavior: "smooth" }));
  await page.waitForTimeout(5000);
  await page.keyboard.press("Escape");
  await page.waitForTimeout(1200);

  // ---------- 05 Stage ----------
  await click(page.getByRole("button", { name: /Next · Stage/i }));
  await page.waitForTimeout(2000);
  await click(page.getByRole("button", { name: /assemble stage/i }));
  await waitStage("stage", "assemble", 300);
  await page.waitForTimeout(3000);
  await orbit(page.locator(".stage-view model-viewer"), 220);
  await page.waitForTimeout(1500);
  await click(page.getByRole("button", { name: /Test physics/ }));
  await waitStage("stage", "physics test", 900);
  await page.waitForTimeout(5000);
  log("done");
} catch (e) {
  if (e !== "__finished__") {
    log("STOPPED:", e.message);
    process.exitCode = 1;
  }
} finally {
  const v = page.video();
  await ctx.close();
  await browser.close();
  if (v) {
    const p = await v.path();
    const dest = join(out, `simscrub_demo${DRY ? "_dry" : ""}${STAGE_ONLY ? "_stage_finale" : ""}_${new Date().toISOString().slice(0, 16).replace(/[:T]/g, "-")}.webm`);
    renameSync(p, dest);
    log("video:", dest);
  }
}
