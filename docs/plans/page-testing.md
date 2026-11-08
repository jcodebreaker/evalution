# Plan: play tests through the app's own page

Today a try reaches the app through its API (`adapters/http.py`, `langgraph.py`, `letta.py`). This plan adds a second look at the same turn: evalution opens the app's real page on its own copy, types and clicks the way a person would, records it, and checks what showed on screen beside what was stored and what reached the model.

Mockup: `docs/mockups/play-through-the-page.html`. Builds on ADR 0009 (isolation found and proven) and `docs/plans/isolation.md`: a page is only ever driven on evalution's own copy. Decision: [ADR 0010](../adr/0010-the-page-is-a-second-observation.md) (proposed).

## What the person gets

- A switch per target: **Plays through: Its API | Its page.**
- A try they can **watch**: a recording of the page on the test copy, with the steps beside it, each a mark on the timeline.
- A verdict that says **which layer broke**: stored (code, from the app), answered (code, from the traces), shown (code, from the page). "Failed on the page, not in memory" is a result an API-only tester can't give.
- Proof kept with the run: screenshots per step, the page's console, and video when the try failed.

## Decisions

1. **The page is a second observation of the same turn, not a replacement for the API.** In v1 a page target always has an inner API adapter. The inner adapter sends, waits and returns the reply that judging reads, exactly as today. The page shows the same conversation to a browser and records what appeared. `Turn.reply` stays the inner adapter's; a new `Turn.shown` is the page's. Every runner rule that reads the reply (`runs/play.py` `_check_the_adapter`, `_still_out`) keeps working unchanged, and "the reply never showed on the page" is the page's finding, never "the adapter read an empty reply".
2. **"Page" is a mode of a target, not a kind of app.** It isn't registered in `adapters.infos()`. A target's config gets a `page:` block, and the `runs` layer wraps the target's adapter: `PageTarget(inner, map)`. Per target only in v1; per test later, if at all.
3. **The agent learns the page once; a fixed script drives it on every try.** No model chooses clicks at run time. In v1 the map is hand-written; the agent writing it comes after v1 has shown its worth.
4. **What it learns is data: the page map.** Roles and accessible names (`button "Send"`), or test ids. No CSS, XPath or coordinates. Saved with the target, versioned and undoable like the understanding (ADR 0005).
5. **Judged by code; the verdict reads layers from check kinds.** `Decider` stays `code | model` (ADR 0007). New check kinds, `shown_contains`, `shown_lacks`, `page_healthy`, are decided by code from the page. The banner's "stored / answered / shown" comes from which kinds passed, not from a new decider.
6. **A broken page map is not an app failure.** It leaves the try unjudged with its own failure kind, `page_map`, and ends the run with that reason after the first try (every try would fail the same way). Never `adapter_broken`, never a fail.
7. **Only on evalution's copy.** Page mode requires a managed footing (`runs/isolated.py`, `Footing.managed`). The map's URLs must start with `{copy_page}`, checked when the map is read; `isolation/isolate.py` `adapter_on` fills it with the copy's address. No page run against a person's own app, ever.
8. **Local and CI only in v1.** Hosted mode has no copies (`docs/hosting.md`), so it has no page mode. Said in the page, not discovered.
9. **Playwright for Python, headless Chromium,** in an optional extra and a separate image tag. The default install and image say what's missing when page mode is chosen.

## What changed after review

A review against the code (2026-11-07) changed the first draft. In short:

| First draft | Now | Why |
|---|---|---|
| The page sends, and its text is the reply | The inner adapter sends; the page's text is `Turn.shown` | `play.py:626` re-labels an empty reply as `Failure.ADAPTER`, and `runner.py:884` ends the run after repeated adapter failures. The headline result would have been "couldn't judge", then a dead run |
| `page` registered as an adapter | A mode of a target, wrapped in `runs` | `AdapterInfo` is per kind of app; the conformance kit checks class-level methods; one adapter per slot per run |
| The adapter reads traces for "done" and checks the copy's URL | The runner keeps the trace watch; the URL rule is map validation plus `adapter_on` | `adapters` sits below `traces` and `isolation` (ADR 0006, `pyproject.toml` import contract) |
| "Done" from spinner + quiet 800 ms + trace | Done is the inner `send` returning; the page then has a bounded wait to catch up | Streams pause past 800 ms on tool calls; background work shows "Queued!" first; the API's completion is a ground truth to measure against |
| Source tags `page / code / trace / model` | Check kinds; `Decider` unchanged | `Decider` is `code | model` (`core/judgement.py:20`) and feeds `rules.py`; ADR 0007 |
| `{person_email}` / `{person_password}` | Seeded accounts per slot, from the copy's setup | No credential exists for a test person; `Person` is name, language, time zone |
| Agent learns the page in v1 | Hand-written map in v1; agent later | Most of the value without the largest phase |
| Files under `<data_dir>/runs/...`, object store when hosted | A `Files` store in `storage/`, deleted with the run; no hosted page mode | Results live in the database; nothing deletes run folders; hosted has no object store or copies |
| No ADR | ADR 0010 before code | Changes the Target protocol, the evidence model and the judgement rules |

## Decided while building (2026-11-08)

- **`watch`** joined the Target contract: an optional `Target.watch(session, since, *, deadline)`, declared by `Capabilities.watch`. It waits on a turn something else sent, with `send`'s semantics. The http adapter declares it with `done: {poll}`; the `relay` adapter (in the data folder) declares it on an evalution that knows it. Without it, the page watches and the inner adapter sends.
- **Sign-in by headers** (`sign_in: {headers: …}`), sent only to the copy's origin, with values from templates (`{person.email}`, `{secret:NAME}`), never literal secrets. A form sign-in reads seeded accounts.
- **`page_map_broken`** ends a run after one try; `page_map` and `no_reply` are new reasons a try can't be judged.
- **Page checks guide by default.** A test's `page: {decides: true}` makes `shown` and `page_health` must-checks; `page: {contains, lacks}` add its own. `RULES_VERSION` is 3.
- **Files** are kept per try under `<data_dir>/files/<run>/<case>.<n>/`, deleted with the run (`RunStore.delete_run`, no route yet). Uploaded runs keep the names, not the files.
- **Video** (`page.video: failed | always | never`, default `failed`) needs Playwright's ffmpeg (`playwright install ffmpeg`); without it a try plays on and says why it has none. Each context records at the viewport's size; a form sign-in happens on a page of its own, closed before the try's page opens, so no video shows the form. `PageTarget.record` closes the try's page to finish its video and keeps it as `try.webm` (over 50 MB it is dropped, and said); the next turn in the session opens a new page. With `failed`, the runner deletes a passed try's video once judged; the evidence keeps the name and `video_policy`, and the try view says "kept only when a try fails". A try cut off by a restart or a stop before `record` has no video.
- **Installed with evalution.** `./install.sh` installs the page extra, Playwright's Chromium and ffmpeg (skipped with `EVALUTION_NO_BROWSER=1`); `evalution doctor` says what UI testing lacks, without launching anything, and fails only when a target plays through its page; `docker build --target page` is an image with all of it, the default image stays without a browser.

## Where it stands

| Part | Where | State |
|---|---|---|
| Driving a real page and recording it | `docs/demo/capture.mjs` | Works, in Node, for evalution's own page |
| A made-up app and model gateway | `server/tests/acceptance/made_up_app.py` | Works, API only, no page |
| The adapter contract | `adapters/base.py` | Stable. Gains `Turn.shown` |
| Copies with their own address | `isolation/launch.py` `_copy_url`, `isolate.py` `adapter_on` | In progress (isolation plan phases 3 to 5). Remaps only the published API port today |
| Tries linked to spans | `X-Evalution-Run`/`-Try` headers, `trace_attribute` | Works for API adapters; the page's own requests carry nothing yet |
| Check kinds decided by code | `judging/checks.py`, `core/rules.py` | Works; gets `shown_*`, `page_healthy` |
| Masking secrets in logs | `isolation/launch.py` `secrets_of`, `scrub` | Works; reused for console and network text |
| Large files kept with a run | | None. Evidence text items are capped at 64 KB (`core/evidence.py`) |

## The page map (v1)

A `page:` block in the target's config. Hand-written in v1.

```yaml
page:
  url: "{copy_page}/chat"               # the copy's page address; anything else is refused
  ready: {role: textbox, name: "Message"}
  sign_in:                              # none | cookie | form
    form:
      url: "{copy_page}/login"
      fill:
        - {role: textbox, name: "Email", value: "{account.email}"}
        - {role: textbox, name: "Password", value: "{account.password}"}
      submit: {role: button, name: "Sign in"}
      done: {url_matches: "/chat"}
  accounts: seed                        # one seeded account per slot, from the copy's setup
  open_session:                         # how the page shows the conversation the inner adapter opened
    url: "{copy_page}/chat/{session}"   # or: pick it from a list, by title
  actions:
    new_chat: {click: {role: button, name: "New chat"}}
    type:     {fill: {role: textbox, name: "Message"}}
    send:     {click: {role: button, name: "Send"}}
  replies:
    in: {role: log, name: "Messages"}
    item: {role: article}
    mine: {attr: data-role, equals: assistant}
  busy:                                 # the page is still working while any of these hold
    - {role: button, name: "Stop"}
    - {attr: aria-busy, equals: "true"}
  catch_up_seconds: 10                  # after the inner send returns, how long the page gets to show it
```

### How a turn plays

There are two ways to make the page and the inner adapter see the same turn, and phase 0 decides which:

- **Page sends (preferred):** the page types and clicks Send; the inner adapter only *waits* on that session (poll, or its done rule) and reads the reply. The recording shows a real person's path. Needs the inner adapter to wait on a session it didn't send to, which `http.py`'s `done.poll` already does for background work.
- **Inner sends, page watches:** the inner adapter sends as today; the page has the conversation open and records the reply arriving. Simpler and always possible, but the page never typed anything: it proves display, not input.

Either way: the turn is done when the inner adapter says so (it already honours `background`, `cancel`, `heard`, `lost`, deadlines). Then the page gets `catch_up_seconds` for `busy` to clear and a reply to appear; `Turn.shown` is what it shows then, or `None` with what it showed instead.

## Phases

Each ships on its own and leaves `main` working.

### Phase 0: spike on relay-lite (throwaway)

A script outside the package, on `eval-relay-lite-0`, hand-written map.

1. **Topology:** where relay-lite's page is served (same origin as the API? a Vite port? a sidecar?), and whether the copy's override remaps it.
2. **Test person:** how an account exists on the copy. Seed one per slot in the copy's setup, or start the copy with a test-auth setting. What a new person per try would take (sign-up through the page?).
3. **Same session:** can the page open the conversation the inner adapter made (URL, list), or can the inner adapter wait on one the page made? Decides "page sends" or "page watches".
4. **Trace linking:** do headers added with `page.route` reach the app on fetch, SSE and WebSocket? (Playwright's `extra_http_headers` may not apply to WebSocket handshakes; check.) If none, can the page's session id be read and matched to `session.id` on spans?
5. **Catch-up:** over 50 turns, how long after the inner `send` returns does the reply show? Ground truth is the API's completion. Count turns where the page showed a partial or wrong reply at `catch_up_seconds`.
6. **Cost:** time per try against the API path, memory per browser context, screenshot and video sizes.

**Done when:** each question has an answer written into this plan, and 50 turns in a row show the same text on the page as the API returned.

#### What phase 0 found (2026-11-08, on `eval-relay-lite-0`)

A Node script with Playwright and the system Chrome, against evalution's copy (never the live stacks). 5 turns, not 50: each one is a real model call. The scripts aren't kept; the numbers are.

| Question | Answer |
|---|---|
| 1. Topology | One origin. The engine serves the web and the API on the same container port, and the copy publishes it once (`127.0.0.1:51146`). For relay-lite, `{copy_page}` is the API's URL. |
| 2. Test person | **No sign-in form: identity is a header.** Relay-lite takes `X-Forwarded-Email` with `X-Atrium-Proxy-Secret`, the way the `relay` adapter already does. A browser route on the copy's origin adds both to every request (53 out of 53 on load), and the page plays as the eval person (`/v1/me` gave `eval+…@relay.local`). So a new person per try is free, with no seeded accounts. New `sign_in` kind: `headers`, sent only to the copy's origin. |
| 3. Same session | **Yes, without trying.** Relay-lite gives each person exactly one Conversation; the page opened at `/app/<conversation id>`, the same id the API's `/v1/me/assistant` gives. **Page sends works:** the page typed and clicked Send, and the adapter's own way of finding the new Round (`_new_round`: Rounds started since the message was sent) found the page's Round every time. |
| 4. Trace linking | No WebSocket and no EventSource: every request is `fetch` (39 of 53), so headers added by the route reach everything, `X-Evalution-Run`/`-Try` included. Spans carry `session.id` = the Conversation id, shared by the page and the API. |
| 5. Catch-up | The page showed the API's reply within one 250 ms sample of the Round settling, 5 out of 5. Stop appeared 0.07 to 0.22 s after Send and was gone when the Round settled. The page matched the API's words every time after normalising. |
| 6. Cost | 1.2 to 2 s per turn once warm (13 s for the first, which starts the person's host); 28 s for page load plus 5 turns. A screenshot is about 90 KB. Video needs Playwright's own ffmpeg build, a separate download: the image must carry it. |

What it changes:

1. **Page sends.** The inner adapter needs a way to wait on a turn it didn't send: an optional `Target.watch(session, since, deadline) -> Turn`, declared by a `watch` capability. The `relay` adapter's `send` already splits into "post" and "wait for the Round", so `watch` is its second half. Without `watch`, the page target falls back to "page watches" (the inner adapter sends).
2. **Replies have no roles of their own.** Relay-lite's messages are paragraphs inside `main`, with no `log` and no `article`. Finding "the newest reply element" by role won't work here. The `shown` check becomes: **the adapter's reply text appears in the conversation region after the turn**, normalised. `replies.item` and `replies.mine` become optional.
3. **Busy is "Stop" in the composer.** `busy: [{role: button, name: "Stop"}]` held on every turn. Matches the plan.
4. **A real finding on the first load:** `GET /v1/me/assistant` returned **500** once for a brand-new eval person, and the page logged it as a console error, though the page then worked. Every first load of a new person would fail `page_healthy`. Either relay-lite has a race when it registers a person (worth reporting), or `page_healthy` needs a way to say "known, ignore". It shouldn't be silenced by default.
5. **Video** needs ffmpeg in the image, not only Chromium.

### Phase 1: ADR 0010 and the seam

1. ADR 0010 accepted (drafted: `docs/adr/0010-the-page-is-a-second-observation.md`).
2. `Turn.shown: str | None` and `TurnRecord.shown`, with an upcast in `results/migrate.py`. Every adapter today leaves it `None`; nothing reads it yet.
3. `Failure.PAGE_MAP` with its label in `judging/checks.py` `_FAILURE` and `core/rules.py` `_UNJUDGED_LABEL`; the runner ends a run on it after one try, with its own reason.

**Done when:** the ADR is accepted, the full test suite passes with the new fields, and an old run's evidence still reads.

### Phase 2: the page map as data

`runs/page/map.py` (in `runs`, which may import `adapters`, `isolation` and `traces`).

1. `PageMap` (pydantic): the fields above, `status` (`draft`, `tried`, `approved`, `stale`, `broken`), evidence per entry.
2. Validation: every URL starts with `{copy_page}`; every locator is a role with a name, or a test id.
3. Read from the target's `page:` block; versioned with the target.

**Done when:** a map pointing outside the copy is refused with a test, and a valid one round-trips.

### Phase 3: the page target

`runs/page/target.py`.

1. `PageTarget(inner, map)`, composed by the runner when the target has a `page:` block and its footing is managed; refused with a plain reason otherwise.
2. `capabilities`: the inner adapter's, as an instance attribute (as `http.py:215` already does). `conversation_break` is the inner adapter's: it starts the new conversation and the page opens it, so New chat needn't be mapped. Never `files` or `stop` in v1.
3. One browser per copy (as `adapter_on` builds one adapter per copy, at most `MOST_COPIES`); a new browser context per try, so cookies, storage, IndexedDB and service workers never carry over.
4. `open`: the inner `open`, then a context, sign in with the slot's seeded account, open the session's page, wait for `ready`.
5. `send`: as phase 0 chose ("page sends" or "page watches"). Read the send request's response through `page.on("response")` and reuse `failure_of_status` and `status_may_have_passed_on`, so `heard`, `lost`, `REFUSED`, `TRANSIENT` mean what they do today. An element not found is `PAGE_MAP`.
6. `break_conversation`: the inner one, then open the new session's page (or `new_chat` when the page sends).
7. `close` and `cancel`: work from `Session.extra` alone (the context id and the recording folder), since they're called after a restart with no browser (`adapters/base.py`, the `close` notes). A context that's gone is a no-op that still marks the folder as interrupted.
8. Cases with a `fresh` turn are refused in page mode: there's nowhere on a page to carry the token unseen.
9. `persona`: the browser's `locale` and `timezone_id` follow the test person's language and time zone, on top of what the inner adapter honours.
10. Its own conformance module, run over each inner adapter, checking instance capabilities against what it delegates.

**Done when:** a library case plays on the made-up app's page (phase 6's testing page) with the same verdict as through its API when both are right, and a try stopped mid-turn is closed cleanly after a restart.

### Phase 4: page evidence and its store

1. `PageRecord` on `TryEvidence`: per turn, `shown`, what showed instead, the time it showed; console errors and warnings; failed requests to the copy; the step times. Text items go through `capped()`.
2. A `Files` store in `storage/` (a folder now, behind an interface): screenshots per step, written as they're taken; video on failure (or always, by a setting), finalised on context close. A crash leaves the screenshots and a step log; the video may be missing and the try says so.
3. Retention: deleting a run deletes its files. `results/store.py` gets the delete path it doesn't have.
4. `run --server`: uploads screenshots and console; video only by choice, within the import cap (`api/deps.py`, 32 MiB); a run whose video stayed behind says where.
5. Secrets: `scrub` over console and network text; the sign-in form masked in screenshots; no Playwright `trace.zip` kept (it holds bodies and cookies); a URL with a token in it masked. Seeded accounts are fictional, and the page says so.

**Done when:** a failed try's screenshots, console and video are kept and shown; deleting the run removes them; an uploaded run carries its screenshots.

### Phase 5: page checks and the verdict

1. `CheckKind.SHOWN` (`shown_contains`, `shown_lacks`) and `CheckKind.PAGE_HEALTH` (no console errors, no failed requests to the copy), decided by code. `EvidenceRef` gets `page:<turn>` and `shot:<n>`.
2. `reply_contains`, the rubric and the context checks keep reading `Turn.reply`, the inner adapter's text. Rendered text (collapsed whitespace, Markdown gone, emoji) never feeds them.
3. A page target gets one `shown` check per turn by default: the reply showed, and its text matches the inner reply after normalising. A test may add `shown_*` checks of its own, and decides whether page checks count toward its outcome, as tool calls already do.
4. The banner reads layers from check kinds: stored (state checks), answered (reply and context checks), shown (`SHOWN`, `PAGE_HEALTH`). "Through the API alone, this try would have passed" is said only when the non-page checks passed in the same try.
5. `RULES_VERSION` bumped (ADR 0007); older results marked as decided by the old rules.

**Done when:** the mockup's try 3 happens for real on the made-up app with a broken page (stored ✓, answered ✓, shown ✕), and the same case without the page passes.

### Phase 6: the made-up app gets a page

`tests/acceptance/made_up_app.py` serves a small static chat page (HTML and JS, no Node): sign-in, input, Send, streaming replies over SSE, New chat, a Stop button while busy. Switches break it: rename Send, never clear busy, throw on a reply shape, show an error after the text, drop a reply. Acceptance tests play cases through it in CI with headless Chromium.

Written alongside phase 3, since phase 3's tests need it.

**Done when:** each switch gives the result it should: a broken map, a failed `shown`, a failed `page_healthy`, and a pass with the switches off.

### Phase 7: the page in the app (v1 ends here)

1. Target settings: "Plays through: Its API | Its page", enabled only with a managed footing and the extra installed, and saying why when it isn't.
2. The try view from the mockup: screenshots per step (video when kept), steps that jump to their screenshot or moment, the layered banner, Console.
3. A broken map on Home, like a stale plan, with what wasn't found.
4. The run dialog's estimate uses phase 0's numbers.
5. Mockup corrections: "Reply showed, and it names Biscuit" is decided by code (`shown`), not judged by a model; "Before … checked empty" is the copy's restore check, labelled so; jumps to a moment in the video are approximate.

**Done when:** v1 has found one real "shown ✕, answered ✓" on relay-lite.

### After v1

- **The agent writes the map** (from the first draft's phase 6): read the frontend code, look at the copy's accessibility snapshot, try one entry at a time through typed tools, propose with evidence, at most 3 revisions, one approval. The map's evidence files join the claim links, so a commit marks it stale and it's tried again on the copy before the next run. **Built (2026-11-08)**, as below.

#### How the agent learns the map (2026-11-08)

- **Five typed tools in the writer's loop** (`writer/paging.py`, acting through `runs/page/learn.py`): `read_page_code` (candidates per entry from the code folder, with `file:line`, and the page's routes), `look_at_page` (an accessibility snapshot, signed in as a proposed `sign_in`), `try_page_entry` (one entry, locating only: the message box takes a fixed harmless text and is cleared, Send, New chat and Stop are found and never clicked, and the browser refuses any request but a read while it looks), `propose_page_map` (checked as a run reads it; ready, the message box, Send and the replies each rest on a cited file and line or a try this session that found them) and `rehearse_page_map` (one real message, "Hello, this is a test.", through the whole map). Each starts evalution's own copy from the proven isolation plan and takes it down after; without one, every tool is refused in plain words. Not while a run plays: they hold the run lock.
- **Two yeses, as with tests.** The admin approves a proposal in the chat ("Approve and try it"), since rehearsing spends one turn of the app. A map whose reply showed is kept as `tried` with its screenshots (`page-maps/<target>/proposed.json`, screenshots in the file store) and approved once under **How tests reach <target>** on the Test environment page, which saves it into the target's `page:` block (status `approved`). A failed rehearsal is evidence for a revision; after 3 failed revisions the admin is asked.
- **Versions and undo** like the isolation plan: every approved map, and each change of its status, is a version (`page-maps/<target>/versions/`). Undo goes back to the map before, or to no `page:` block (its API) when there was none. A map written by hand before is kept as the version an approved one replaces. A target set in `evalution.yaml` is never written: approving says to add the block there.
- **Kept current like a claim.** The map's evidence files and their hashes are kept with it. A commit touching one marks it `stale` (the background check, as for the isolation plan), and Home says so. Before a run plays through the page, a stale or broken map, or one whose evidence changed, is checked on the copy: every element a turn needs is found, none is acted on, no message is sent. Found: approved again. Not found: `broken`, saying what's missing, and the run ends `page_map_broken` with no try played and a proposal to re-learn the page.
- **Only while the block is the map as approved.** A `page:` block edited by hand afterwards is the person's: nothing marks or checks it. Editing a connected target in the form keeps its `page:` and `page_accounts`.
- **Page-only targets** (no inner adapter): reply-only and labelled so, done by page signals (`busy` clearing, Stop turning back to Send) with quiet time as a tiebreaker, no `probe`, no state checks. **Built (2026-11-08)**, as below.

#### A target reached through its page alone (2026-11-08)

Decision: ADR 0010, "Amended 2026-11-08".

```yaml
targets:
  chat:
    adapter: page                        # no adapter: a stand-in outside a run, no INFO
    page:
      url: "{copy_page}/chat"
      session_id: {from: url, pattern: "/chat/(?P<id>[\\w-]+)"}   # optional
      quiet_ms: 800                      # replies unchanged this long, nothing busy: done
      ready: …, sign_in: …, actions: {type, send, new_chat?}, replies: {in, item?, mine?}, busy: […]
    page_accounts: […]                   # for a form or cookie sign-in, as before
```

- **Plays:** `PageTarget(None, map)` on the copy. Open: new context, sign in, `url`, New chat when mapped, the id from the address when `session_id` says. Send: type, Send, the copy answers the message's request; then busy gone and replies quiet for `quiet_ms` (busy first), the page given `catch_up_seconds` to start. Reply: the newest `mine` item, else the replies after the person's message. `Turn.reply` = `Turn.shown`.
- **Can:** managed isolation, New chat when mapped, language and time zone. **Can't:** reset (the copy's restore is the reset), state, probe, files, Stop, watch, sections, parallel (page targets stall side by side on relay-lite: one slot).
- **Says so:** `PageRecord.page_only`, the run's `plays: page_only`, "Plays through: Its page only", "a pass is reply only", never "through the API alone".
- **Fails on the page, never the adapter:** a page that settled with no reply is `app` (the try fails, `shown` fails), "answered, not shown" when the traces show the agent answered; busy at the deadline is out of time. The run's first check only warns of it.
- **Tests:** `tests/test_page_only.py` (browser: reply read from the page, busy then quiet, busy forever → out of time, nothing shown → the app's failure, renamed Send → page map, the id from the URL or evalution's own), `tests/test_page_only_contract.py` (no browser: capabilities, the stand-in, map reading, refusals, the adapter rule) and `tests/acceptance/test_page_only_runs.py` (the made-up app's page alone: a reply-only pass, `drop_reply` a fail with "answered, not shown").
- **Not yet:** an isolation plan's rehearsal sends through an adapter, so a page-only target's plan is proven by hand for now.
- **A new person per try** through the page, when sign-up can be mapped.
- **Per-test page mode**, if per-target proves too coarse.

## Testing

- **The seam:** every existing adapter and run passes with `Turn.shown` present and `None`; old evidence upcasts.
- **The made-up app's page** (phase 6), in CI, with no model: the made-up gateway answers.
- **Refusals:** a map URL outside the copy; page mode without a managed footing; a `fresh` turn in page mode; each with a test.
- **Restart:** a page try interrupted mid-turn; `recover()` closes it from `Session` fields alone.
- **CI cost:** Chromium cached; the page job runs when `runs/page/`, the made-up app or `web/` change, and nightly.

## Order and size

0 first, alone. Then 1. 2 and 6 side by side. 3 after 1 and 2, with 6. 4 and 5 after 3. 7 last. Real projects need isolation phases 3 to 5 (the copy and its address) first; until then, the made-up app only.

Roughly: 0 is days, 1 small, 2 small, 3 the bulk, 4 medium, 5 medium, 6 small, 7 medium.

## Risks

| Risk | What we do |
|---|---|
| The page and the API see different conversations | Phase 0, question 3; v1 can always fall back to "page watches" |
| The page shows a reply late or partly | `catch_up_seconds`, measured against the API in phase 0; the shown text recorded with its time |
| The page's requests carry no try headers (WebSocket) | Inner adapter's spans link the try as today; the page's own requests matter only for `page_healthy` |
| Sign-in differs per app | Seeded accounts or test auth on the copy; SSO, magic links and captchas out of scope, and said |
| A flaky page hides a memory bug | Layers judged separately; `shown` never changes what `reply` decided |
| Recordings are big | Screenshots always, video on failure; capped per try |
| Chromium in the image | Optional extra and image tag; the default says what's missing |
| Interrupted tries lose their video | Screenshots and the step log written as they go; the try says the video is missing |
| Secrets in recordings | `scrub`, masked sign-in, no `trace.zip`, fictional accounts |
| The page writes state the isolation plan doesn't cover | A new browser context per try; server-side stores stay the plan's job |
| `watch` takes the previous turn's reply as this one's | The page waits for the copy to answer the request carrying the message before `watch` starts; http's `watch` doesn't accept a done state it never saw move. Known limit: on an http target, a page-sent reply word for word the same as the one before, that finishes before the first poll, times out instead of passing |
| A repeated reply ("Ok." twice) matches the old one on screen | Catch-up needs the reply to appear more times than before the turn, and takes the newest |
| **Open:** tries played side by side through relay-lite's page stall | Seen 2026-11-08: with 3 slots, the page sent each message and relay-lite showed it, but no Round started and every try ran out of time. The same 4 tries passed one at a time through the page, and side by side through the API. The page also opens a WebSocket (`/v1/hosts/…/tunnel`) that phase 0 didn't see, which route headers may not reach. A later parallel run caught the cause on the page itself: relay-lite answered the page's `POST /v1/sessions/…/events` with **503** after about two minutes, while new people's hosts were starting side by side. Through the API the same 503 comes back sooner and the runner retries it. The fault is relay-lite's under parallel host starts. Until it's fixed there, a page target plays one slot at a time (`parallel=False`) |

## Open questions

1. "Page sends" or "page watches" as the default, once phase 0 shows both are possible?
2. Accessibility findings come for free (an unnamed button): say them, or keep quiet unless asked?
3. One viewport per target, or mobile too?
4. Electron apps through Playwright's `_electron`: worth a phase after v1?
