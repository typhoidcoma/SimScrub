// Capture tab: does the walkthrough preview play in a real browser (Edge has the codecs)?
import { chromium } from "playwright";
const out = process.argv[2] ?? ".";
let browser;
try { browser = await chromium.launch({ channel: "msedge" }); } catch { browser = await chromium.launch(); console.log("(Edge not found: bundled Chromium, no proprietary codecs)"); }
const page = await browser.newPage({ viewport: { width: 1440, height: 900 } });
page.on("pageerror", (e) => console.log("PAGE ERROR:", e.message));
await page.goto("http://localhost:5173/", { waitUntil: "networkidle" });
await page.getByRole("button", { name: /01 Capture/i }).click();
await page.waitForTimeout(2500);
const state = async (label) => {
  const r = await page.evaluate(() => {
    const v = document.querySelector(".video-frame video");
    const img = document.querySelector(".video-frame img");
    return v ? { video: true, ready: v.readyState, w: v.videoWidth, h: v.videoHeight, dur: v.duration, err: v.error && v.error.code }
             : { video: false, posterImg: !!img, note: document.querySelector(".video-note")?.textContent };
  });
  console.log(label, JSON.stringify(r), "| facts:", await page.locator(".video-facts").innerText().catch(() => "-"));
};
await state("job video:");
await page.getByRole("button", { name: "20261105_121402.mp4" }).click();
await page.waitForTimeout(2500);
await state("project video:");
const v = page.locator(".video-frame video");
if (await v.count()) { await v.evaluate((el) => { el.currentTime = 2.0; }); await page.waitForTimeout(800); }
await page.screenshot({ path: `${out}/capture.png` });
await browser.close();
