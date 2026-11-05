// Rename the project through the UI, confirm it persists, then put the old name back.
import { chromium } from "playwright";
const browser = await chromium.launch();
const page = await browser.newPage({ viewport: { width: 1280, height: 800 } });
for (let i = 0; i < 40; i++) { try { await page.goto("http://localhost:5173/", { waitUntil: "networkidle" }); break; } catch { await page.waitForTimeout(500); } }
await page.waitForSelector(".project-name");
const before = await page.locator(".project-name").evaluate((el) => el.firstChild.textContent.trim());
const rename = async (to) => {
  await page.locator(".project-name").click();
  await page.locator(".project-name-input").fill(to);
  await page.keyboard.press("Enter");
  await page.waitForTimeout(500);
};
await rename("Rename Test 123");
await page.reload({ waitUntil: "networkidle" });
await page.waitForSelector(".project-name");
const after = await page.locator(".project-name").evaluate((el) => el.firstChild.textContent.trim());
// Esc must cancel without saving
await page.locator(".project-name").click();
await page.locator(".project-name-input").fill("SHOULD NOT SAVE");
await page.keyboard.press("Escape");
await page.waitForTimeout(400);
const afterEsc = await page.locator(".project-name").evaluate((el) => el.firstChild.textContent.trim());
await rename(before);
console.log(JSON.stringify({ before, afterReload: after, afterEsc, restored: await page.locator(".project-name").evaluate((el) => el.firstChild.textContent.trim()) }));
await browser.close();
