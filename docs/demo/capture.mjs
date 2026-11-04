// Plays the story in the real page against the fake API and records it.
//   node capture.mjs            the stills, then the GIF
//   node capture.mjs stills     docs/demo/steps/*.png and home-dark.png
//   node capture.mjs gif        docs/demo/evalution-demo.gif

import { execFileSync } from "node:child_process";
import { mkdirSync, mkdtempSync, renameSync, rmSync, statSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { chromium } from "playwright-core";
import { clock, FOLDER, SHOE_TEST } from "./cartly.mjs";
import { startFakeApi } from "./fake-api.mjs";

const HERE = dirname(fileURLToPath(import.meta.url));
const WEB = join(HERE, "..", "..", "web");
const CHROME = process.env.CHROME ?? "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome";
const PORT = 8917;
const BASE = `http://127.0.0.1:${PORT}`;
/** The page's size: small, so the text stays readable when it shrinks to fit a README. */
const VIEW = { width: 1164, height: 764 };
/** The GIF is the page zoomed in the browser: the screencast only gives one pixel per CSS pixel. */
const GIF_ZOOM = 1.1;
const GIF_VIEW = { width: 1280, height: 840 };
/** The whole recording plays this much faster than it was acted out. */
const GIF_PACE = 1.2;
const GIF_FPS = 15;
const STILL_MAX_BYTES = 400 * 1024;

const sleep = (ms) => new Promise((done) => setTimeout(done, ms));

/** The pointer: headless Chrome draws none, and clicks read better when you can see them. */
const POINTER = `
addEventListener("DOMContentLoaded", () => {
  document.body.style.zoom = ${GIF_ZOOM};
  const pointer = document.createElement("div");
  pointer.id = "demo-pointer";
  pointer.style.cssText = "position:fixed;left:0;top:0;z-index:99999;pointer-events:none;width:22px;height:22px;transform:translate(560px,430px)";
  pointer.innerHTML = '<svg viewBox="0 0 22 22" width="22" height="22"><path d="M4 2.5v14.6l4-3.7 2.6 6 2.7-1.2-2.6-5.9 5.5-.3z" fill="#14161a" stroke="#fff" stroke-width="1.4" stroke-linejoin="round"/></svg>';
  document.documentElement.append(pointer);
  let at = window.__demoStart ?? [560, 430];
  window.__demoMove = (x, y, ms) => new Promise((done) => {
    const [fromX, fromY] = at;
    const began = performance.now();
    const step = (now) => {
      const t = Math.min(1, (now - began) / ms);
      const eased = t < 0.5 ? 2 * t * t : 1 - (-2 * t + 2) ** 2 / 2;
      at = [fromX + (x - fromX) * eased, fromY + (y - fromY) * eased];
      pointer.style.transform = "translate(" + at[0] + "px," + at[1] + "px)";
      if (t < 1) requestAnimationFrame(step); else done();
    };
    requestAnimationFrame(step);
  });
});`;

/** The GIF's time: stretches of the recording played faster, or left out. */
function timeline() {
  const marks = [{ at: Date.now(), speed: 1 }];
  return {
    speed(speed) {
      marks.push({ at: Date.now(), speed });
    },
    cut() {
      marks.push({ at: Date.now(), speed: Number.POSITIVE_INFINITY });
    },
    /** Where a moment of the recording lands in the GIF, in ms from its start. */
    place(at) {
      let out = 0;
      for (const [index, mark] of marks.entries()) {
        const end = marks[index + 1]?.at ?? Number.POSITIVE_INFINITY;
        if (at <= mark.at) break;
        out += (Math.min(at, end) - mark.at) / mark.speed;
      }
      return out;
    },
  };
}

async function story({ page, api, mode, times }) {
  const stills = mode === "stills";
  const hold = (ms) => sleep(stills ? Math.min(ms, 150) : ms);
  const setClock = (at) => page.clock.setFixedTime(new Date(at * 1000));

  const pointTo = async (locator, ms = 420) => {
    await locator.waitFor();
    const box = await locator.boundingBox();
    const x = box.x + box.width / 2;
    const y = box.y + box.height / 2;
    if (!stills) await page.evaluate(([px, py, t]) => window.__demoMove(px, py, t), [x, y, ms]);
    await page.mouse.move(x, y);
  };
  const click = async (locator, ms) => {
    await pointTo(locator, ms);
    await hold(140);
    await page.mouse.down();
    await sleep(60);
    await page.mouse.up();
  };
  const type = async (text, gap = 24) => {
    for (const char of text) {
      await page.keyboard.type(char);
      if (!stills) await sleep(gap + Math.random() * 16);
    }
  };
  /** A still of the screen as it is now, at `height` when the step needs more of the page. */
  const still = async (name, height) => {
    if (!stills) return;
    if (height) await page.setViewportSize({ width: VIEW.width, height });
    await sleep(350);
    await saveStill(page, join(HERE, name));
    if (height) await page.setViewportSize(VIEW);
  };
  /** Slowly to the bottom of the screen's scrolling area, then back to the top on the next page. */
  const scrollDown = async (ms) => {
    if (stills) return;
    await page.evaluate(
      (t) =>
        new Promise((done) => {
          const area = [...document.querySelectorAll("main div")].find(
            (el) => el.scrollHeight > el.clientHeight + 4 && getComputedStyle(el).overflow === "auto",
          );
          const distance = area.scrollHeight - area.clientHeight;
          const began = performance.now();
          const step = (now) => {
            const p = Math.min(1, (now - began) / t);
            area.scrollTop = distance * (p < 0.5 ? 2 * p * p : 1 - (-2 * p + 2) ** 2 / 2);
            if (p < 1) requestAnimationFrame(step);
            else done();
          };
          requestAnimationFrame(step);
        }),
      ms,
    );
  };
  const button = (name, options = {}) => page.getByRole("button", { name, ...options });
  const nav = (name) => page.getByRole("navigation").getByRole("button", { name, exact: true });
  const scene = (name) => console.log(`${(times.place(Date.now()) / 1000).toFixed(1)} s: ${name}`);

  // 1. Connect
  scene("connect");
  await setClock(clock.connect);
  await page.goto(BASE);
  const folder = page.getByLabel("Address or project folder");
  await folder.waitFor();
  await page.evaluate(() => document.fonts.ready);
  await hold(300);
  await click(folder, 380);
  await type(FOLDER, 36);
  await hold(200);
  await click(button("Connect", { exact: true }), 320);
  times.speed(1.5);
  await page.getByText("Ready", { exact: true }).waitFor();
  await still("steps/01-connect.png");

  // 2. What it learned
  scene("learned");
  times.speed(3.5);
  await click(page.getByRole("main").getByRole("button", { name: "What it learned" }), 320);
  await page.getByRole("heading", { name: "What it learned" }).waitFor();
  await page.getByRole("img", { name: "Diagram of how Cartly works" }).waitFor({ timeout: 20_000 });
  times.speed(1);
  await page.getByRole("heading", { name: "Typical paths" }).waitFor();
  await still("steps/02-learned.png");
  await hold(2900);

  // A cut to later that afternoon: one test has been added and run, as Run 1.
  await pointTo(nav("Tests"));
  await hold(150);
  times.cut();
  api.seedFirstRun();
  await setClock(clock.tests);
  await page.goto(`${BASE}/?target=cartly&view=tests`);
  await page.getByText("Finds the right order").waitFor();
  await page.mouse.move(VIEW.width / 2, 200);
  await sleep(300);
  times.speed(1);

  // 3. Ask for a test
  scene("ask");
  await hold(250);
  const composer = page.getByRole("textbox", { name: "What should we test?" });
  await click(composer, 380);
  await type("Check it remembers a customer's shoe size in a new conversation");
  await still("steps/03-ask-test.png");
  await hold(250);
  await click(button("Write the test"), 340);

  // 4. Ask AI proposes
  scene("plan");
  await page.getByRole("complementary", { name: "Ask AI" }).waitFor();
  times.speed(3);
  const add = button("Add to tests");
  await add.waitFor({ timeout: 20_000 });
  times.speed(1);
  await page.mouse.move(600, 620);
  await still("steps/04-plan.png");
  await hold(2600);
  await click(add, 380);
  times.speed(3);
  const added = page.getByRole("complementary").getByRole("button", { name: SHOE_TEST.name });
  await added.waitFor({ timeout: 20_000 });
  times.speed(1);
  await hold(350);

  // 5. The test's steps, before it has run
  scene("steps");
  await click(added, 340);
  await page.getByText("Not run yet").waitFor();
  await hold(100);
  await click(button("Close"), 300);
  await page.getByText("Every check above holds").waitFor();
  await page.mouse.move(600, 420);
  await still("steps/05-steps.png", 1290);
  await hold(1000);
  await scrollDown(1900);
  await hold(800);

  // 6. Run: the confirm with the estimate, then progress
  scene("run");
  await click(nav("Tests"), 380);
  const runAll = button(/^Run all · about/);
  await runAll.waitFor();
  await hold(150);
  await click(runAll, 340);
  await page.getByText(/rough guess/).waitFor();
  await page.getByRole("alertdialog").getByRole("button", { name: "Run", exact: true }).hover();
  await still("steps/06-run-confirm.png");
  await hold(1300);
  await click(page.getByRole("alertdialog").getByRole("button", { name: "Run", exact: true }), 260);
  await setClock(clock.secondRun);
  await page.getByText(/^\d of 2 tests$/).first().waitFor();
  times.speed(4.5);
  await page.mouse.move(600, 500);
  await page.getByText("try 2").first().waitFor({ timeout: 20_000 });
  await still("steps/06-run.png");
  await page.getByText("1 of 2 tests pass").waitFor({ timeout: 30_000 });
  times.speed(1);
  await hold(1100);

  // 7. The report on Home: the climax
  scene("report");
  await click(nav("Home"), 340);
  await page.getByRole("heading", { name: "1 of 2 tests passed" }).waitFor();
  await page.getByText("Tests passing", { exact: true }).waitFor();
  await page.mouse.move(980, 690);
  await still("steps/07-report.png", 1180);
  if (stills) {
    await page.emulateMedia({ colorScheme: "dark" });
    await still("home-dark.png", 1180);
    await page.emulateMedia({ colorScheme: "light" });
  }
  await hold(2800);
  await pointTo(page.getByRole("button", { name: "See why" }).first(), 800);
  await hold(5400);

  // 8. The code changed
  scene("code");
  await setClock(clock.codeChange);
  api.codeChanged();
  times.speed(4);
  const change = page.getByRole("heading", { name: /Code changed since the last run/ });
  await change.waitFor({ timeout: 10_000 });
  times.speed(1);
  await hold(250);
  const runThem = button(/^Run them/);
  await pointTo(runThem, 450);
  await still("steps/08-code-change.png");
  await hold(2000);

  // 9. Asking a question
  scene("question");
  const askBox = page.getByRole("textbox", { name: "Ask about Run 2" });
  await click(askBox, 420);
  await type("Why does it forget the size if it has a profile tool?");
  await hold(250);
  await page.keyboard.press("Enter");
  times.speed(4);
  const answer = page.getByText("Because the prompt sends it to notes");
  await answer.waitFor({ timeout: 20_000 });
  times.speed(1);
  await page.mouse.move(300, 680);
  await still("steps/09-ask.png");
  await hold(3600);
}

/** A screenshot as a small palette PNG; text and flat colour lose nothing visible at 256 colours. */
async function saveStill(page, path) {
  mkdirSync(dirname(path), { recursive: true });
  const raw = `${path}.raw.png`;
  await page.screenshot({ path: raw });
  execFileSync("ffmpeg", [
    "-y", "-loglevel", "error", "-i", raw,
    "-vf", "split[a][b];[a]palettegen=max_colors=256:stats_mode=full[p];[b][p]paletteuse=dither=none",
    "-compression_level", "9", path,
  ]);
  rmSync(raw);
  const { size } = statSync(path);
  console.log(`${path.slice(HERE.length + 1)}: ${Math.round(size / 1024)} KB`);
  if (size > STILL_MAX_BYTES) console.warn(`  over the ${STILL_MAX_BYTES / 1024} KB limit`);
}

async function open(mode) {
  const browser = await chromium.launch({ executablePath: CHROME, headless: true, args: ["--hide-scrollbars"] });
  const context = await browser.newContext({
    viewport: mode === "stills" ? VIEW : GIF_VIEW,
    deviceScaleFactor: mode === "stills" ? 2 : 1,
    colorScheme: "light",
    locale: "en-US",
    timezoneId: "America/New_York",
  });
  const page = await context.newPage();
  page.on("pageerror", (error) => console.error("page error:", error.message));
  page.on("response", (response) => {
    if (response.status() >= 400) console.error(`${response.status()} ${response.url()}`);
  });
  if (mode === "gif") await page.addInitScript(POINTER);
  return { browser, page, context };
}

async function runStills() {
  const api = await startFakeApi({ port: PORT, webRoot: join(WEB, "dist") });
  const { browser, page } = await open("stills");
  try {
    await story({ page, api, mode: "stills", times: timeline() });
  } finally {
    await browser.close();
    api.close();
  }
}

async function runGif() {
  const frames = mkdtempSync(join(tmpdir(), "evalution-demo-"));
  const api = await startFakeApi({ port: PORT, webRoot: join(WEB, "dist") });
  const { browser, page, context } = await open("gif");
  const cdp = await context.newCDPSession(page);
  const got = [];
  cdp.on("Page.screencastFrame", async ({ data, sessionId, metadata }) => {
    const file = join(frames, `${String(got.length).padStart(6, "0")}.png`);
    got.push({ file, at: metadata.timestamp * 1000 });
    writeFileSync(file, Buffer.from(data, "base64"));
    await cdp.send("Page.screencastFrameAck", { sessionId }).catch(() => {});
  });
  await page.goto("about:blank");
  await cdp.send("Page.startScreencast", { format: "png", everyNthFrame: 1 });
  const times = timeline();
  let ended;
  try {
    await story({ page, api, mode: "gif", times });
    ended = Date.now();
    await cdp.send("Page.stopScreencast");
  } finally {
    await browser.close();
    api.close();
  }
  encodeGif(got, times, ended, frames);
  rmSync(frames, { recursive: true, force: true });
}

function encodeGif(got, times, ended, frames) {
  const minimum = 1000 / GIF_FPS;
  const place = (at) => times.place(at) / GIF_PACE;
  const kept = [];
  for (const frame of got) {
    const at = place(frame.at);
    const last = kept.at(-1);
    if (last && at - last.at < minimum) last.file = frame.file;
    else kept.push({ file: frame.file, at });
  }
  while (kept.length && kept[0].at <= 0) kept.shift();
  const finish = place(ended);
  const list = kept
    .map((frame, index) => `file '${frame.file}'\nduration ${(((kept[index + 1]?.at ?? finish) - frame.at) / 1000).toFixed(3)}`)
    .join("\n");
  const listFile = join(frames, "list.txt");
  writeFileSync(listFile, `${list}\nfile '${kept.at(-1).file}'\n`);
  const out = join(HERE, "evalution-demo.gif");
  execFileSync("ffmpeg", [
    "-y", "-loglevel", "error", "-f", "concat", "-safe", "0", "-i", listFile,
    "-vf", "split[a][b];[a]palettegen=max_colors=200:stats_mode=full[p];[b][p]paletteuse=dither=none:diff_mode=rectangle",
    "-fps_mode", "vfr", "-loop", "0", `${out}.tmp.gif`,
  ]);
  renameSync(`${out}.tmp.gif`, out);
  console.log(`evalution-demo.gif: ${(statSync(out).size / 1024 / 1024).toFixed(2)} MB, ${(finish / 1000).toFixed(1)} s, ${kept.length} frames`);
}

const what = process.argv[2] ?? "all";
execFileSync("npx", ["vite", "build"], { cwd: WEB, stdio: "ignore" });
if (what !== "gif") await runStills();
if (what !== "stills") await runGif();
