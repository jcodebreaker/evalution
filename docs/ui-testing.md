# UI testing in five minutes

## What it is

UI testing plays a test through your app's own page, the way a person uses it: evalution opens the page in a browser on its own copy of your app, types each message, presses Send, and checks what showed on screen beside what was stored and what reached the model. So a result can say which layer broke: a reply your app stored and answered but the page never showed fails on the page (**Answered ✓, Shown ✕**), a result a tester that only calls the API can't give. Every turn keeps a screenshot, and a failed try keeps a video. Nothing runs against your own app: the page is only ever driven on evalution's copy ([ADR 0010](adr/0010-the-page-is-a-second-observation.md)).

## Try the demo

```sh
uv run --project server evalution demo ui
```

In about half a minute it plays two tests through a made-up chat app's page, on a copy it starts itself, and opens the failing one's result in your browser: one test passes with its answer shown, and in the other the page drops a reply the app did give (a switch in the made-up app, on for that one question). It spends nothing: the made-up app answers by itself and every check is decided by code, so no model is called. Everything lives in a scratch folder of its own, never with your targets or runs, and Ctrl-C stops it and deletes that folder. `--no-open` prints the address instead of opening it.

It needs the page extra and a browser. `./install.sh` sets them up; if they're missing, the demo says so and gives the one command: `uv sync --project server --extra page && uv run --project server playwright install chromium ffmpeg`.

## Set it up for your app

1. **Connect your app and prove isolation** first (README, Quick start, steps 2 to 4): the page is only ever played on evalution's copy, so the target needs a proven isolation plan.
2. **Test environment → How tests reach *your app* → Its page → Learn the page.** The test writer reads your frontend code and looks at the copy's page, tries each part it needs (the message box, Send, where replies show) without sending anything, and proposes a page map with the file and line each part rests on.
3. **Approve and try it** in the chat: one real message, "Hello, this is a test.", goes through the whole map on the copy, with screenshots.
4. **Approve** on the card. From then on the target's tests play through its page. **Undo** goes back to the map before, or to its API.

A map is kept current like everything else evalution learns: a commit that touches a file it rests on marks it stale, and the next run checks it on the copy before playing. A page that changed so the map no longer finds Send ends the run after one try with what's missing and **Re-learn the page**, never a failed test.

## What you see after a run

- **Pills** on each try: **Stored**, **Answered**, **Shown**, each ✓ or ✕ (a layer the test doesn't check isn't shown). A headline names a page-only failure: "Failed on the page, not in the answer", and, when every other check held, "Through the API alone, this try would have passed."
- **Screenshots** of every turn, beside its step.
- **Video** of the try, kept when it fails (Playwright's ffmpeg records it; without it the try plays on and says it has none).
- **Console and failed requests**: what the page logged, and whether it stayed healthy.

## Proof settings

- What a try keeps is the page map's `proof`: `screenshots` (one after each message) and `video`, each `always`, `failed` (kept only when a try fails) or `off`. By default every screenshot is kept, and the video only when a try fails. Set it per target on the **How tests reach** card, or per test under **What it checks** (its `page.proof`, which wins). The older `video` setting still reads, as `proof.video`. Over 50 MB a video is dropped, and the try says so.
- Whether the page decides: page checks guide by default. A test's `"page": {"decides": true}` makes "the reply showed" and the page's health must-checks; `"page": {"contains": [...], "lacks": [...]}` adds checks of its own ([case format](case-format.md)).

## Limits

- **Local and CI only.** A hosted evalution has no copies of apps, so it has no UI testing.
- **One try at a time.** Page tries played side by side stalled on one real app, so a target played through its page uses one copy.
- **Sign-in kinds:** none, a form with seeded test accounts (one per copy), a cookie, or headers sent only to the copy. Single sign-on, magic links and captchas aren't covered.
- One viewport (1280 × 720), Chromium only.

More: [the plan and what was decided while building it](plans/page-testing.md), and [what makes it easy to find](plans/ui-testing-ease.md).
