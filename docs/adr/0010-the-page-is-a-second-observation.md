# 0010: The page is a second observation of the same turn

> **Status:** Proposed 2026-11-07. Plan: [page testing](../plans/page-testing.md). Mockup: `docs/mockups/play-through-the-page.html`. Needs [0009](0009-isolation-is-found-and-proven.md) for its copies.

Evalution reaches an app through its API. It checks what was stored and what reached the model, and a model or code judges the reply the API returned. A person doesn't use the API, though. They use the page, and a page can fail where the API succeeds: a reply in a shape the frontend doesn't expect, a spinner that never clears, an error that replaces text that did arrive. For an assistant, "it remembered, but the person never saw it" is as much a regression as forgetting, and nothing in evalution can see it today.

The obvious way in, an adapter that drives the page instead of calling the API, doesn't fit the code as it stands:

- **The runner would blame the adapter for the page's failure.** When a turn's reply is empty but the traces show the agent answered, `runs/play.py` (`_check_the_adapter`) re-labels the try `Failure.ADAPTER`: couldn't judge, the adapter misread. After repeated adapter failures the runner ends the run (`runs/runner.py`, `ADAPTER_FAILURES`). A page that never shows a reply would produce exactly that, so the result this feature exists for would be unreachable, and the run would die on it.
- **An adapter can't reach what it would need.** `adapters` sits below `traces` and `isolation` in the import layers (ADR 0006, the import contract in `server/pyproject.toml`). A page adapter can't read the try's spans to tell when a reply is done, and can't ask isolation for the copy's address.
- **"Page" isn't a kind of app.** `AdapterInfo` describes a kind of app: how to find it, propose it, check it, read its traffic. The registry, the conformance kit and the runner all build one adapter per target. Driving a page is a way to reach any of those apps, not another one beside them.
- **Text on screen isn't the reply.** Rendered text loses whitespace, Markdown and code fences, and may hold a placeholder ("Couldn't load this reply"). The reply checks and the match that finds the answering model call (`judging/checks.py`) would misread it.

## Decision

- **The page observes; the API still plays.** A turn is sent and finished through the target's own adapter, as today, and its `Turn.reply` is what judging reads. The page shows the same conversation in a browser and records what appeared. A new field, `Turn.shown`, holds the page's text (or `None`, with what showed instead). Every runner rule that reads the reply keeps working unchanged, and "the reply never showed" is the page's finding, never the adapter's fault.
- **A mode of a target, composed above the adapters.** A target's config may carry a `page:` block. The `runs` layer, which may import `adapters`, `isolation` and `traces`, wraps the target's adapter in a page target when it has one. Page mode isn't registered in `adapters.infos()`. It is set per target.
- **The page map is data; a fixed script follows it.** Which page, how to sign in, how to open a conversation, where the message box, Send, the replies and New chat are, and when the page is still busy. Locators are roles with accessible names, or test ids: no CSS, XPath or coordinates. No model picks a click while a test runs. The map is versioned with the target and undoable (ADR 0005). It is written by hand at first; an agent that writes it from the code and the page comes later, through typed tools that act only by map entries.
- **Done is the adapter's word, then the page catches up.** A turn is over when the adapter says so: it already honours background work, cancelling, deadlines, and whether the app heard. Then the page gets a bounded time (`catch_up_seconds`) for its busy signals to clear and the reply to appear. The traces stay the runner's to watch.
- **Code decides what the page shows.** New check kinds, `shown_contains`, `shown_lacks` and `page_healthy` (no console errors, no failed requests to the copy), decided by code from the page. `Decider` stays `code | model` (ADR 0007). Each page turn gets a default `shown` check: the reply showed, and it matches the adapter's reply once both are normalised. A test decides whether page checks count toward its outcome, as it does for tool calls.
- **The verdict names the layer.** Stored (state checks), answered (reply and context checks) and shown (page checks) are read from check kinds, and the banner says which held. "Through the API alone this would have passed" is said only when the other checks passed in the same try.
- **A broken page map isn't a result.** An element the map names but the page doesn't have is `Failure.PAGE_MAP`: the try is unjudged with its own reason, and the run ends after that first try, since every try would meet the same thing. It is never `ADAPTER` and never a fail.
- **Only on evalution's copy, so only where copies are.** Page mode needs a managed footing (ADR 0009). Every URL in the map starts with the copy's page address, checked when the map is read, and filled in where `adapter_on` points the adapter at the copy. A test person signs in with an account seeded on the copy. A page run never touches a person's own app or a real account. Hosted mode has no copies, so it has no page mode.
- **One browser per copy, a clean context per try.** Cookies, local storage, IndexedDB and service workers never carry from one try to the next. Server-side state stays the isolation plan's to reset.
- **Proof is kept, secrets aren't.** Per try: a screenshot per step, written as it's taken; the page's console and failed requests, capped like other evidence; video when the try failed, or always if a setting says so. They live in a file store beside the run's evidence and are deleted with the run. Console and network text go through the same scrubbing as copy logs; sign-in fields and token-bearing URLs are masked; Playwright's full trace archive, which holds request bodies and cookies, is never kept.
- **Playwright for Python, headless Chromium, optional.** In an extra and a separate image tag. The default install keeps its size and says what's missing when page mode is chosen.

## Consequences

- `Turn` and `TurnRecord` gain `shown`. Every existing adapter leaves it `None`, and stored evidence upcasts on read.
- `Failure` gains `PAGE_MAP`, with its own unjudged reason and a run-ending rule of its own.
- New check kinds and a layered banner change the rules. `RULES_VERSION` is bumped, and older results are marked as decided under the old rules, not read as an improvement (ADR 0007).
- A file store in `storage/` for screenshots and video, and a delete path for runs, which they don't have today.
- `run --server` uploads screenshots and console by default. Video goes only by choice, within the upload cap, and a run whose video stayed on the machine that played it says so.
- After a restart, `close` and `cancel` work from `Session` fields alone, as the Target contract requires. A try cut short keeps its screenshots and step log; its video may be missing, and the try says so.
- Cases with a `fresh` turn are refused in page mode: a page has nowhere to carry the token where the person can't see it.
- Page tries are slower and heavier than API tries. That's why page mode is opt-in, why video is kept on failure only by default, and why the run's estimate counts it.
- Apps whose sign-in can't be seeded or bypassed on a copy (SSO, magic links, captchas) can't use page mode, and are told so.
- Without an API adapter underneath, there is no page mode yet. A page-only target would be reply-only and labelled that way, with done told by the page's own busy signals. That is a later decision, not this one.

## Amends

- **ADR 0004 / the Target protocol (`docs/architecture.md`, `docs/adapters.md`):** `Turn.shown` is added; adapters leave it empty. The page target is not an adapter, and adapter authors need do nothing for it.
- **ADR 0007:** new check kinds decided by code, a default `shown` check on page turns, the layered banner, and the rules version bump that comes with them.
- **ADR 0009:** copies remap the page's address as well as the API's, where the two differ, and the copy's setup can seed one test account per slot.
- **README, Known limits:** page mode is local and CI only, needs a proven copy, and supports sign-in only through seeded accounts or test auth.
