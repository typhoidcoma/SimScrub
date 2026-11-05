// Drag-test every visible 3D viewer on the Reconstruct and SimReady tabs, the way a user would.
// Usage: node scripts/check_orbit.mjs   (servers running)
import { chromium } from "playwright";

const browser = await chromium.launch({ args: ["--use-angle=d3d11", "--enable-gpu", "--ignore-gpu-blocklist"] });
const page = await browser.newPage({ viewport: { width: 1440, height: 900 } });
await page.goto("http://localhost:5173/", { waitUntil: "networkidle" });

for (const tab of ["03 Reconstruct", "04 SimReady"]) {
  await page.getByRole("button", { name: new RegExp(tab, "i") }).click();
  await page.waitForTimeout(6000);
  const viewers = page.locator("model-viewer").filter({ visible: true });
  const n = await viewers.count();
  for (let i = 0; i < n; i++) {
    const loc = viewers.nth(i);
    await loc.scrollIntoViewIfNeeded();
    await loc.evaluate((el) => el.loaded || new Promise((r) => el.addEventListener("load", r, { once: true })));
    await page.waitForTimeout(300);
    const info = await loc.evaluate((el) => {
      el.__cam = 0; el.addEventListener("camera-change", () => el.__cam++);
      const r = el.getBoundingClientRect();
      return { w: Math.round(r.width), h: Math.round(r.height), loaded: el.loaded, top: Math.round(r.top), bottom: Math.round(r.bottom) };
    });
    const box = await loc.boundingBox();
    if (!box || box.width < 10 || box.height < 10) { console.log(`${tab} #${i + 1}: COLLAPSED`, JSON.stringify(info)); continue; }
    const cx = box.x + box.width * 0.4, cy = box.y + box.height * 0.42; // off the exact centre, where model-viewer keeps its pan-target dot
    await page.mouse.move(cx, cy); await page.mouse.down();
    for (let k = 1; k <= 15; k++) { await page.mouse.move(cx + k * 6, cy + k * 2); await page.waitForTimeout(16); }
    await page.mouse.up(); await page.waitForTimeout(400);
    const cam = await loc.evaluate((el) => el.__cam);
    console.log(`${tab} #${i + 1}: ${cam > 0 ? "ORBITS" : "STUCK "} cam=${cam}`, JSON.stringify(info));
  }
}
await browser.close();
