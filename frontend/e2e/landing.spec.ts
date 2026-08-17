import AxeBuilder from '@axe-core/playwright';
import { expect, test, type Locator, type Page, type TestInfo } from '@playwright/test';

const EXACT_HERO = 'Через 10 минут вы сможете сказать: наш бизнес использует AI';
const EXACT_DESCRIPTION = 'Добавьте ссылку на сайт и бесплатно получите первую версию персонального AI-виджета для вашего бизнеса. Обычно первая версия готова за 10–20 минут; сложные сайты могут потребовать больше времени.';
const PHONE_VIEWPORTS = [
  { width: 320, height: 568 },
  { width: 360, height: 800 },
  { width: 390, height: 844 },
  { width: 430, height: 932 },
] as const;

function viewportsForProject(testInfo: TestInfo) {
  return testInfo.project.name === 'desktop-1920'
    ? [{ width: 1_920, height: 1_080 }]
    : PHONE_VIEWPORTS;
}

async function expectNoHorizontalOverflow(page: Page) {
  const overflow = await page.evaluate(() =>
    Math.max(
      document.documentElement.scrollWidth,
      document.body.scrollWidth,
    ) - window.innerWidth,
  );
  expect(overflow).toBeLessThanOrEqual(1);
}

async function expectContainedInViewport(locator: Locator, label: string) {
  const box = await locator.boundingBox();
  expect.soft(box, `${label} must have a rendered box`).not.toBeNull();
  if (!box) return;
  const viewportWidth = await locator.evaluate(() => window.innerWidth);
  expect.soft(box.x, `${label} must stay inside the left viewport edge`).toBeGreaterThanOrEqual(-1);
  expect.soft(box.x + box.width, `${label} must stay inside the right viewport edge`).toBeLessThanOrEqual(viewportWidth + 1);
  expect.soft(box.width, `${label} must not be wider than the viewport`).toBeLessThanOrEqual(viewportWidth + 1);
}

async function expectVisibleInsideSoft(locator: Locator, container: Locator, label: string) {
  const elementBox = await locator.boundingBox();
  const containerBox = await container.boundingBox();
  expect.soft(elementBox, `${label} must have a rendered box`).not.toBeNull();
  expect.soft(containerBox, `${label} container must have a rendered box`).not.toBeNull();
  if (!elementBox || !containerBox) return;

  const elementRight = elementBox.x + elementBox.width;
  const containerRight = containerBox.x + containerBox.width;
  const elementBottom = elementBox.y + elementBox.height;
  const containerBottom = containerBox.y + containerBox.height;
  const intersectionWidth = Math.max(0, Math.min(elementRight, containerRight) - Math.max(elementBox.x, containerBox.x));
  const intersectionHeight = Math.max(0, Math.min(elementBottom, containerBottom) - Math.max(elementBox.y, containerBox.y));
  const elementArea = elementBox.width * elementBox.height;
  const visibleRatio = elementArea > 0 ? (intersectionWidth * intersectionHeight) / elementArea : 0;
  expect.soft(visibleRatio, `${label} must remain inside its container`).toBeGreaterThanOrEqual(0.95);
}

async function expectMobileTouchTargets(page: Page, viewportLabel: string) {
  const undersized = await page.locator('a[href], button, input:not([type="radio"]), textarea, summary, label.feedback-topic').evaluateAll((elements) => (
    elements.flatMap((element) => {
      const style = getComputedStyle(element);
      const rect = element.getBoundingClientRect();
      if (element.closest('[hidden], [aria-hidden="true"]') || style.display === 'none' || style.visibility === 'hidden' || rect.width === 0 || rect.height === 0) return [];
      if (rect.width >= 44 && rect.height >= 44) return [];
      return [{
        name: element.getAttribute('aria-label') || element.textContent?.trim() || element.tagName,
        width: Math.round(rect.width * 10) / 10,
        height: Math.round(rect.height * 10) / 10,
      }];
    })
  ));
  expect.soft(undersized, `all mobile touch targets must be at least 44px at ${viewportLabel}`).toEqual([]);
}

async function expectMobileTextSizes(page: Page, viewportLabel: string) {
  const undersized = await page.locator('h1, h2, h3, h4, p, a, button, label, input, textarea, summary, li, strong, span, small, code').evaluateAll((elements) => (
    elements.flatMap((element) => {
      const rect = element.getBoundingClientRect();
      const style = getComputedStyle(element);
      const mockUi = element.closest(
        '.browser-mockup, .widget-preview-card, .mini-site, [aria-hidden="true"]',
      );
      if (mockUi || element.classList.contains('sr-only') || style.display === 'none' || style.visibility === 'hidden' || rect.width === 0 || rect.height === 0) return [];
      const text = element.textContent?.trim() ?? '';
      if (!text && !element.matches('input, textarea')) return [];
      const size = Number.parseFloat(style.fontSize);
      const isKicker = element.matches(
        '.section-kicker, [class*="kicker"], [class*="eyebrow"], [class*="meta"]',
      );
      const isRadio = element.matches('input[type="radio"]');
      const isFooterLink = Boolean(element.closest('.site-footer__links'));
      const isConsentCopy = Boolean(element.closest('.feedback-composer__consent'));
      if (isRadio) return [];
      const minimum = isFooterLink || isConsentCopy
        ? 12
        : element.matches('input, textarea')
          ? 16
          : isKicker
            ? 11
            : element.matches('span, small, code')
              ? 12
              : 14;
      if (size >= minimum) return [];
      return [{
        name: element.getAttribute('aria-label') || text.slice(0, 80) || element.tagName,
        size,
        minimum,
      }];
    })
  ));
  expect.soft(undersized, `meaningful mobile text must remain legible at ${viewportLabel}`).toEqual([]);
}

async function expectHeadingWordsFit(locator: Locator, label: string) {
  const measurement = await locator.evaluate((heading) => {
    const styles = getComputedStyle(heading);
    const probe = document.createElement('span');
    probe.setAttribute('aria-hidden', 'true');
    probe.style.position = 'fixed';
    probe.style.left = '-10000px';
    probe.style.top = '0';
    probe.style.visibility = 'hidden';
    probe.style.whiteSpace = 'nowrap';
    probe.style.display = 'inline-block';
    probe.style.fontFamily = styles.fontFamily;
    probe.style.fontSize = styles.fontSize;
    probe.style.fontStyle = styles.fontStyle;
    probe.style.fontWeight = styles.fontWeight;
    probe.style.fontStretch = styles.fontStretch;
    probe.style.letterSpacing = styles.letterSpacing;
    probe.style.textTransform = styles.textTransform;
    document.body.appendChild(probe);
    const words = heading.textContent?.trim().split(/\s+/).filter(Boolean) ?? [];
    const widths = words.map((word) => {
      probe.textContent = word;
      return probe.getBoundingClientRect().width;
    });
    const result = {
      headingWidth: heading.clientWidth,
      maxWordWidth: Math.max(0, ...widths),
      words,
    };
    probe.remove();
    return result;
  });
  expect(
    measurement.maxWordWidth,
    `${label}: every H1 word must fit the heading width (${measurement.words.join(', ')})`,
  ).toBeLessThanOrEqual(measurement.headingWidth + 1);
}

async function expectLegalDocumentContent(page: Page, title: string, label: string) {
  await expect(page.locator('.legal-page')).toBeVisible();
  const heading = page.getByRole('heading', { level: 1, name: title });
  await expect(heading).toBeVisible();
  await expectHeadingWordsFit(heading, label);
  await expectMobileTextSizes(page, label);

  const toc = page.getByRole('navigation', { name: 'Содержание документа' });
  await expect(toc).toBeVisible();
  const tocLinks = toc.getByRole('link');
  const tocCount = await tocLinks.count();
  expect(tocCount, `${label}: TOC must include every section and contact`).toBeGreaterThan(1);

  for (let index = 0; index < tocCount; index += 1) {
    const href = await tocLinks.nth(index).getAttribute('href');
    expect(href, `${label}: TOC link ${index + 1} must point to a local section`).toMatch(/^#[a-z0-9-]+$/);
    const target = page.locator(`[id="${href?.slice(1)}"]`);
    await expect(target, `${label}: TOC target ${href}`).toHaveCount(1);
    await expect(target.getByRole('heading', { level: 2 })).toBeVisible();
    await expect(target.locator('p').filter({ hasText: /\S/ }).first()).toBeVisible();
  }

  const sections = page.locator('.legal-document__sections .legal-section, .legal-contact');
  const sectionCount = await sections.count();
  expect(sectionCount, `${label}: all document sections must render`).toBe(tocCount);
  for (let index = 0; index < sectionCount; index += 1) {
    await expect(sections.nth(index).getByRole('heading', { level: 2 })).toBeVisible();
    await expect(sections.nth(index).locator('p').filter({ hasText: /\S/ }).first()).toBeVisible();
  }
}

async function expectFormaImagesLoaded(container: Locator, viewportLabel: string) {
  for (const src of ['/assets/forma-site-before.webp', '/assets/forma-widget-answer.webp', '/assets/forma-site-widget.webp']) {
    const image = container.locator(`img[src="${src}"]`);
    const imageCount = await image.count();
    expect.soft(imageCount, `${src} must be present at ${viewportLabel}`).toBe(1);
    if (imageCount === 0) continue;
    const state = await image.evaluate((element) => {
      const imageElement = element as HTMLImageElement;
      return { complete: imageElement.complete, naturalWidth: imageElement.naturalWidth };
    });
    expect.soft(state.complete && state.naturalWidth > 0, `${src} must be loaded at ${viewportLabel}`).toBe(true);
  }
}

async function attachScreenshot(
  page: Page,
  testInfo: TestInfo,
  name: string,
  options: { fullPage?: boolean } = {},
) {
  const body = await page.screenshot({
    fullPage: options.fullPage,
  });
  await testInfo.attach(name, { body, contentType: 'image/png' });
}

async function revealLanding(page: Page) {
  const sections = page.locator('[data-landing-section]');
  const sectionCount = await sections.count();
  for (let index = 0; index < sectionCount; index += 1) {
    await sections.nth(index).scrollIntoViewIfNeeded();
    await page.waitForTimeout(80);
  }
  await page.evaluate(() => window.scrollTo({ top: 0, behavior: 'instant' }));
}

async function expectVisibleInside(
  locator: Locator,
  container: Locator,
  minimumRatio = 0.95,
  label = 'element',
) {
  const elementBox = await locator.boundingBox();
  const containerBox = await container.boundingBox();
  expect(elementBox, `${label} must have a rendered box`).not.toBeNull();
  expect(containerBox, `${label} container must have a rendered box`).not.toBeNull();

  const intersectionWidth = Math.max(0, Math.min(
    (elementBox?.x ?? 0) + (elementBox?.width ?? 0),
    (containerBox?.x ?? 0) + (containerBox?.width ?? 0),
  ) - Math.max(elementBox?.x ?? 0, containerBox?.x ?? 0));
  const intersectionHeight = Math.max(0, Math.min(
    (elementBox?.y ?? 0) + (elementBox?.height ?? 0),
    (containerBox?.y ?? 0) + (containerBox?.height ?? 0),
  ) - Math.max(elementBox?.y ?? 0, containerBox?.y ?? 0));
  const elementArea = (elementBox?.width ?? 0) * (elementBox?.height ?? 0);
  const visibleRatio = elementArea > 0 ? (intersectionWidth * intersectionHeight) / elementArea : 0;
  expect.soft(visibleRatio, `${label} visible area`).toBeGreaterThanOrEqual(minimumRatio);
}

async function expectMinimumTarget(locator: Locator, minimum = 44, label = 'interactive target') {
  const box = await locator.boundingBox();
  expect(box, `${label} must have a rendered box`).not.toBeNull();
  expect.soft(box?.width ?? 0, `${label} width`).toBeGreaterThanOrEqual(minimum);
  expect.soft(box?.height ?? 0, `${label} height`).toBeGreaterThanOrEqual(minimum);
}

async function expectProductTourInsideViewport(page: Page) {
  const section = page.locator('.product-tour-section');
  const controls = section.locator('.product-tour__controls');
  await expect(section).toBeVisible();
  await expect(section.locator('[data-tour-step]')).toHaveCount(3);
  await expectVisibleInside(controls, section, 0.99, 'product tour controls');
  await expectNoHorizontalOverflow(page);

  const geometry = await section.evaluate((element) => {
    const sectionRect = element.getBoundingClientRect();
    const controlsRect = element.querySelector('.product-tour__controls')?.getBoundingClientRect();
    return {
      sectionBottom: sectionRect.bottom,
      controlsBottom: controlsRect?.bottom ?? Number.POSITIVE_INFINITY,
      viewportHeight: window.innerHeight,
    };
  });
  expect.soft(geometry.sectionBottom, 'product tour must end inside the viewport')
    .toBeLessThanOrEqual(geometry.viewportHeight + 1);
  expect.soft(geometry.controlsBottom, 'product tour controls must remain available without scrolling')
    .toBeLessThanOrEqual(geometry.viewportHeight + 1);
}

async function expectProductTourUsable(page: Page) {
  const section = page.locator('.product-tour-section');
  const controls = section.locator('.product-tour__controls');
  await expect(section).toBeVisible();
  await expect(section.locator('[data-tour-step]')).toHaveCount(3);
  await expect(controls).toBeVisible();
  await expectNoHorizontalOverflow(page);

  const geometry = await section.evaluate((element) => {
    const sectionRect = element.getBoundingClientRect();
    const controlsRect = element.querySelector('.product-tour__controls')?.getBoundingClientRect();
    return {
      controlsBottom: controlsRect?.bottom ?? Number.POSITIVE_INFINITY,
      sectionBottom: sectionRect.bottom,
      sectionHeight: sectionRect.height,
      viewportHeight: window.innerHeight,
    };
  });
  expect.soft(geometry.controlsBottom, 'product tour controls must stay inside the section')
    .toBeLessThanOrEqual(geometry.sectionBottom + 1);
  expect.soft(geometry.sectionHeight, 'product tour must remain reasonably compact')
    .toBeLessThanOrEqual(geometry.viewportHeight * 1.45);
}

async function expectReasonableMobileTourHeight(
  tour: Locator,
  viewport: { width: number; height: number },
  label: string,
) {
  const sectionHeight = await tour.evaluate((element) => element.getBoundingClientRect().height);
  const maximumHeight = viewport.height <= 430
    ? viewport.height * 3
    : Math.max(1_300, viewport.height * 1.45);
  expect.soft(sectionHeight, `${label} must keep controls within a reasonable scroll distance`)
    .toBeLessThanOrEqual(maximumHeight);
}

async function expectCompactFirstScreen(page: Page) {
  const viewport = page.viewportSize();
  expect(viewport, 'compact project must provide a viewport').not.toBeNull();
  const viewportLabel = `${viewport?.width}x${viewport?.height}`;
  const scene = page.getByTestId('hero-scene');
  const finalWidget = page.locator('[data-testid="widget-preview"][data-visible="true"]');
  const widgetLabel = page.locator('.hero-browser-stage__widget-label');
  const heroComposer = page.locator('.hero-copy .url-composer');
  const processCards = page.locator('[data-testid="process-card"][data-visible="true"]');
  await expect(widgetLabel).toBeVisible();
  await expect(heroComposer).toBeVisible();
  await expect(processCards).toHaveCount(3);

  const firstScreenElements: Array<[string, Locator]> = [
    ['hero section', page.locator('.hero-section')],
    ['hero copy', page.locator('.hero-copy')],
    ['hero scene', scene],
    ['URL composer', heroComposer],
    ['widget label', widgetLabel],
    ['final widget', finalWidget],
  ];
  for (let index = 0; index < 3; index += 1) {
    firstScreenElements.push([`process card ${index + 1}`, processCards.nth(index)]);
  }

  const browserMockup = page.locator('.hero-browser-stage');
  const browserBounds = await browserMockup.boundingBox();
  expect(browserBounds, `browser mockup must have a rendered box at ${viewportLabel}`).not.toBeNull();
  for (let index = 0; index < 3; index += 1) {
    const cardBounds = await processCards.nth(index).boundingBox();
    expect(cardBounds, `process card ${index + 1} must have a rendered box at ${viewportLabel}`).not.toBeNull();
    const gap = (browserBounds?.x ?? 0) - ((cardBounds?.x ?? 0) + (cardBounds?.width ?? 0));
    expect.soft(gap, `process card ${index + 1} must end before the browser mockup at ${viewportLabel}`)
      .toBeGreaterThanOrEqual(12);
  }

  const viewportTolerance = 1;
  for (const [name, locator] of firstScreenElements) {
    await expect(locator, `${name} must be visible in the completed hero at ${viewportLabel}`).toBeVisible();
    const bounds = await locator.evaluate((element) => {
      const rect = element.getBoundingClientRect();
      return {
        left: rect.left,
        top: rect.top,
        right: rect.right,
        bottom: rect.bottom,
        viewportWidth: window.innerWidth,
        viewportHeight: window.innerHeight,
      };
    });
    expect.soft(bounds.left, `${name} must not protrude past the left viewport edge at ${viewportLabel}`)
      .toBeGreaterThanOrEqual(-viewportTolerance);
    expect.soft(bounds.top, `${name} must not protrude past the top viewport edge at ${viewportLabel}`)
      .toBeGreaterThanOrEqual(-viewportTolerance);
    expect.soft(bounds.right, `${name} must not protrude past the right viewport edge at ${viewportLabel}`)
      .toBeLessThanOrEqual(bounds.viewportWidth + viewportTolerance);
    expect.soft(bounds.bottom, `${name} must not protrude past the bottom viewport edge at ${viewportLabel}`)
      .toBeLessThanOrEqual(bounds.viewportHeight + viewportTolerance);
  }
  await expectNoHorizontalOverflow(page);

  const header = page.locator('.site-header');
  const headerBox = await header.boundingBox();
  expect(headerBox, `site header must have a rendered box at ${viewportLabel}`).not.toBeNull();
  expect.soft(headerBox?.height ?? Number.POSITIVE_INFINITY, `header height at ${viewportLabel}`)
    .toBeLessThanOrEqual(96);
  await expectMinimumTarget(page.locator('.site-header__logo'), 44, `header logo at ${viewportLabel}`);

  const otherHeaderTargets = page.locator(
    '.site-header a:not(.site-header__logo):visible, .site-header button:visible',
  );
  const otherHeaderTargetCount = await otherHeaderTargets.count();
  for (let index = 0; index < otherHeaderTargetCount; index += 1) {
    await expectMinimumTarget(
      otherHeaderTargets.nth(index),
      44,
      `visible header target ${index + 1} at ${viewportLabel}`,
    );
  }

  const heroHeadingFontSize = await page.locator('.hero-copy h1').evaluate((element) =>
    Number.parseFloat(getComputedStyle(element).fontSize),
  );
  expect.soft(heroHeadingFontSize, `hero heading font size at ${viewportLabel}`).toBeLessThanOrEqual(50);
}

test('landing compact desktop fits the first screen and exposes the brand @compact', async ({ page }) => {
  test.setTimeout(90_000);
  await page.addInitScript(() => {
    type HeroMotionDiagnostic = {
      sourceAt: number | null;
      completeAt: number | null;
    };

    const diagnostic: HeroMotionDiagnostic = { sourceAt: null, completeAt: null };
    (window as Window & { __kaigoHeroMotionTiming?: HeroMotionDiagnostic }).__kaigoHeroMotionTiming = diagnostic;

    const sampleCompactGap = () => {
      const browser = document.querySelector('.hero-browser-stage')?.getBoundingClientRect();
      const visibleCards = [...document.querySelectorAll<HTMLElement>('.process-card')]
        .filter((card) => Number.parseFloat(getComputedStyle(card).opacity) > 0.05);
      if (browser && visibleCards.length > 0) {
        const gap = Math.min(
          ...visibleCards.map((card) => browser.left - card.getBoundingClientRect().right),
        );
        const browserWindow = window as Window & { __kaigoHeroMinimumMotionGap?: number };
        browserWindow.__kaigoHeroMinimumMotionGap = Math.min(
          browserWindow.__kaigoHeroMinimumMotionGap ?? Number.POSITIVE_INFINITY,
          gap,
        );
      }
      requestAnimationFrame(sampleCompactGap);
    };
    requestAnimationFrame(sampleCompactGap);

    const sceneSelector = '[data-testid="hero-scene"]';
    const recordPhase = (scene: Element) => {
      const phase = scene.getAttribute('data-motion-phase');
      if (phase === 'source' && diagnostic.sourceAt === null) {
        diagnostic.sourceAt = performance.now();
      }
      if (phase === 'complete' && diagnostic.completeAt === null) {
        diagnostic.completeAt = performance.now();
      }
    };
    const inspectNode = (node: Node) => {
      if (!(node instanceof Element)) return;
      if (node.matches(sceneSelector)) recordPhase(node);
      node.querySelectorAll(sceneSelector).forEach(recordPhase);
    };

    const observer = new MutationObserver((records) => {
      for (const record of records) {
        if (record.type === 'attributes') {
          inspectNode(record.target);
          continue;
        }
        record.addedNodes.forEach(inspectNode);
      }
    });
    observer.observe(document, {
      subtree: true,
      childList: true,
      attributes: true,
      attributeFilter: ['data-motion-phase'],
    });
    if (document.documentElement) inspectNode(document.documentElement);
  });

  await page.setViewportSize({ width: 1_536, height: 960 });
  await page.goto('/');

  const scene = page.getByTestId('hero-scene');
  await expect(scene).toHaveAttribute('data-motion-phase', 'complete', { timeout: 20_000 });
  const motionTiming = await page.evaluate(() => (
    (window as Window & {
      __kaigoHeroMotionTiming?: { sourceAt: number | null; completeAt: number | null };
    }).__kaigoHeroMotionTiming
  ));
  expect(motionTiming?.sourceAt, 'observer must capture the first source phase').toEqual(expect.any(Number));
  expect(motionTiming?.completeAt, 'observer must capture the first complete phase').toEqual(expect.any(Number));
  const motionDurationMs = (motionTiming?.completeAt ?? Number.POSITIVE_INFINITY)
    - (motionTiming?.sourceAt ?? Number.NEGATIVE_INFINITY);
  expect(motionDurationMs, 'complete phase must follow the source phase').toBeGreaterThanOrEqual(0);
  expect(motionDurationMs, 'hero motion from source to complete must finish within 12 seconds')
    .toBeLessThanOrEqual(12_000);
  const minimumMotionGap = await page.evaluate(() => (
    (window as Window & { __kaigoHeroMinimumMotionGap?: number }).__kaigoHeroMinimumMotionGap
  ));
  expect(minimumMotionGap, 'cards must not overlap the browser during compact hero motion')
    .toBeGreaterThanOrEqual(12);

  await expectCompactFirstScreen(page);
  await page.setViewportSize({ width: 1_366, height: 768 });
  await page.evaluate(() => new Promise<void>((resolve) => {
    requestAnimationFrame(() => requestAnimationFrame(() => resolve()));
  }));
  await expectCompactFirstScreen(page);
  await page.setViewportSize({ width: 1_680, height: 960 });
  await page.evaluate(() => new Promise<void>((resolve) => {
    requestAnimationFrame(() => requestAnimationFrame(() => resolve()));
  }));
  await expectCompactFirstScreen(page);

  await expect.soft(page).toHaveTitle('Kaigo — AI-консультант для вашего сайта');
  await expect.soft(page.locator('meta[name="description"]')).toHaveAttribute('content', EXACT_DESCRIPTION);
  await expect.soft(page.locator('link[rel="icon"]')).toHaveAttribute('href', '/favicon.png');
  await expect.soft(page.locator('link[rel="icon"]')).toHaveAttribute('type', 'image/png');
});

test('landing desktop completes the hero story without overflow @desktop', async ({ page }, testInfo) => {
  test.setTimeout(90_000);
  await page.goto('/');
  await expect(page.getByRole('heading', { name: EXACT_HERO })).toBeVisible();
  await expect(page.getByTestId('hero-scene')).toHaveAttribute('data-motion-phase', 'source');

  await attachScreenshot(page, testInfo, 'hero-source-1920.png');
  await expect(page.getByTestId('hero-scene')).toHaveAttribute('data-motion-phase', 'scanning', {
    timeout: 3_000,
  });
  await attachScreenshot(page, testInfo, 'hero-scan-1920.png');

  await expect(page.getByTestId('hero-scene')).toHaveAttribute('data-motion-phase', 'complete', {
    timeout: 12_000,
  });
  await expect(page.getByTestId('process-card')).toHaveCount(3);
  await expect(page.locator('[data-testid="widget-preview"][data-visible="true"]')).toBeVisible();
  await expectNoHorizontalOverflow(page);

  await page.emulateMedia({ reducedMotion: 'reduce' });
  await revealLanding(page);
  await expect(page.locator('[data-landing-section]').nth(1)).toHaveAttribute('id', 'product-tour');
  await expect(page.locator('.hero-browser-stage .browser-mockup__address')).toContainText('Ваш сайт');
  await expect(page.locator('.hero-browser-stage .browser-mockup__address')).toContainText('teply-hleb.ru');
  await expect(page.locator('#how-it-works')).toHaveCount(0);
  await expect(page.locator('.product-tour__autoplay')).toHaveCount(0);
  await expect(page.locator('.product-tour-section')).toContainText('Реальный кейс · FORMA');

  const caseSectionHeight = await page.locator('#case-study').evaluate((section) =>
    section.getBoundingClientRect().height,
  );
  expect.soft(caseSectionHeight, 'before/after case should fit inside a 1080px screen')
    .toBeLessThanOrEqual(1_080);
  await expectVisibleInside(
    page.locator('.case-panel--after .widget-preview-card'),
    page.locator('.case-panel--after .browser-stack'),
    0.95,
    'case study AI widget',
  );
  const finalDesktopWidgetBox = await page.locator('.final-site-card--after .widget-preview-card').boundingBox();
  expect.soft(
    (finalDesktopWidgetBox?.height ?? 0) / Math.max(finalDesktopWidgetBox?.width ?? 1, 1),
    'final widget should read as a vertical product on desktop',
  ).toBeGreaterThanOrEqual(1.35);
});

test('standalone product tour explains the complete result in one screen @desktop', async ({ page }) => {
  await page.emulateMedia({ reducedMotion: 'reduce' });
  await page.goto('/tour');

  const tour = page.locator('.product-tour-section');
  await expect(tour).toHaveAttribute('data-active-step', '1');
  await expect(tour.locator('.product-tour__autoplay')).toHaveCount(0);
  await expect(tour).toContainText('Реальный кейс · FORMA');
  await expect(tour.locator('img[alt*="FORMA"]').first()).toBeVisible();
  await expectProductTourInsideViewport(page);

  const steps = tour.locator('.product-tour__steps button');
  for (let index = 1; index < 3; index += 1) {
    await steps.nth(index).click();
    await expect(tour).toHaveAttribute('data-active-step', String(index + 1));
    await expectProductTourInsideViewport(page);
  }
  await expect(tour.locator('code')).toContainText('widget.js');
  await expect(tour).toContainText('Tilda');
  await expect(tour).toContainText('одну строку кода');
});

test('standalone product tour stays inside a 768px tablet viewport @desktop', async ({ page }) => {
  await page.setViewportSize({ width: 768, height: 1_024 });
  await page.emulateMedia({ reducedMotion: 'reduce' });
  await page.goto('/tour');

  const tour = page.locator('.product-tour-section');
  const steps = tour.locator('.product-tour__steps button');
  for (let index = 0; index < 3; index += 1) {
    await steps.nth(index).click();
    await expect(tour).toHaveAttribute('data-active-step', String(index + 1));
    await expectProductTourUsable(page);
  }
});

test('scanner travels continuously across card reveals @desktop', async ({ page }) => {
  await page.goto('/');

  const scene = page.getByTestId('hero-scene');
  const scanner = page.getByTestId('hero-scanner');

  await expect(scene).toHaveAttribute('data-visible-cards', '1', { timeout: 15_000 });
  const firstScan = await scanner.evaluate((element) => {
    const styles = getComputedStyle(element);
    return {
      animationName: styles.animationName,
      animationPlayState: styles.animationPlayState,
      y: element.getBoundingClientRect().y,
    };
  });
  expect(firstScan.animationName).toContain('hero-scanner-sweep');
  expect(firstScan.animationPlayState).toBe('running');

  await expect(scene).toHaveAttribute('data-visible-cards', '2', { timeout: 3_000 });
  const secondScan = await scanner.evaluate((element) => {
    const styles = getComputedStyle(element);
    return {
      animationName: styles.animationName,
      y: element.getBoundingClientRect().y,
    };
  });

  expect(secondScan.animationName).toBe(firstScan.animationName);
  expect(secondScan.y - firstScan.y).toBeGreaterThan(50);
});

test('landing navigation, composer, case toggle and FAQ are functional @desktop', async ({ page }) => {
  await page.route('**/api/drafts', async (route) => {
    expect(route.request().method()).toBe('POST');
    expect(route.request().postDataJSON()).toEqual({
      url: 'https://example.com/',
      brief: 'Отвечай кратко',
    });
    await route.fulfill({
      status: 201,
      contentType: 'application/json',
      body: JSON.stringify({ id: 'draft-playwright-opaque' }),
    });
  });
  await page.route('**/api/auth/session', async (route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        enabled: true,
        authenticated: false,
        csrf_token: null,
        pending_draft_id: 'draft-playwright-opaque',
        providers: ['google'],
      }),
    });
  });
  await page.goto('/');

  const productTourLink = page
    .getByRole('navigation', { name: 'Основная навигация' })
    .locator('a[href="#product-tour"]');
  await productTourLink.click();
  await expect(page).toHaveURL(/#product-tour$/);
  await expect(page.locator('#product-tour')).toBeVisible();

  await page.getByRole('navigation', { name: 'Основная навигация' })
    .getByRole('link', { name: 'Кейсы' })
    .click();
  await expect(page).toHaveURL(/#case-study$/);
  await expect(page.getByRole('heading', { name: 'Что меняется для посетителя сайта' })).toBeVisible();

  const faqButton = page.getByRole('button', { name: 'Сколько времени занимает создание?' });
  await faqButton.focus();
  await expect(faqButton).toBeFocused();
  await faqButton.press('Enter');
  await expect(faqButton).toHaveAttribute('aria-expanded', 'true');
  await expect(page.getByText(/Первую версию AI-виджета/)).toBeVisible();

  await page.getByLabel('Ссылка на действующий сайт').first().fill('https://example.com');
  await page.getByRole('textbox', { name: 'Пожелание к AI-виджету' }).first().fill('Отвечай кратко');
  await page.getByRole('button', { name: 'Получить бесплатную версию' }).first().click();
  await expect(page).toHaveURL(/\/studio\?draft=draft-playwright-opaque$/);
  expect(page.url()).not.toContain('example.com');
  expect(page.url()).not.toContain(encodeURIComponent('Отвечай кратко'));
  await expect(page.getByRole('heading', { name: 'Сначала сохраните результат' })).toBeVisible();
});

test('Product Tour mobile contracts at 320px @mobile', async ({ page }) => {
  test.setTimeout(30_000);
  await page.setViewportSize({ width: 320, height: 568 });

  for (const pathname of ['/', '/tour']) {
    await page.goto(pathname);
    const tour = page.locator('.product-tour-section');
    const stepsRail = tour.locator('.product-tour__steps');
    expect.soft(await tour.getAttribute('data-autoplay'), `Product Tour autoplay at 320px on ${pathname}`).toBe('false');
    expect.soft(await stepsRail.getAttribute('data-mobile-snap'), `Product Tour snap marker at 320px on ${pathname}`).toBe('true');
    const snapType = await stepsRail.evaluate((element) => getComputedStyle(element).scrollSnapType);
    expect.soft(snapType, `Product Tour computed snap type at 320px on ${pathname}`).toContain('x mandatory');
    expect.soft(await stepsRail.locator('button').count(), `Product Tour stage buttons at 320px on ${pathname}`).toBe(3);
  }
});

test('landing mobile contracts @mobile', async ({ page }) => {
  test.setTimeout(180_000);
  const viewports = [
    { width: 320, height: 568 },
    { width: 360, height: 800 },
    { width: 390, height: 844 },
    { width: 430, height: 932 },
    { width: 568, height: 320 },
    { width: 844, height: 390 },
  ];

  for (const viewport of viewports) {
    const viewportLabel = `${viewport.width}x${viewport.height}`;
    const mobileLike = viewport.width <= 767 || (viewport.width <= 900 && viewport.height <= 430);
    await page.setViewportSize(viewport);
    await page.goto('/');
    await revealLanding(page);
    expect.soft(await page.getByRole('heading', { name: EXACT_HERO }).isVisible(), `exact hero at ${viewportLabel}`).toBe(true);
    await expectNoHorizontalOverflow(page);

    for (const [name, locator] of [
      ['site header', page.locator('.site-header')],
      ['hero content', page.locator('.hero-section__inner')],
      ['product tour viewport', page.locator('.product-tour__viewport')],
      ['active product tour scene', page.locator('.product-tour__scene[data-active="true"]')],
      ['case comparison', page.locator('.case-comparison')],
      ['active case panel', page.locator('.case-panel.is-mobile-active')],
      ['capability stage', page.locator('.capability-stage')],
      ['Studio layout', page.locator('.studio-layout')],
      ['FAQ layout', page.locator('.faq-layout')],
      ['final visual', page.locator('.final-cta-visual')],
    ] as const) {
      await locator.scrollIntoViewIfNeeded();
      await expectContainedInViewport(locator, `${name} at ${viewportLabel}`);
    }

    const tour = page.locator('.product-tour-section');
    await tour.scrollIntoViewIfNeeded();
    if (mobileLike) {
      expect.soft(await tour.getAttribute('data-autoplay'), `mobile Product Tour must stop autoplay at ${viewportLabel}`).toBe('false');
      const stepsRail = tour.locator('.product-tour__steps');
      expect.soft(await stepsRail.getAttribute('data-mobile-snap'), `mobile Product Tour must expose its snap rail at ${viewportLabel}`).toBe('true');
      const snapType = await stepsRail.evaluate((element) => getComputedStyle(element).scrollSnapType);
      expect.soft(snapType, `mobile Product Tour rail must snap horizontally at ${viewportLabel}`).toContain('x mandatory');
    } else {
      expect.soft(await tour.getAttribute('data-autoplay'), `desktop Product Tour must retain autoplay at ${viewportLabel}`).toBe('true');
    }
    const steps = tour.locator('.product-tour__steps button');
    expect.soft(await steps.count(), `Product Tour must retain three button stages at ${viewportLabel}`).toBe(3);
    expect.soft(await tour.locator('.product-tour__arrows button').count(), `Product Tour arrows must remain available at ${viewportLabel}`).toBe(2);
    for (let index = 0; index < 3; index += 1) {
      await steps.nth(index).click();
      expect.soft(await tour.getAttribute('data-active-step'), `Product Tour stage ${index + 1} at ${viewportLabel}`).toBe(String(index + 1));
      await expectContainedInViewport(
        tour.locator('.product-tour__scene[data-active="true"]'),
        `active Product Tour scene ${index + 1} at ${viewportLabel}`,
      );
      await expectReasonableMobileTourHeight(
        tour,
        viewport,
        `Product Tour stage ${index + 1} at ${viewportLabel}`,
      );
      await expectMobileTextSizes(page, `${viewportLabel}, Product Tour stage ${index + 1}`);
    }
    await expectFormaImagesLoaded(tour, viewportLabel);

    const caseSection = page.locator('#case-study');
    await caseSection.scrollIntoViewIfNeeded();
    const caseHeading = caseSection.getByRole('heading', { name: 'Что меняется для посетителя сайта' });
    expect.soft(await caseHeading.count(), `case heading at ${viewportLabel}`).toBe(1);
    expect.soft(await caseHeading.textContent(), `case heading copy at ${viewportLabel}`).toBe('Что меняется для посетителя сайта');
    expect.soft(await caseSection.locator('.case-panel.is-mobile-active').count(), `one active case panel at ${viewportLabel}`).toBe(1);
    expect.soft(await caseSection.locator('.case-panel--after.is-mobile-active').isVisible(), `after case panel at ${viewportLabel}`).toBe(true);
    const inactiveCasePanel = caseSection.locator('.case-panel:not(.is-mobile-active)');
    expect.soft(await inactiveCasePanel.getAttribute('aria-hidden'), `inactive case panel aria-hidden at ${viewportLabel}`).toBe('true');
    expect.soft(await inactiveCasePanel.getAttribute('inert'), `inactive case panel inert at ${viewportLabel}`).not.toBeNull();
    await expectContainedInViewport(caseSection.locator('.case-heading'), `case heading container at ${viewportLabel}`);
    await expectContainedInViewport(caseSection.locator('.case-comparison'), `case comparison container at ${viewportLabel}`);

    const finalSection = page.locator('#final-cta');
    await finalSection.scrollIntoViewIfNeeded();
    const finalSite = finalSection.locator('.final-site-card--after .mini-site');
    const finalWidget = finalSection.locator('.final-site-card--after .widget-preview-card');
    expect.soft(await finalSite.isVisible(), `final after visual at ${viewportLabel}`).toBe(true);
    expect.soft(await finalWidget.isVisible(), `final after widget at ${viewportLabel}`).toBe(true);
    await expectVisibleInsideSoft(finalWidget, finalSite, `final after widget at ${viewportLabel}`);

    await expectMobileTouchTargets(page, viewportLabel);
  }

  for (const viewport of viewports) {
    const viewportLabel = `${viewport.width}x${viewport.height}`;
    const mobileLike = viewport.width <= 767 || (viewport.width <= 900 && viewport.height <= 430);
    await page.setViewportSize(viewport);
    await page.goto('/tour');
    const tour = page.locator('.product-tour-section--standalone');
    if (mobileLike) {
      expect.soft(await tour.getAttribute('data-autoplay'), `standalone Product Tour autoplay at ${viewportLabel}`).toBe('false');
      const stepsRail = tour.locator('.product-tour__steps');
      expect.soft(await stepsRail.getAttribute('data-mobile-snap'), `standalone tour must expose its snap rail at ${viewportLabel}`).toBe('true');
      const snapType = await stepsRail.evaluate((element) => getComputedStyle(element).scrollSnapType);
      expect.soft(snapType, `standalone tour rail must snap horizontally at ${viewportLabel}`).toContain('x mandatory');
    } else {
      expect.soft(await tour.getAttribute('data-autoplay'), `standalone Product Tour must retain autoplay at ${viewportLabel}`).toBe('true');
    }
    const standaloneSteps = tour.locator('.product-tour__steps button');
    for (let index = 0; index < 3; index += 1) {
      await standaloneSteps.nth(index).click();
      await expectReasonableMobileTourHeight(
        tour,
        viewport,
        `standalone Product Tour stage ${index + 1} at ${viewportLabel}`,
      );
    }
    const cta = tour.getByRole('link', { name: 'Создать бесплатную версию' });
    const ctaVisible = await cta.isVisible();
    expect.soft(ctaVisible, `standalone CTA at ${viewportLabel}`).toBe(true);
    if (ctaVisible) await expectContainedInViewport(cta, `standalone CTA at ${viewportLabel}`);
    await expectFormaImagesLoaded(tour, `standalone ${viewportLabel}`);
  }
});

test('landing mobile preserves content order, menu, controls and comparison @mobile', async ({ page }, testInfo) => {
  test.setTimeout(90_000);
  await page.goto('/');
  await expect(page.getByRole('heading', { name: EXACT_HERO })).toBeVisible();
  await expectNoHorizontalOverflow(page);

  const copyBeforeScene = await page.evaluate(() => {
    const copy = document.querySelector('.hero-copy');
    const scene = document.querySelector('.hero-scene');
    return Boolean(copy && scene && (copy.compareDocumentPosition(scene) & Node.DOCUMENT_POSITION_FOLLOWING));
  });
  expect(copyBeforeScene).toBe(true);

  const menuButton = page.getByRole('button', { name: 'Открыть меню' });
  await expectMinimumTarget(menuButton);
  await menuButton.click();
  const mobileNavigation = page.getByRole('navigation', { name: 'Мобильная навигация' });
  await expect(mobileNavigation).toBeVisible();
  await mobileNavigation.getByRole('link', { name: 'Продукт' }).click();
  await expect(page).toHaveURL(/#product$/);
  await expect(page.getByRole('button', { name: 'Открыть меню' })).toBeFocused();

  await expectMinimumTarget(page.getByRole('button', { name: 'Получить бесплатную версию' }).first());

  const caseSection = page.locator('#case-study');
  await caseSection.scrollIntoViewIfNeeded();
  const before = caseSection.getByRole('button', { name: 'До', exact: true });
  const after = caseSection.getByRole('button', { name: 'После', exact: true });
  await expect(after).toHaveAttribute('aria-pressed', 'true');
  await before.click();
  await expect(before).toHaveAttribute('aria-pressed', 'true');
  await expect(after).toHaveAttribute('aria-pressed', 'false');
  await after.click();
  await expect(after).toHaveAttribute('aria-pressed', 'true');
  await expectNoHorizontalOverflow(page);
  const undersizedButtons = await page.locator('button:visible').evaluateAll((buttons) =>
    buttons.flatMap((button) => {
      const box = button.getBoundingClientRect();
      if (box.width >= 44 && box.height >= 44) return [];
      return [{
        name: button.getAttribute('aria-label') || button.textContent?.trim() || button.className,
        width: Math.round(box.width * 10) / 10,
        height: Math.round(box.height * 10) / 10,
      }];
    }),
  );
  expect(undersizedButtons).toEqual([]);

  await revealLanding(page);
  await expect(page.locator('#how-it-works')).toHaveCount(0);

  const finalWidget = page.locator('.final-site-card--after .widget-preview-card');
  const finalSite = page.locator('.final-site-card--after .mini-site');
  await expectVisibleInside(finalWidget, finalSite, 0.95, 'final vertical widget');
  const finalWidgetBox = await finalWidget.boundingBox();
  expect.soft(
    (finalWidgetBox?.height ?? 0) / Math.max(finalWidgetBox?.width ?? 1, 1),
    'final widget must remain vertical on mobile',
  ).toBeGreaterThanOrEqual(1.4);
  const finalWidgetFontSize = await finalWidget.evaluate((element) =>
    Number.parseFloat(getComputedStyle(element).fontSize),
  );
  expect.soft(finalWidgetFontSize, 'final widget text must remain legible on mobile')
    .toBeGreaterThanOrEqual(8);
  await attachScreenshot(page, testInfo, 'landing-mobile-390.png', { fullPage: true });
});

test('landing feedback stores a consented message through the API contract @mobile @desktop', async ({ page }, testInfo) => {
  test.setTimeout(180_000);
  const captured: Array<{
    headers: Record<string, string>;
    body: Record<string, unknown>;
  }> = [];
  let sessionRequests = 0;

  await page.route('**/api/feedback/session', async (route) => {
    sessionRequests += 1;
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        csrf_token: 'csrf-feedback',
        consent_version: 'feedback-v2',
        message_max_length: 4000,
      }),
    });
  });
  await page.route('**/api/feedback', async (route) => {
    if (route.request().method() !== 'POST') {
      await route.continue();
      return;
    }
    captured.push({
      headers: route.request().headers(),
      body: route.request().postDataJSON() as Record<string, unknown>,
    });
    await route.fulfill({
      status: 201,
      contentType: 'application/json',
      body: JSON.stringify({
        receipt_id: `fb_e2e_${captured.length}`,
        status: 'stored',
        received_at: '2026-08-17T10:00:00Z',
      }),
    });
  });

  for (const viewport of viewportsForProject(testInfo)) {
    const viewportLabel = `${viewport.width}x${viewport.height}`;
    captured.length = 0;
    sessionRequests = 0;
    await page.setViewportSize(viewport);
    await page.goto('/');

    const section = page.locator('#contact');
    await section.scrollIntoViewIfNeeded();
    await expect(section.getByRole('heading', { name: 'Есть вопрос или идея?' })).toBeVisible();
    await expect(section.locator('a[href^="mailto:"]')).toHaveCount(0);
    await expect(page.locator('footer a[href^="mailto:"]')).toHaveCount(0);
    await expect(page.locator('body')).not.toContainText(/[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}/i);

    const form = section.locator('form.feedback-composer');
    const message = `Проверка обратной связи ${viewportLabel}`;
    await form.getByLabel('Сообщение').fill(message);
    await form.getByRole('button', { name: 'Отправить' }).click();
    await expect(form.getByRole('status')).toHaveText(
      'Спасибо. Сообщение сохранено и поможет улучшать Kaigo.',
    );

    expect(sessionRequests, `one landing session request at ${viewportLabel}`).toBe(1);
    expect(captured, `one stored landing request at ${viewportLabel}`).toHaveLength(1);
    expect(captured[0].headers['x-csrf-token']).toBe('csrf-feedback');
    expect(captured[0].headers['idempotency-key']).toMatch(/^feedback-/);
    expect(captured[0].body).toEqual({
      topic: 'question',
      message,
      source: 'landing_contact',
      consent: { version: 'feedback-v2', accepted: true },
      honeypot: '',
    });
    for (const forbiddenField of [
      'email', 'contact', 'name', 'page', 'project', 'project_id', 'run', 'run_id', 'domain', 'context',
    ]) {
      expect(captured[0].body, `${forbiddenField} must not be client supplied`).not.toHaveProperty(forbiddenField);
    }

    if (viewport.width <= 767) {
      await expectNoHorizontalOverflow(page);
      const undersizedFeedbackTargets = await section.locator(
        'a[href]:visible, button:visible, textarea:visible, .feedback-topic:visible',
      ).evaluateAll((elements) => elements.flatMap((element) => {
        const bounds = element.getBoundingClientRect();
        if (bounds.width >= 44 && bounds.height >= 44) return [];
        return [{
          name: element.getAttribute('aria-label') || element.textContent?.trim().slice(0, 80) || element.tagName,
          width: Math.round(bounds.width * 10) / 10,
          height: Math.round(bounds.height * 10) / 10,
        }];
      }));
      expect(undersizedFeedbackTargets, `feedback targets at ${viewportLabel}`).toEqual([]);
    }
  }
});

test('landing feedback retains text and replays an ambiguous 500 with the same request identity @mobile @desktop', async ({ page }) => {
  test.setTimeout(60_000);
  const posts: Array<{
    headers: Record<string, string>;
    body: Record<string, unknown>;
  }> = [];
  let sessionRequests = 0;

  await page.route('**/api/feedback/session', async (route) => {
    sessionRequests += 1;
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        csrf_token: 'csrf-feedback',
        consent_version: 'feedback-v2',
        message_max_length: 4000,
      }),
    });
  });
  await page.route('**/api/feedback', async (route) => {
    if (route.request().method() !== 'POST') {
      await route.continue();
      return;
    }
    posts.push({
      headers: route.request().headers(),
      body: route.request().postDataJSON() as Record<string, unknown>,
    });
    if (posts.length === 1) {
      await route.fulfill({
        status: 500,
        contentType: 'application/json',
        body: JSON.stringify({ error: { code: 'temporary_failure' } }),
      });
      return;
    }
    await route.fulfill({
      status: 201,
      contentType: 'application/json',
      body: JSON.stringify({
        receipt_id: 'fb_e2e_retry',
        status: 'stored',
        received_at: '2026-08-17T10:00:00Z',
      }),
    });
  });

  await page.goto('/');
  const section = page.locator('#contact');
  await section.scrollIntoViewIfNeeded();
  const form = section.locator('form.feedback-composer');
  const message = 'Проверка повтора после временной ошибки';
  await form.getByLabel('Сообщение').fill(message);
  await form.getByRole('button', { name: 'Отправить' }).click();
  await expect(form.getByRole('alert')).toHaveText('Не удалось отправить. Ваш текст остался в форме.');
  await expect(form.getByLabel('Сообщение')).toHaveValue(message);
  await form.getByRole('button', { name: 'Повторить' }).click();
  await expect(form.getByRole('status')).toHaveText(
    'Спасибо. Сообщение сохранено и поможет улучшать Kaigo.',
  );

  expect(sessionRequests).toBe(1);
  expect(posts).toHaveLength(2);
  expect(posts[1].body).toEqual(posts[0].body);
  expect(posts[1].headers['idempotency-key']).toBe(posts[0].headers['idempotency-key']);
  expect(posts[1].headers['x-csrf-token']).toBe(posts[0].headers['x-csrf-token']);
});

test('all legal pages stay readable and touch-safe on supported viewports @mobile @desktop', async ({ page }, testInfo) => {
  test.setTimeout(180_000);
  const legalRoutes = [
    ['/privacy/', 'Политика конфиденциальности'],
    ['/personal-data-consent/', 'Согласие на обработку персональных данных'],
    ['/terms/', 'Условия использования'],
    ['/offer/', 'Предварительная публичная оферта'],
  ] as const;
  const viewports = viewportsForProject(testInfo);

  for (const viewport of viewports) {
    const viewportLabel = `${viewport.width}x${viewport.height}`;
    await page.setViewportSize(viewport);
    for (const [pathname, title] of legalRoutes) {
      await page.goto(pathname);
      await expectLegalDocumentContent(page, title, `${pathname} at ${viewportLabel}`);
      if (viewport.width <= 767) {
        await expectNoHorizontalOverflow(page);
        await expectMobileTouchTargets(page, `${pathname} at ${viewportLabel}`);
      }
      if (viewport.width === 320) {
        const legalResults = await new AxeBuilder({ page })
          .withTags(['wcag2a', 'wcag2aa'])
          .analyze();
        const blocking = legalResults.violations.filter(({ impact }) =>
          impact === 'serious' || impact === 'critical',
        );
        expect(blocking, `${pathname} at ${viewportLabel}`).toEqual([]);
      }
    }
  }
});

test('standalone product tour keeps every step usable on mobile @mobile', async ({ page }) => {
  await page.emulateMedia({ reducedMotion: 'reduce' });
  await page.goto('/tour');

  const tour = page.locator('.product-tour-section');
  const returnLink = page.locator('.product-tour-page__header > a:last-child');
  await expectMinimumTarget(returnLink, 44, 'return to landing link');
  await expectProductTourUsable(page);

  const steps = tour.locator('.product-tour__steps button');
  for (let index = 0; index < 3; index += 1) {
    await steps.nth(index).click();
    await expect(tour).toHaveAttribute('data-active-step', String(index + 1));
    await expectProductTourUsable(page);
  }

  await expectVisibleInside(
    tour.locator('.tour-publish-visual .tour-site-frame'),
    tour.locator('.tour-publish-visual .tour-window'),
    0.95,
    'published FORMA site',
  );
});

test('mobile landing and every tour step have no serious accessibility violations @mobile', async ({ page }) => {
  test.setTimeout(180_000);
  await page.emulateMedia({ reducedMotion: 'reduce' });
  await page.goto('/');
  await revealLanding(page);

  const menuButton = page.getByRole('button', { name: 'Открыть меню' });
  await menuButton.click();
  await expect(page.getByRole('navigation', { name: 'Мобильная навигация' })).toBeVisible();
  const landingResults = await new AxeBuilder({ page })
    .withTags(['wcag2a', 'wcag2aa'])
    .analyze();
  expect(landingResults.violations.filter(({ impact }) =>
    impact === 'serious' || impact === 'critical')).toEqual([]);
  await page.keyboard.press('Escape');

  await page.goto('/tour');
  const tour = page.locator('.product-tour-section');
  const steps = tour.locator('.product-tour__steps button');
  for (let index = 0; index < 3; index += 1) {
    await steps.nth(index).click();
    await expect(tour).toHaveAttribute('data-active-step', String(index + 1));
    const tourResults = await new AxeBuilder({ page })
      .withTags(['wcag2a', 'wcag2aa'])
      .analyze();
    expect(
      tourResults.violations.filter(({ impact }) =>
        impact === 'serious' || impact === 'critical'),
      `mobile standalone product tour stage ${index + 1}`,
    ).toEqual([]);
  }
});

test('landing reaches the final hero state immediately with reduced motion @reduced', async ({ page }) => {
  await page.emulateMedia({ reducedMotion: 'reduce' });
  await page.goto('/');
  await expect(page.getByTestId('hero-scene')).toHaveAttribute('data-motion-phase', 'complete');
  await expect(page.getByTestId('process-card')).toHaveCount(3);
  await expect(page.locator('[data-testid="widget-preview"][data-visible="true"]')).toBeVisible();

  const infiniteAnimations = await page.evaluate(() =>
    document.getAnimations().filter((animation) => {
      const timing = animation.effect?.getComputedTiming();
      return animation.playState === 'running' && timing?.iterations === Number.POSITIVE_INFINITY;
    }).length,
  );
  expect(infiniteAnimations).toBe(0);
});

test('landing has no serious or critical accessibility violations @a11y', async ({ page }) => {
  test.setTimeout(90_000);
  await page.emulateMedia({ reducedMotion: 'reduce' });
  await page.goto('/');
  await revealLanding(page);
  const results = await new AxeBuilder({ page })
    .withTags(['wcag2a', 'wcag2aa'])
    .analyze();
  const blocking = results.violations.filter(({ impact }) =>
    impact === 'serious' || impact === 'critical',
  );
  expect(blocking).toEqual([]);

  await page.goto('/tour');
  const tour = page.locator('.product-tour-section');
  const steps = tour.locator('.product-tour__steps button');
  for (let index = 0; index < 3; index += 1) {
    await steps.nth(index).click();
    await expect(tour).toHaveAttribute('data-active-step', String(index + 1));
    const standaloneResults = await new AxeBuilder({ page })
      .withTags(['wcag2a', 'wcag2aa'])
      .analyze();
    const standaloneBlocking = standaloneResults.violations.filter(({ impact }) =>
      impact === 'serious' || impact === 'critical',
    );
    expect(standaloneBlocking, `standalone product tour stage ${index + 1}`).toEqual([]);
  }
});
