// Screenshot the SimReady spec UI: cards, the compare inspector's spec panel, and the stage list.
import { chromium } from "playwright";
const out = process.argv[2] ?? ".";
const browser = await chromium.launch({ args: ["--use-angle=d3d11", "--enable-gpu", "--ignore-gpu-blocklist"] });
const page = await browser.newPage({ viewport: { width: 1440, height: 900 } });
page.on("pageerror", (e) => console.log("PAGE ERROR:", e.message));
await page.goto("http://localhost:5173/", { waitUntil: "networkidle" });
await page.getByRole("button", { name: /04 SimReady/i }).click();
await page.waitForTimeout(5000);
await page.screenshot({ path: `${out}/simready_cards.png` });
await page.getByRole("button", { name: /Compare/ }).first().click();
await page.waitForTimeout(4000);
await page.screenshot({ path: `${out}/compare_spec.png` });
await page.keyboard.press("Escape");
await page.getByRole("button", { name: /05 Stage/i }).click();
await page.waitForTimeout(4000);
await page.screenshot({ path: `${out}/stage.png` });
await browser.close();
