# Living Fold Favicon Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Publish the approved Living Fold K as the favicon across modern browsers, Apple touch bookmarks, and legacy favicon requests without stale-cache or nginx 404 failures.

**Architecture:** Keep the approved 128 × 128 transparent PNG as the visual source, derive deterministic Apple and ICO variants, and reference a content-versioned PNG from the shared Vite HTML shell. Stable compatibility URLs are served by explicit nginx locations with no-cache headers, while the versioned asset uses the existing immutable `/assets/` route.

**Tech Stack:** Vite 8, React 19, HTML link metadata, Pillow asset conversion, nginx, Vitest, Python unittest/pytest, production curl smoke checks.

---

### Task 1: Lock the icon asset contract with failing tests

**Files:**
- Modify: `frontend/src/landing/AccessibilityContracts.test.tsx:50-57`
- Modify: `frontend/e2e/landing.spec.ts:303`
- Modify: `tests/deployment_cases/test_marketing_site_package.py:92-115`
- Modify: `tests/deployment_cases/test_marketing_site_package.py:193-197`
- Modify: `tests/deployment_cases/test_marketing_site_package.py:373-382`

- [ ] **Step 1: Write the failing HTML-shell contract**

Replace the single `/favicon.png` assertion with assertions for the approved
versioned PNG, ICO fallback, and Apple icon:

```tsx
const favicon = htmlDocument.querySelector('link[rel="icon"]');
const shortcut = htmlDocument.querySelector('link[rel="shortcut icon"]');
const apple = htmlDocument.querySelector('link[rel="apple-touch-icon"]');

expect(favicon?.getAttribute('href')).toMatch(
  /^\/assets\/favicon-living-fold-[a-f0-9]{8}\.png$/,
);
expect(favicon?.getAttribute('type')).toBe('image/png');
expect(favicon?.getAttribute('sizes')).toBe('128x128');
expect(shortcut?.getAttribute('href')).toBe('/favicon.ico');
expect(apple?.getAttribute('href')).toBe('/apple-touch-icon.png');
expect(apple?.getAttribute('sizes')).toBe('180x180');
```

- [ ] **Step 2: Write the failing package and nginx contracts**

Make the deployment suite require:

```python
from PIL import Image

favicon_png = next((DIST / "assets").glob("favicon-living-fold-*.png"))
favicon_ico = DIST / "favicon.ico"
apple_icon = DIST / "apple-touch-icon.png"

assert Image.open(favicon_png).size == (128, 128)
assert Image.open(apple_icon).size == (180, 180)
assert Image.open(favicon_ico).format == "ICO"
```

Also require explicit nginx locations for `/favicon.png`, `/favicon.ico`, and
`/apple-touch-icon.png`, and a permanent redirect from `/favicon.svg` to the
versioned Living Fold PNG.

- [ ] **Step 3: Run the focused tests and confirm RED**

Run:

```powershell
Set-Location frontend
npm exec vitest -- run src/landing/AccessibilityContracts.test.tsx
Set-Location ..
python -m pytest tests/deployment_cases/test_marketing_site_package.py -q
```

Expected: the frontend test fails because the HTML still names `/favicon.png`;
the deployment tests fail because ICO/Apple/versioned assets and nginx routes do
not exist.

### Task 2: Produce deterministic Living Fold favicon assets

**Files:**
- Create: `frontend/public/assets/favicon-living-fold-a96d189f.png`
- Create: `frontend/public/apple-touch-icon.png`
- Create: `frontend/public/favicon.ico`
- Retain: `frontend/public/favicon.png`

- [ ] **Step 1: Generate files from the approved square PNG**

Run this one-time deterministic conversion from the repository root:

```powershell
@'
from pathlib import Path
from shutil import copyfile
from PIL import Image

public = Path("frontend/public")
source = Image.open(public / "favicon.png").convert("RGBA")
assert source.size == (128, 128)
copyfile(
    public / "favicon.png",
    public / "assets" / "favicon-living-fold-a96d189f.png",
)
source.resize((180, 180), Image.Resampling.LANCZOS).save(
    public / "apple-touch-icon.png", optimize=True
)
source.save(
    public / "favicon.ico",
    format="ICO",
    sizes=[(16, 16), (32, 32), (48, 48)],
)
'@ | python -
```

- [ ] **Step 2: Verify dimensions, formats, transparency, and source identity**

Run:

```powershell
@'
from pathlib import Path
from PIL import Image

public = Path("frontend/public")
for path in (
    public / "favicon.png",
    public / "assets" / "favicon-living-fold-a96d189f.png",
    public / "apple-touch-icon.png",
    public / "favicon.ico",
):
    image = Image.open(path)
    print(path, image.format, image.size, image.mode)
'@ | python -
```

Expected: source/versioned PNG are 128 × 128, Apple PNG is 180 × 180, ICO is
recognized as ICO and exposes the requested embedded sizes.

### Task 3: Wire HTML and nginx to the new icon set

**Files:**
- Modify: `frontend/index.html:10`
- Modify: `deploy/nginx/kaigo-marketing-site.conf:21-27`
- Modify: `docs/KAIGO_SPACE_OPERATIONS.md:84-125`

- [ ] **Step 1: Replace the HTML metadata**

Use the exact shared-shell metadata:

```html
<link rel="icon" type="image/png" sizes="128x128" href="/assets/favicon-living-fold-a96d189f.png" />
<link rel="shortcut icon" href="/favicon.ico" />
<link rel="apple-touch-icon" sizes="180x180" href="/apple-touch-icon.png" />
```

- [ ] **Step 2: Replace the stale nginx favicon block**

Serve stable files with no-cache and keep the legacy URL non-broken:

```nginx
location = /favicon.svg {
    return 308 /assets/favicon-living-fold-a96d189f.png;
}

location ~ ^/(favicon\.png|favicon\.ico|apple-touch-icon\.png)$ {
    root /var/www/kaigo-marketing/current;
    try_files $uri =404;
    access_log off;
    add_header Cache-Control "no-cache";
    add_header X-Content-Type-Options "nosniff" always;
}
```

- [ ] **Step 3: Update operational smoke commands**

Document HEAD checks for the versioned PNG, stable PNG, ICO, Apple icon, and the
legacy SVG redirect. Keep `/landing-old/favicon.svg` unchanged.

- [ ] **Step 4: Run focused tests and confirm GREEN**

Run:

```powershell
Set-Location frontend
npm exec vitest -- run src/landing/AccessibilityContracts.test.tsx
npm run typecheck
npm run lint -- --no-cache
npm run build
Set-Location ..
python -m pytest tests/deployment_cases/test_marketing_site_package.py -q
```

Expected: all commands pass; `frontend/dist/index.html` references all three
icons and every referenced file exists.

### Task 4: Review, commit, and deploy the marketing-only release

**Files:**
- Verify: all files from Tasks 1-3
- Deploy: immutable marketing release created from the final Git commit

- [ ] **Step 1: Run final local verification**

Run:

```powershell
git diff --check
git status --short
Set-Location frontend
npm test -- --run
npm run build
Set-Location ..
python -m pytest tests/deployment_cases/test_marketing_site_package.py -q
```

Expected: tests and build pass, diff-check is clean, and only intended favicon,
HTML, nginx, tests, and documentation files are changed.

- [ ] **Step 2: Commit and push intentionally**

Run:

```powershell
git add frontend deploy/nginx/kaigo-marketing-site.conf tests/deployment_cases/test_marketing_site_package.py docs/KAIGO_SPACE_OPERATIONS.md docs/superpowers/specs/2026-08-14-living-fold-favicon-design.md docs/superpowers/plans/2026-08-14-living-fold-favicon.md
git commit -m "fix: publish living fold favicon assets"
git push origin HEAD:codex/saas-foundation
```

Expected: a new immutable commit exists on `origin/codex/saas-foundation`.

- [ ] **Step 3: Deploy only the static marketing package and nginx snippet**

On production, fetch and verify the exact commit, create a clean immutable
release directory, build or upload `frontend/dist`, then install the reviewed
snippet at the confirmed active path and validate it before reload:

```bash
release_sha="$(git rev-parse HEAD)"
install -m 0644 \
  "/opt/kaigo/releases/${release_sha}/deploy/nginx/kaigo-marketing-site.conf" \
  /etc/nginx/snippets/kaigo-marketing-site.conf
nginx -t
KAIGO_MARKETING_SKIP_BUILD=1 \
  bash "/opt/kaigo/releases/${release_sha}/scripts/deploy_marketing_site.sh" \
  "${release_sha}-living-fold-favicon"
```

Do not reset or update `/root/ai_project`, do not create a Compose project named
`kaigo`, and do not recreate backend or database containers for this static
release.

- [ ] **Step 4: Verify production bytes and headers**

Run HEAD/GET checks against:

```text
https://kaigo.space/
https://kaigo.space/studio/
https://kaigo.space/assets/favicon-living-fold-a96d189f.png
https://kaigo.space/favicon.png
https://kaigo.space/favicon.ico
https://kaigo.space/apple-touch-icon.png
https://kaigo.space/favicon.svg
```

Expected: primary/stable assets return `200` with correct image content types;
the SVG URL returns a `308` to the Living Fold PNG; the live HTML contains the
three new link tags; SHA-256 for the primary PNG is
`a96d189f1b6bd34bc20f5515306a776d9a378c8930fa3e717014072c956c0c63`.

- [ ] **Step 5: Commit documentation evidence if production smoke is green**

Record the release SHA, public headers, asset digest, and final live check in the
release packet without secrets, then commit the evidence separately.
