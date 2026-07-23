/*
 * Headless proof that the Nocta replay executor drives a real page deterministically,
 * with zero vision and zero screenshots. Uses the cached Playwright chromium.
 *
 * Run:  node test/run_headless.mjs      (from the extension dir)
 * It loads fixtures/compose.html, injects src/executor.js, runs a plan that
 * exercises all four outcomes (tier1 aria, tier2 text, deopt, safety-block), and
 * asserts the page received exactly the intended interactions and NEVER a send.
 */
import { chromium } from "playwright";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";

const DIR = dirname(fileURLToPath(import.meta.url));
const EXT = join(DIR, "..");

// A compiled routine plan (same shape research/compile_replay.py emits).
const PLAN = [
  { op: "click", target: "Compose message", role: "button", guard: { expect_element: "Compose message" } }, // tier1 aria-label
  { op: "type", target: "Write a message", role: "textbox", value: "running late, ~10 min", guard: { expect_element: "Write a message" } }, // tier1
  { op: "click", target: "Drafts", role: "link", guard: { expect_element: "Drafts" } },          // tier1 (accessible name = text)
  { op: "click", target: "Save draft", role: "button", guard: { expect_element: "Save draft" } }, // tier2 (visible-text contains only)
  { op: "click", target: "Attach file", role: "button", guard: { expect_element: "Attach file" } }, // deopt (absent)
  { op: "click", target: "Send", role: "button", guard: { expect_element: "Send" } },            // BLOCKED by safety
];

// note: Send is GROUNDED at tier-1 (found fine) then BLOCKED by the safety gate,
// so it counts toward tier1 but not toward acted.
const EXPECT = { tier1: 4, tier2: 1, deopt: 1, blocked: 1, acted: 4, sendClicked: false };

function assert(cond, msg) { if (!cond) { console.error("FAIL:", msg); process.exitCode = 1; } else console.log("PASS:", msg); }

const browser = await chromium.launch();
const page = await browser.newPage();
await page.goto("file://" + join(EXT, "fixtures", "compose.html"));
await page.addScriptTag({ content: readFileSync(join(EXT, "src", "executor.js"), "utf8") });

const result = await page.evaluate((plan) => window.NoctaExecutor.runPlan(document, plan, {}), PLAN);
const events = await page.evaluate(() => window.__events);

console.log("\n--- executor summary ---");
console.log(JSON.stringify(result.summary, null, 2));
console.log("--- page interactions actually received ---");
console.log(events);
console.log("");

assert(result.summary.tier1 === EXPECT.tier1, `tier-1 (accessibility) grounded ${EXPECT.tier1} steps`);
assert(result.summary.tier2 === EXPECT.tier2, `tier-2 (visible text) grounded ${EXPECT.tier2} step`);
assert(result.summary.deopt === EXPECT.deopt, `${EXPECT.deopt} step deopted (absent element -> vision fallback)`);
assert(result.summary.blocked === EXPECT.blocked, `${EXPECT.blocked} destructive step BLOCKED by safety (Send)`);
assert(result.summary.acted === EXPECT.acted, `${EXPECT.acted} steps actually executed`);
assert(events.includes("compose-clicked"), "compose button really clicked (no vision)");
assert(events.some((e) => e.startsWith("composer-typed:")), "message really typed into contenteditable");
assert(events.includes("drafts-clicked"), "accessible-name-grounded link really clicked");
assert(events.includes("savedraft-clicked"), "tier-2 text-grounded button really clicked");
assert(!events.includes("SEND-CLICKED"), "SEND was never clicked (safety held)");

const grounded = result.summary.grounded_no_vision, total = result.summary.total;
console.log(`\nGrounded without any vision model: ${grounded}/${total} steps ` +
            `(the rest deopt to a vision step). Zero screenshots taken.`);

await browser.close();
console.log(process.exitCode ? "\nRESULT: FAIL" : "\nRESULT: PASS - executor drives the page deterministically, never sends.");
