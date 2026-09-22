# Presentation assets

Four files. Three are generated and should never be edited by hand; the fourth is
the drawing the card is generated from.

| File | Where it is used | How to regenerate |
|---|---|---|
| `demo.gif` | the README, above the fold | `python scripts/record_demo.py` |
| `screenshot.png` | a still of the same moment, for anywhere a GIF is wrong | written by the same script |
| `social-preview.png` | GitHub's social preview card, and the banner at the top of the README | `rsvg-convert -w 1280 -h 640 docs/assets/social-preview.svg -o docs/assets/social-preview.png` |
| `social-preview.svg` | source of the card above — edit this one | hand-written; it is the source |

## The demo

`scripts/record_demo.py` runs the real server and the real frontend bundle and drives
them in a real browser. Only the model is replaced, by `scripts/demo_backend.py`, which
replays Ollama's own wire format at a fixed pace — so the reply is the same every run
and the speed badge at the end reports what the recording actually streamed.

It needs the frontend built (`npm run build` in `frontend/`) and Playwright's Chromium
(`playwright install chromium`). ffmpeg comes from Playwright's own bundle.

Changing the reply, its pace or the length of the clip is done in those two scripts:
`ANSWER` and `_TOKEN_DELAY_S` in the backend, `GIF_SECONDS`, `GIF_WIDTH`, `GIF_FPS` and
`PROMPT` in the recorder.

## The social preview

GitHub does not expose the social preview image through its API, so the PNG has to be
uploaded by hand, once: **Settings → General → Social preview → Upload an image**. It
stays until it is replaced, including across pushes.

The numbers on the card are the measured figures from [../benchmarks.md](../benchmarks.md).
When those change, change the SVG and re-render.

The README embeds the PNG rather than the SVG on purpose. The drawing names
Montserrat and Source Code Pro with no `@font-face`, so a browser without them
falls back to different metrics and the hand-placed columns of numbers collide.
Rendering to PNG here, where the fonts are installed, is what makes the card look
the same for everyone.
