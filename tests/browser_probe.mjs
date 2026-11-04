// Shared Chromium loader for the computed-layout harnesses.
// Resolves playwright-core from DECKLAB_PLAYWRIGHT_CORE (a module path) or the normal
// module resolution. When it or its browser is unavailable (for example on CI), the
// harness prints {"skip": reason} and exits 0; the pytest caller turns that into a skip.
import { createRequire } from "node:module";

const require = createRequire(import.meta.url);

export async function launchChromiumOrSkip() {
  let chromium;
  try {
    ({ chromium } = require(process.env.DECKLAB_PLAYWRIGHT_CORE || "playwright-core"));
  } catch {
    console.log(JSON.stringify({ skip: "playwright-core is not installed (set DECKLAB_PLAYWRIGHT_CORE)" }));
    process.exit(0);
  }
  try {
    return await chromium.launch();
  } catch (error) {
    console.log(JSON.stringify({ skip: "Chromium could not launch: " + String(error.message || error).split("\n")[0] }));
    process.exit(0);
  }
}
