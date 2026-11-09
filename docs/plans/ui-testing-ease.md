# Plan: UI testing you can't miss

Page testing works (ADR 0010, `docs/plans/page-testing.md`), but a developer can use evalution for a week and never learn it exists. Today it shows in two places: a card on the Test environment screen and one line under Connection. The run dialog, the test list, Home and Connect say nothing; the browser it needs is checked by `evalution doctor` in a terminal; and when something goes wrong the page doesn't always say what to do next.

The rule for everything below: **a developer should never wonder whether it's supported, where to start it, or what will happen when they press Run.** Every screen that touches a test says how it will be played, and every problem ends in one button that fixes it.

## The five

### 1. Tell them it exists, where they already are

- **Connect** reads the code already. When it finds a web frontend (a chat page: an input, a send button, routes), the last step says: *"relay-lite has a page. Test it the way people use it?"* with **Set up UI testing** and **Not now**.
- **Home** shows the same offer as a card once, for a target with a frontend and no page map, until it's set up or dismissed.
- **The test list** gets a small badge on each test: **Page** or **API**, so it's always visible how a test plays.

Done when: connecting the made-up app (which has a chat page) offers UI testing without the person looking for it, and dismissing it keeps it dismissed.

### 2. Set it up in the page, in one flow, with nothing to install by hand

- **Readiness in the page, not the terminal.** Settings → *UI testing*: Ready ✓, or *Needs a browser* with an **Install** button that runs the same install `./install.sh` does, with progress. The doctor's finding and this panel read the same `page_parts()`.
- **One flow:** Set up UI testing → the agent learns the page (shown as live steps, like isolation's) → **a short video of the rehearsal** → **Approve**. No YAML anywhere. A target from `evalution.yaml` gets its approved map stored beside it, not refused.

Done when: on a machine with no browser, a person goes from "Set up UI testing" to an approved map without opening a terminal.

### 3. Say what will happen before it happens

- **The Run dialog** says per test how it plays and what it keeps: *"3 tests through relay-lite's page, one at a time, about 40 s each; screenshots of every message, video when a try fails."* And the same for API tests.
- **Proof, as a setting you can see.** Screenshots and video, each **always / on failure / off**: per target on the Test environment card, per test under "What it checks". API tests don't show it (nothing to capture).
- **A test's "What it checks"** says *"Played through the page: types each message, checks it shows"* or *"Played through the API"*.

Done when: before pressing Run, the dialog says how each test plays, what's kept, and roughly how long; changing proof on a test changes what that dialog says.

### 4. Every failure ends in one next step

- **Blame the right layer.** The app refusing the page's message (a 503) or not answering it is the app's, under *Answered*, not "Failed on the page". A slow or refused message request gets its own short limit and is retried like the API path, instead of waiting out the whole try.
- **A broken page map** shows the screenshot of what the page looked like, names what wasn't found ("no button named Send"), and offers **Re-learn the page**.
- **Missing pieces** (no browser, no copy, no ffmpeg) each say what's missing and offer the button that fixes it, never a command to copy.

Done when: each failure kind seen during building (503 on the message, renamed Send, no browser, no copy) shows a plain sentence and one action, checked on the made-up app's switches.

### 5. Show it working in a minute

- **`evalution demo ui`** (and a **Try the demo** link where UI testing is offered): starts the made-up app with its chat page as a target on a copy, plays two tests through its page (one passes; one with a broken page fails on the page), and opens the result: pills, screenshots, video. It spends nothing: the made-up app's gateway answers.
- **A one-page guide**, *UI testing in five minutes* ([docs/ui-testing.md](../ui-testing.md)), linked from each of the places above.

Done when: on a fresh install, `evalution demo ui` ends with the browser open on a page-tested failure, with its video, in under two minutes.

Built (2026-11-08): `evalution demo ui [--no-open]` (`server/evalution/demo/ui.py`). The made-up app moved into the package as `evalution.demo.app` (`python -m evalution.demo.app PORT`), and the acceptance tests start that one. In a new temporary folder (deleted on Ctrl-C), it sets up `demo-chat`, the made-up app's API under its page on a copy started from a plan saved as proven, with two tests: *Answer shown* passes; in *Remembers the dog's name* the page drops the reply to "What did I tell you?" (`DROP_REPLY_TO`, a per-message form of `drop_reply`, so both play on one copy in one run): answered ✓, shown ✕. It plays them through an `evalution serve` of that folder on a free port, prints and opens the failing test's result, and keeps serving until Ctrl-C. No model is set up, every check is code, so nothing is spent; the failed try therefore has no written analysis. About 30 s here. The **Try the demo** link where UI testing is offered waits on 1.

## Order

1 and 3 first, together: they're mostly words in places that exist, and they answer "is it supported?" and "what will happen?". Then 4, which builds on fixes already planned. 2 and 5 are the larger pieces.

## Not here

- The parallel stall on relay-lite (a 503 from the app when hosts start side by side) is the app's; page runs play one at a time until it's fixed there.
- Per-test page mode and a new person per try through sign-up stay as `page-testing.md` leaves them.

## Decided while building 1 and 3 (2026-11-08)

- **The offer** reads the code as the agent's `read_page_code` does (`writer/paging.chat_page`): a message box and a Send in the code folder. It is made only where tries keep files (locally, CI), and not for a target with a page map or one waiting for approval. "Not now" is stored with the target (`ui_offer_dismissed` in its connection doc), not in the browser. "Set up UI testing" hands the agent the page to learn when the copy is proven, else opens the Test environment page.
- **Proof is the page map's `proof: {screenshots, video}`**, each `always | failed | off` (default `always`, `failed`). The older `video: failed | always | never` still reads, as `proof.video` (`never` is `off`); when both are given, `proof` wins, and saving proof from the page drops `video`. Proof is a setting beside the map: changing it doesn't make an approved map "by hand", and approving a new map keeps it. A test's `page.proof` wins part by part; the runner sets it on the slot's page target before opening the try's session (a context records video, or not, when it opens). A passed try's screenshots kept "on failure" are deleted once judged, as its video is; `PageRecord.screenshot_policy` says what the try kept, and the try view says why there are none.
- **The Run dialog's lines** come from the server (`runs/estimate.plays`): grouped by how tests play and what a try keeps, with "about N s a try" from each test's own recent tries, else its messages at the median a message took, else 30 s each.
- Setting a test's proof rewrites its file, so a comparison across that edit says "the test was edited", though nothing it checks changed.

## Decided while building 2 (2026-11-08)

- **Install from the page** runs on the computer the server runs on, one job at a time (`POST /v1/settings/ui-testing/install`, polled as `job` on `GET /v1/settings/ui-testing`, a step per piece and the last lines printed, like isolation's live steps). Only fixed commands, built from the server's own paths, only for what `page_parts()` finds missing: `uv sync --project <server> --extra page --inexact` (only when evalution runs from its checkout under uv, its `.venv` being the running Python; `--inexact` leaves every other package; otherwise the page says to install the `page` extra the way evalution was installed), `<python> -m playwright install chromium`, `<python> -m playwright install ffmpeg`. Refused hosted, during a run, and while the agent is learning a page. `MissingPieceNote` shows **Install** with the progress where the server can; the command stays under "Or run it yourself".
- **One flow, on the card, no chat.** "Set up UI testing" (the offer card and How tests reach, "Re-learn the page" too) starts a server job (`POST /targets/{t}/page/learn`, `confirm: true`) that opens a writer session of its own and asks the agent to learn the page, exactly as the chat does: the agent still reads the code, looks, tries and proposes. The card follows its tool calls as steps; when the agent shows a map and ends its turn, the job approves the rehearsal's one message as the chat's button would. Pressing Set up is that yes, said on the card before anything starts (as isolation's `confirm`). The job ends when a tried map waits; the card then shows its screenshots, **its video** and **Approve**. The person still approves the map itself once. The session stays among the app's chats. The Home card picks up a setup underway (`underway` on the offer).
- **The rehearsal keeps its video**, passed or not, when ffmpeg is here: a person approves by it. (A run's video policy is unchanged.)
- **A target from evalution.yaml** gets its approved map stored in the data folder (`settings` document `page-maps/<target>`), merged in as its `page:` when the file's block has none, so `page_map_of`, runs and the CLI read it; proof and undo write there too. The file's own `page:` wins and is never written: approving over it is refused with why.
- **The offer finds a frontend the code settings leave out** (relay-lite's `include` names only its prompts): `chat_page` also looks, read-only and bounded (400 files), under `web/`, `frontend/`, `client/`, `app/`, `src/` for `.tsx/.jsx/.vue/.svelte/.html`. What the agent reads and cites as evidence is unchanged: `read_page_code` says the page's code is left out, and the agent learns from the page with tries, which are evidence.

## Decided while building 4: a run's rows and a failed try (2026-11-08)

- **A row says which layer held**, from the server: a run's `CaseRow` carries the `layers` of the try its test's page opens on (the first that didn't pass, else the first) and `plays`, `page` when a try has the page's checks. A page row reads `Stored ✓ · Answered ? · Shown ✕ · <reason>`, without the label its ✕ already says; Page / API marks each played row once any test of the run played through the page.
- **A failed `page_health` reads whose it is, not the log line** (`rules.page_health_words`, used by `first_reason`, so rows, the banner and the tests list say the same): a 5xx is "the app answered a request from the page with an error, 503", a 4xx "turned down", no status "got no answer", console errors alone "the page logged an error". It reads the check's stored `why`, so older runs read the same way; the check keeps its detail, and the try's Console and failed requests keep the lines.
- **Page healthy is a pill of its own**: the try view's page gives `shown_alone` (`layers(..., health=False)`), so Shown is what showed of the replies and Page healthy the page's health. `layers()` itself is unchanged.
- **The app never answered**: a page try stored before `not_taken` was kept (or one that ran out of time on the message) that never reached the assistant, has no reply and whose answered layer failed or couldn't be judged, is the app's: "The app never answered the message", never "Failed check".
- **Side by side and one next step**: a page try that didn't pass shows its screenshot and video beside the reply, with "Shown on the page: … · Missing: …" from `rendered.shown_part` (the reply's words the page showed in order, and from the first it didn't). It ends in one sentence and one button that runs the test again: the app's (refused, didn't take, never answered) or the page's (dropped part of a right reply; or every reply showed and its health failed). A broken map keeps its note and Re-learn the page. No browser, no copy and no ffmpeg stop a run before any try, so they end in the Run dialog's note, not here.
