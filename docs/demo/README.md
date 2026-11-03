# Product demo assets

`evalution-demo.gif`, `steps/*.png` and `home-dark.png` are the real page, built from `web/`, driven by a script. Cartly and every number in them are made up.

To regenerate (needs Node, ffmpeg, Chrome and internet for the page's web fonts; calls no model):

```sh
npm ci --prefix web
npm ci --prefix docs/demo
node docs/demo/capture.mjs          # or: stills, gif
```

- `cartly.mjs`: all the fictional data.
- `fake-api.mjs`: answers the `/v1` routes the story uses and serves the built page. Work the real server does in the background plays out on timers.
- `capture.mjs`: plays the story in headless Chrome, typing and clicking in the page. `CHROME` overrides the browser's path.

The app parses the fake answers with `web/src/lib/schema.ts`, so a change there that the data no longer fits shows up as a failed capture.
