// Re-assemble from the Stage tab; the view must still be on Stage when it finishes.
import { chromium } from "playwright";
const browser = await chromium.launch();
const page = await browser.newPage({ viewport: { width: 1440, height: 900 } });
await page.goto("http://localhost:5173/", { waitUntil: "networkidle" });
await page.getByRole("button", { name: /05 Stage/i }).click();
await page.waitForTimeout(800);
await page.getByRole("button", { name: /Re-assemble stage/i }).click();
const activeTab = () => page.locator(".tab.on").innerText();
let sawWorking = false;
for (let i = 0; i < 120; i++) {
  const st = await page.locator("#p-stage .panel-hd .st").innerText().catch(() => "");
  if (/working/i.test(st)) sawWorking = true;
  if (sawWorking && !/working/i.test(st)) break;
  await page.waitForTimeout(250);
}
await page.waitForTimeout(600);
console.log("saw run:", sawWorking, "| active tab after run:", (await activeTab()).trim());
await browser.close();
