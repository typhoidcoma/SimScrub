// Render SimReady thumbnails (SR.002: 256x256 PNG) from viewer GLBs, headless.
// Usage: node scripts/thumbnail.mjs <in1.glb> <out1.png> [<in2.glb> <out2.png> ...]
import { chromium } from "playwright";
import { readFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const here = dirname(fileURLToPath(import.meta.url));
const mvJs = readFileSync(resolve(here, "../node_modules/@google/model-viewer/dist/model-viewer.min.js"), "utf8");
const pairs = process.argv.slice(2);
if (pairs.length < 2 || pairs.length % 2) {
  console.error("usage: thumbnail.mjs <in.glb> <out.png> ...");
  process.exit(2);
}

const browser = await chromium.launch({ args: ["--use-angle=d3d11", "--enable-gpu", "--ignore-gpu-blocklist"] });
const page = await browser.newPage({ viewport: { width: 256, height: 256 }, deviceScaleFactor: 1 });
// Serve each GLB from a fake origin so the page can fetch it.
await page.route("https://thumb.local/**", (route) => {
  const i = Number(new URL(route.request().url()).pathname.split("/").pop());
  route.fulfill({ body: readFileSync(pairs[i * 2]), contentType: "model/gltf-binary" });
});
await page.setContent(`<!doctype html><html><head><style>
  html,body{margin:0;width:256px;height:256px;background:#2a2d30}
  model-viewer{width:256px;height:256px;--poster-color:transparent;background:radial-gradient(circle at 50% 40%,#43474c,#1f2123)}
</style></head><body><model-viewer id="mv" camera-orbit="-35deg 68deg auto" shadow-intensity="0.8" exposure="1.1"
  interaction-prompt="none"></model-viewer><script type="module">${mvJs}</script></body></html>`);

for (let i = 0; i < pairs.length / 2; i++) {
  await page.evaluate(async (src) => {
    const mv = document.getElementById("mv");
    await customElements.whenDefined("model-viewer");
    const loaded = new Promise((r) => mv.addEventListener("load", r, { once: true }));
    mv.src = src;
    await loaded;
  }, `https://thumb.local/${i}`);
  await page.waitForTimeout(400);
  await page.screenshot({ path: pairs[i * 2 + 1], clip: { x: 0, y: 0, width: 256, height: 256 } });
  console.log("thumbnail", pairs[i * 2 + 1]);
}
await browser.close();
