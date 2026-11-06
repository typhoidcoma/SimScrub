// Full-screen demo recording: the app (Edge kiosk) and Isaac Sim, captured from the real screen.
//
//   node scripts/record_screen.mjs <out_dir> [--stage-only] [--dry]
//
// ffmpeg records the whole display (Desktop Duplication -> NVENC H.264 MP4)
// while this script drives the app like a person: Capture -> Process ->
// Segment (exclude BX-06) -> Reconstruct (Regenerate: live Tripo batch) ->
// SimReady -> Stage (assemble + physics test) -> Launch Isaac Sim -> full
// screen drop test + 16-box pile drop in Isaac -> back to the app.
//
// --stage-only  just the Stage + Isaac finale (no Tripo spend)
// --dry         use the mesh cache instead of a new Tripo batch
import { chromium } from "playwright";
import { spawn, execFileSync } from "node:child_process";
import { mkdirSync, mkdtempSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, dirname } from "node:path";
import { fileURLToPath } from "node:url";

const here = dirname(fileURLToPath(import.meta.url));
const out = process.argv[2] ?? "recordings";
const DRY = process.argv.includes("--dry");
const STAGE_ONLY = process.argv.includes("--stage-only");
const PREFLIGHT = process.argv.includes("--preflight");   // open, settle, grab one frame, exit: no recording
const SETTLE_MS = 45000;                                  // let any delayed browser popups appear before recording
const VIDEO = "20261105_121402.mp4";
const EXCLUDE = "06";
const EXPECT_BOXES = 4;
const API = "http://127.0.0.1:8000";
mkdirSync(out, { recursive: true });

const t0 = Date.now();
const log = (...a) => console.log(`[${((Date.now() - t0) / 1000).toFixed(0).padStart(4)}s]`, ...a);
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const api = async (path, method = "GET") => (await fetch(API + path, { method })).json();
function front(target) {
  // A number targets a process id (our own browser); a string matches a window title (Isaac Sim).
  const args = typeof target === "object" ? ["-CmdMatch", target.cmd]
    : typeof target === "number" ? ["-ProcessId", String(target)] : ["-Title", target];
  try {
    return execFileSync("powershell", ["-NoProfile", "-ExecutionPolicy", "Bypass", "-File", join(here, "window.ps1"), ...args],
      { encoding: "utf8" }).trim();
  } catch (e) { return `front(${target}) failed: ${e.message}`; }
}

// ---------- screen recorder ----------
const stamp = new Date().toISOString().slice(0, 16).replace(/[:T]/g, "-");
const file = join(out, `simscrub_screen${STAGE_ONLY ? "_stage" : ""}${DRY ? "_dry" : ""}_${stamp}.mp4`);
let ff = null;
function startRecording() {
  ff = spawn("ffmpeg", ["-y", "-loglevel", "error", "-f", "lavfi", "-i", "ddagrab=output_idx=0:framerate=30:draw_mouse=0",
    "-vf", "hwdownload,format=bgra", "-c:v", "h264_nvenc", "-preset", "p5", "-rc", "vbr", "-cq", "19", "-b:v", "0",
    "-pix_fmt", "yuv420p", "-movflags", "+faststart", file], { stdio: ["pipe", "inherit", "inherit"] });
  log("recording", file);
}

// ---------- app in Edge, full screen ----------
// A throwaway profile folder: no personal bookmarks or sign-ins, and a unique
// marker to find exactly this browser's window (never a personal one).
const PROFILE = mkdtempSync(join(tmpdir(), "simscrub-rec-"));
// Chrome, not Edge: Edge signs a fresh profile into the Windows Microsoft account
// and pops a "now syncing" dialog over everything (with your email in it).
const ctx = await chromium.launchPersistentContext(PROFILE, { channel: "chrome", headless: false, viewport: null,
  ignoreDefaultArgs: ["--enable-automation"],          // no "controlled by automated test software" bar
  args: ["--start-maximized", "--no-first-run", "--no-default-browser-check", "--disable-sync",
         "--test-type",                               // no "unsupported command-line flag" warning bar
         "--disable-features=Translate,ChromeWhatsNewUI,PrivacySandboxSettings4", "--enable-gpu"] });
const browser = { close: () => ctx.close() };
await ctx.addInitScript(() => {
  addEventListener("DOMContentLoaded", () => {
    const st = document.createElement("style");   // no scrollbars in the recording
    st.textContent = "html,body{scrollbar-width:none} ::-webkit-scrollbar{display:none}";
    document.head.appendChild(st);
    const c = document.createElement("div");
    c.style.cssText = "position:fixed;left:0;top:0;width:22px;height:22px;margin:-11px 0 0 -11px;border-radius:50%;" +
      "background:rgba(255,255,255,.85);border:2px solid #f05a28;box-shadow:0 0 0 4px rgba(240,90,40,.25);" +
      "pointer-events:none;z-index:2147483647;transition:transform .12s ease;transform:translate(50vw,50vh)";
    document.body.appendChild(c);
    let x = innerWidth / 2, y = innerHeight / 2;
    addEventListener("mousemove", (e) => { x = e.clientX; y = e.clientY; c.style.transform = `translate(${x}px,${y}px)`; }, true);
    addEventListener("mousedown", () => { c.style.transform = `translate(${x}px,${y}px) scale(.7)`; }, true);
    addEventListener("mouseup", () => { c.style.transform = `translate(${x}px,${y}px) scale(1)`; }, true);
  });
});
const page = ctx.pages()[0] ?? await ctx.newPage();
page.on("pageerror", (e) => log("PAGE ERROR:", e.message));
await page.goto("http://localhost:5173/", { waitUntil: "networkidle" });
// Our browser only, by process id: never a personal browser window that happens to show the app.
async function appFullscreen() {
  log("app window", front({ cmd: PROFILE }));
  const cdp = await ctx.newCDPSession(page);
  const { windowId, bounds } = await cdp.send("Browser.getWindowForTarget");
  // Only (re)enter full screen if needed: re-entering shows Edge's "to exit full screen" toast.
  if (bounds.windowState !== "fullscreen") {
    await cdp.send("Browser.setWindowBounds", { windowId, bounds: { windowState: "normal" } }).catch(() => {});
    await cdp.send("Browser.setWindowBounds", { windowId, bounds: { windowState: "fullscreen" } });
  }
  await page.waitForTimeout(1200);
}
await appFullscreen();
// Settle: give delayed popups a chance to appear, then check before recording anything.
await page.waitForTimeout(PREFLIGHT ? SETTLE_MS : SETTLE_MS);
await appFullscreen();
const W = await page.evaluate(() => innerWidth), H = await page.evaluate(() => innerHeight);
const sh = await page.evaluate(() => screen.height);
log(`app viewport ${W}x${H} (screen ${sh})`);
if (H < sh - 4) {
  log(`ABORT: app isn't truly full screen (${H} < ${sh}); not recording`);
  await ctx.close(); process.exit(2);
}
if (PREFLIGHT) {
  const shot = join(out, "preflight.png");
  execFileSync("ffmpeg", ["-y", "-loglevel", "error", "-f", "lavfi", "-i", "ddagrab=output_idx=0:framerate=5:draw_mouse=0",
    "-vf", "hwdownload,format=bgra", "-frames:v", "1", shot]);
  log("preflight frame:", shot);
  await ctx.close(); process.exit(0);
}
startRecording();
await page.waitForTimeout(1500);

let mx = W / 2, my = H / 2;
async function glide(x, y, ms = 650) {
  const steps = Math.max(8, Math.round(ms / 16));
  for (let i = 1; i <= steps; i++) {
    const k = i / steps, e = k < 0.5 ? 2 * k * k : 1 - (-2 * k + 2) ** 2 / 2;
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
  await locator.evaluate((el) => el.loaded || new Promise((r) => el.addEventListener("load", r, { once: true })));
  await page.waitForTimeout(600);
  const b = await locator.boundingBox();
  const sx = b.x + b.width * 0.38, sy = b.y + b.height * 0.42;
  await glide(sx, sy, 500);
  await page.mouse.down();
  for (let i = 1; i <= 40; i++) { await page.mouse.move(sx + (dx * i) / 40, sy + (14 * i) / 40); await page.waitForTimeout(20); }
  await page.mouse.up();
  mx = sx + dx; my = sy + 14;
}
async function waitStage(stage, label, limitS = 1800) {
  const end = Date.now() + limitS * 1000;
  let seen = false;
  while (Date.now() < end) {
    const s = (await api("/api/job")).stages[stage].status;
    if (s === "working") seen = true;
    if (seen && ["done", "attention", "failed"].includes(s)) { log(`${label}: ${s}`); return s; }
    await page.waitForTimeout(1000);
  }
  throw new Error(`timed out waiting for ${label}`);
}
const tab = (re) => page.getByRole("button", { name: re });

try {
  await page.waitForTimeout(2500);
  if (!STAGE_ONLY) {
    // ---------- 01 Capture ----------
    await click(tab(/01 Capture/i));
    await page.waitForTimeout(1200);
    await click(page.getByRole("button", { name: VIDEO }));
    await page.waitForTimeout(1200);
    const video = page.locator(".video-frame video");
    if (await video.count()) { await video.evaluate((v) => { v.currentTime = 0; v.play(); }); await page.waitForTimeout(7600); }
    await click(page.getByRole("button", { name: /^Process/ }));
    log("process");
    await waitStage("segment", "segment", 300);
    await page.waitForTimeout(2500);

    // ---------- 02 Segment ----------
    await glide(W * 0.45, H * 0.55, 900);
    await page.waitForTimeout(800);
    await click(page.locator(".strip-item").filter({ hasText: EXCLUDE }), { ms: 900, pause: 700 });
    log(`excluded BX-${EXCLUDE}`);
    await page.waitForTimeout(2500);

    // ---------- 03 Reconstruct ----------
    await click(page.getByRole("button", { name: /Next · Reconstruct/i }));
    await page.waitForTimeout(3000);
    const plan = await api("/api/reconstruct/plan");
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
    await page.waitForTimeout(3000);
    await orbit(page.locator("section:visible model-viewer").first(), 180);
    await page.waitForTimeout(1200);
    await click(page.getByRole("button", { name: /Inspect/ }).first());
    await page.waitForTimeout(2500);
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
    await click(page.getByRole("button", { name: /Next · Stage/i }));
  } else {
    await click(tab(/05 Stage/i));
  }

  // ---------- 05 Stage ----------
  await page.waitForTimeout(2000);
  await click(page.getByRole("button", { name: /assemble stage/i }));
  await waitStage("stage", "assemble", 300);
  await page.waitForTimeout(3000);
  await orbit(page.locator(".stage-view model-viewer"), 220);
  await page.waitForTimeout(1200);
  await click(page.getByRole("button", { name: /Test physics/ }));
  await waitStage("stage", "physics test", 900);
  await page.waitForTimeout(3000);

  // ---------- Isaac Sim, full screen ----------
  await click(page.getByRole("button", { name: /^Launch Isaac$/ }), { pause: 700 });
  log("launch isaac");
  let scene = null;
  for (let i = 0; i < 120 && !(scene && ["opened", "failed"].includes(scene.state)); i++) {
    await sleep(2000);
    scene = (await api("/api/isaac")).scene;
  }
  if (!scene || scene.state !== "opened") throw new Error(`Isaac Sim didn't open the stage (${scene?.state})`);
  log("isaac opened", front("Isaac Sim"));
  await api("/api/isaac/control?cmd=present", "POST");
  await sleep(4500);
  await api("/api/isaac/control?cmd=drop&lift=0.4", "POST");
  log("drop 1");
  await sleep(6000);
  await api("/api/isaac/control?cmd=pile&count=16", "POST");
  log("pile drop x16");
  await sleep(10000);

  // ---------- back to the app ----------
  await api("/api/isaac/control?cmd=windowed", "POST");
  await sleep(1500);
  await appFullscreen();
  await page.waitForTimeout(1500);
  await click(page.getByRole("button", { name: /^Checks$/ }).last()).catch(() => {});
  await page.waitForTimeout(5000);
  log("done");
} catch (e) {
  log("STOPPED:", e.message);
  process.exitCode = 1;
} finally {
  if (ff) {
    ff.stdin.write("q");
    await new Promise((r) => ff.on("close", r));
  }
  await browser.close();
  for (const p of execFileSync("powershell", ["-NoProfile", "-Command",
    "Get-Process | ? { $_.MainWindowTitle -like '*Isaac Sim*' } | % { $_.Id }"], { encoding: "utf8" }).split(/\s+/).filter(Boolean)) {
    try { process.kill(Number(p)); } catch { /* already gone */ }
  }
  log("video:", file);
}
