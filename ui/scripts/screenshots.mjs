// UI check: screenshot every stage tab and test that 3D views can be orbited.
// Usage: node scripts/screenshots.mjs <out_dir> [width] [height]
import { chromium } from "playwright";

const out = process.argv[2] ?? ".";
const width = Number(process.argv[3] ?? 1440);
const height = Number(process.argv[4] ?? 900);

const browser = await chromium.launch({ args: ["--use-angle=d3d11", "--enable-gpu", "--ignore-gpu-blocklist"] });
const page = await browser.newPage({ viewport: { width, height } });
page.on("pageerror", (e) => console.log("PAGE ERROR:", e.message));
page.on("console", (m) => m.type() === "error" && console.log("CONSOLE:", m.text()));

await page.goto("http://localhost:5173/", { waitUntil: "networkidle" });
await page.waitForTimeout(800);

const tabs = ["Capture", "Segment", "Reconstruct", "SimReady", "Stage"];
for (const [i, t] of tabs.entries()) {
  await page.getByRole("button", { name: new RegExp(`0${i + 1} ${t}`, "i") }).click();
  await page.waitForTimeout(t === "Reconstruct" || t === "SimReady" ? 9000 : 700);
  await page.screenshot({ path: `${out}/tab_${i + 1}_${t.toLowerCase()}.png`, fullPage: true });
  const h = await page.evaluate(() => document.documentElement.scrollHeight);
  console.log(`${t.padEnd(12)} page height ${h}px ${h <= height ? "fits" : `scrolls ${h - height}px`}`);

  if (t === "Reconstruct" || t === "SimReady") {
    const mv = page.locator("section:visible model-viewer").first();
    if (!(await mv.count())) { console.log(t, "no model-viewer"); continue; }
    const info = await mv.evaluate((el) => ({
      attrs: el.getAttributeNames(),
      loaded: el.loaded,
      cameraControls: el.cameraControls,
      autoRotate: el.autoRotate,
    }));
    console.log(t, "model-viewer:", JSON.stringify(info));
    const before = await mv.evaluate((el) => el.getCameraOrbit().toString());
    const box = await mv.boundingBox();
    await page.mouse.move(box.x + box.width / 2, box.y + box.height / 2);
    await page.mouse.down();
    await page.mouse.move(box.x + box.width / 2 + 80, box.y + box.height / 2 + 20, { steps: 10 });
    await page.mouse.up();
    await page.waitForTimeout(300);
    const after = await mv.evaluate((el) => el.getCameraOrbit().toString());
    // what element actually receives the pointer at the centre of the viewer?
    const hit = await page.evaluate(([x, y]) => {
      const e = document.elementFromPoint(x, y);
      return e ? `${e.tagName.toLowerCase()}.${e.className}` : null;
    }, [box.x + box.width / 2, box.y + box.height / 2]);
    console.log(t, "drag:", before, "->", after, "| element under cursor:", hit);
  }
}

// text overflow audit: elements whose content is clipped
const clipped = await page.evaluate(() => {
  const out = [];
  for (const el of document.querySelectorAll("body *")) {
    if (el.offsetParent === null || el.children.length) continue;
    if (el.scrollWidth > el.clientWidth + 1 && getComputedStyle(el).overflow !== "visible") {
      out.push(`${el.tagName.toLowerCase()}.${el.className}: "${el.textContent.slice(0, 60)}"`);
    }
  }
  return out;
});
console.log("clipped on last tab:", clipped.length, clipped.slice(0, 12));
await browser.close();
