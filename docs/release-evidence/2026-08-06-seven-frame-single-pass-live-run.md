# Seven-frame single-pass reference capture — live evidence

Date: 2026-08-06  
Production worker commit: `7bb4361`  
Reference site: `https://skyeng.ru/`

## Scope

This run measures only the deterministic browser stage that loads and scrolls the
reference site, settles the selected viewports, and produces JPEG evidence. It
does not call an AI model and therefore does not include visual-analysis or
idea-generation latency.

Desktop and mobile were captured concurrently. Each viewport used one forward
evidence pass; there was no separate warm scroll and no return-to-top pass.

## Result

- Crawl result: `succeeded`
- Coverage: `complete`
- Required evidence: 7/7 JPEGs
- Crawler duration: 54.769 seconds
- One-off container wall time: 60.472 seconds
- Peak two-browser container memory observed: about 1.16 GiB
- Server memory available before the run: about 1.8 GiB of 2.9 GiB

The same site before the final native-scroll optimization took 85.271 seconds
inside the crawler and 92.186 seconds wall-clock. The final run is about 35.8%
faster inside the crawler. This is a same-site, same-settings comparison.

### Viewport timings

| Viewport | Load and initial settle | Evidence pass | Final settle and screenshots | Total |
| --- | ---: | ---: | ---: | ---: |
| Desktop 1920x1080 | 10.235 s | 31.091 s | 4.264 s | 45.598 s |
| Mobile | 17.041 s | 29.409 s | 3.216 s | 49.667 s |

The crawler-level total is slightly higher than the slower viewport because it
also includes request validation and orchestration around both concurrent
captures.

## Evidence packet

1. [Desktop top](assets/2026-08-06-skyeng-seven-frame/home-desktop-top.jpg)
2. [Desktop immediately after the first screen](assets/2026-08-06-skyeng-seven-frame/home-desktop-after_top.jpg)
3. [Desktop middle](assets/2026-08-06-skyeng-seven-frame/home-desktop-middle.jpg)
4. [Desktop bottom](assets/2026-08-06-skyeng-seven-frame/home-desktop-bottom.jpg)
5. [Mobile top](assets/2026-08-06-skyeng-seven-frame/home-mobile-mobile-top.jpg)
6. [Mobile middle](assets/2026-08-06-skyeng-seven-frame/home-mobile-mobile-middle.jpg)
7. [Mobile bottom](assets/2026-08-06-skyeng-seven-frame/home-mobile-mobile-bottom.jpg)

Raw metadata and resource samples are stored beside the images:

- `reference.json`
- `wall.json`
- `resource-samples.txt`

## Visual inspection

All seven files are readable JPEGs and contain distinct, rendered states. The
new desktop `after_top` frame no longer repeats the hero: it captures the next
content section while preserving the site's sticky header. Lazy images and the
site's own chat widget are rendered in later frames, confirming that the forward
scroll triggered viewport-dependent content.

## User-visible stage boundary

The browser capture and AI work now emit separate events:

1. `reference.started` — the site is being loaded and screenshots are prepared.
2. `reference.capture_completed` — seven screenshots are ready; capture timing is attached.
3. `reference.analysis_started` — the AI starts analysing the evidence and choosing a direction.
4. `reference.completed` — AI analysis has completed.

No 15-second limit is applied to the AI-analysis stage. The performance target
in this report applies only to deterministic browser capture.
