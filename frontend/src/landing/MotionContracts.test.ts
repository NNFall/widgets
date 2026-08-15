import { describe, expect, it } from 'vitest';

import { WIDGET_LAUNCHER_REPEAT_DELAY_SECONDS } from '../shared/BrowserMockup';
import revealSource from '../shared/Reveal.tsx?raw';
import stylesSource from '../styles.css?raw';
import {
  CAPABILITY_CARD_STAGGER_SECONDS,
  CAPABILITY_REVEAL_DELAYS_SECONDS,
} from './CapabilitiesSection';
import heroOrbitSceneSource from './HeroOrbitScene.tsx?raw';
import analysisSectionSource from './AnalysisSection.tsx?raw';
import faqSectionSource from './FaqSection.tsx?raw';
import finalCtaSectionSource from './FinalCtaSection.tsx?raw';
import howItWorksSectionSource from './HowItWorksSection.tsx?raw';
import studioSectionSource from './StudioSection.tsx?raw';

describe('landing motion contracts', () => {
  it('preserves the capability cadence while simplifying the process story', () => {
    expect(CAPABILITY_CARD_STAGGER_SECONDS).toBe(0.26);
  });

  it('uses invisible transform-only Reveal presets with a reduced-motion shortcut', () => {
    expect(revealSource).toMatch(/heading:\s*\{[^}]*opacity:\s*0,[^}]*y:/s);
    expect(revealSource).toMatch(/fromLeft:\s*\{[^}]*opacity:\s*0,[^}]*x:\s*'clamp\(-65px, -5vw, -36px\)'[^}]*scale:\s*0\.92/s);
    expect(revealSource).toMatch(/fromRight:\s*\{[^}]*opacity:\s*0,[^}]*x:\s*'clamp\(36px, 5vw, 65px\)'[^}]*scale:\s*0\.92/s);
    expect(revealSource).toMatch(/scale:\s*\{[^}]*opacity:\s*0,[^}]*scale:\s*0\.9/s);
    expect(revealSource).toContain(
      'initial={reducedMotion || !viewportMotionAvailable ? false : hiddenByPreset[preset]}',
    );
  });

  it('uses one integrated process surface without an animated route over content', () => {
    expect(howItWorksSectionSource).toContain('data-journey-layout="unified"');
    expect(howItWorksSectionSource).toContain('data-testid="how-live-preview"');
    expect(howItWorksSectionSource).not.toContain('className="how-route__path"');
    expect(howItWorksSectionSource).not.toContain("index % 2 === 0 ? 'fromLeft' : 'fromRight'");
  });

  it('gates only the scan and ready signals behind the section activity boundary', () => {
    expect(howItWorksSectionSource).toContain('useMotionActivity<HTMLElement>()');
    expect(howItWorksSectionSource).toContain("data-motion-active={active ? 'true' : 'false'}");
    expect(howItWorksSectionSource).toContain('how-stage__scan-line');
    expect(howItWorksSectionSource).toContain('how-stage__status-dot');
  });

  it('keeps the analysis browser rotation inside its scale entrance and exposes focus targets', () => {
    expect(analysisSectionSource).toContain('useMotionActivity<HTMLElement>()');
    expect(analysisSectionSource).toContain('className="analysis-browser__entrance"');
    expect(analysisSectionSource).toContain('preset="scale"');
    expect(analysisSectionSource).toContain('className="analysis-browser"');
    expect(analysisSectionSource).toContain('className="analysis-focus-ring"');
    expect(analysisSectionSource.match(/data-analysis-target=/g)).toHaveLength(4);
    expect(analysisSectionSource).toContain('motionComplete={active}');
    expect(analysisSectionSource).toContain('reducedMotion={reducedMotion}');
    expect(analysisSectionSource).not.toContain('reducedMotion={!active}');
  });

  it('drives focus, lenses, and notes from one shared observation phase', () => {
    expect(analysisSectionSource).toContain('useAnalysisObservationCycle(active)');
    expect(analysisSectionSource).toContain('data-analysis-focus={observation}');
    expect(analysisSectionSource).toContain('data-analysis-kind="lens"');
    expect(analysisSectionSource).toContain('data-analysis-kind="note"');
    expect(analysisSectionSource).toContain("data-analysis-active={observation === target ? 'true' : 'false'}");
    expect(stylesSource).not.toContain('@keyframes analysis-focus-travel');
    expect(stylesSource).not.toContain('@keyframes analysis-lens-breathe');
  });

  it('gates every new infinite section animation and disables it for reduced motion', () => {
    const activeLoops = [
      'how-stage-scan',
      'how-status-pulse',
    ];

    activeLoops.forEach((name) => {
      expect(stylesSource).toMatch(
        new RegExp(`\\[data-motion-active='true'\\][^{}]*\\{[^}]*animation:[^;]*${name}[^;]*infinite`, 's'),
      );
    });

    const reducedMotionStart = stylesSource.indexOf('@media (prefers-reduced-motion: reduce)');
    const nextMediaQuery = stylesSource.indexOf('@media (max-width: 1280px)', reducedMotionStart);
    const reducedMotionRules = stylesSource.slice(reducedMotionStart, nextMediaQuery);
    expect(reducedMotionRules).toContain('.how-section');
    expect(reducedMotionRules).toContain('.analysis-section');
    expect(reducedMotionRules).toContain('animation: none !important;');
    expect(reducedMotionRules).toContain('.how-stage__scan-line');
    expect(reducedMotionRules).toContain('.how-stage__status-dot');
  });

  it('keeps all process and analysis keyframes on transform and opacity only', () => {
    const sectionKeyframes = [
      'how-stage-scan',
      'how-status-pulse',
    ];

    sectionKeyframes.forEach((name) => {
      const body = stylesSource.match(new RegExp(`@keyframes ${name}\\s*\\{([\\s\\S]*?)\\n\\}`))?.[1];
      expect(body, `missing @keyframes ${name}`).toBeDefined();
      expect(body).toMatch(/(?:transform|opacity):/);
      expect(body).not.toMatch(/(?:top|right|bottom|left|width|height|filter|box-shadow):/);
    });
  });

  it('reveals all six capability cards in one alternating sequence without pairs', () => {
    expect(CAPABILITY_REVEAL_DELAYS_SECONDS).toEqual([0, 0.26, 0.52, 0.78, 1.04, 1.3]);
    expect(new Set(CAPABILITY_REVEAL_DELAYS_SECONDS).size).toBe(6);
  });

  it('keeps the recurring launcher animation on a restrained 13 second cycle', () => {
    const pulseDurationSeconds = 2.3;
    const totalCycleSeconds = pulseDurationSeconds + WIDGET_LAUNCHER_REPEAT_DELAY_SECONDS;

    expect(totalCycleSeconds).toBeGreaterThanOrEqual(12);
    expect(totalCycleSeconds).toBeLessThanOrEqual(15);
    expect(totalCycleSeconds).toBe(13);
  });

  it('matches scanner travel to the distinct cinematic and loop scanning phases', () => {
    expect(stylesSource).toMatch(
      /data-motion-program='cinematic'\]\s*\{[^}]*--hero-scanner-duration:\s*6\.3s;/s,
    );
    expect(stylesSource).toMatch(
      /data-motion-program='loop'\]\s*\{[^}]*--hero-scanner-duration:\s*3\.8s;/s,
    );
    expect(stylesSource).toMatch(
      /\.hero-browser-stage__scanner\[data-active='true'\]\s*\{[^}]*animation:\s*hero-scanner-sweep var\(--hero-scanner-duration\) linear both;/s,
    );

    const scannerMarkup = heroOrbitSceneSource.slice(
      heroOrbitSceneSource.indexOf('className="hero-browser-stage__scanner"'),
      heroOrbitSceneSource.indexOf('data-testid="hero-scanner-band"'),
    );
    expect(scannerMarkup).not.toContain('animate=');
    expect(scannerMarkup).not.toContain('transition=');
    expect(heroOrbitSceneSource).not.toContain('SCANNER_DURATION_SECONDS');
  });

  it('keeps every new CSS motion keyframe on transform and opacity only', () => {
    const motionKeyframes = [
      'hero-scanner-sweep',
      'hero-scanner-particle',
      'hero-widget-halo',
      'hero-widget-burst',
      'hero-rest-shimmer',
    ];

    motionKeyframes.forEach((name) => {
      const body = stylesSource.match(new RegExp(`@keyframes ${name}\\s*\\{([\\s\\S]*?)\\n\\}`))?.[1];
      expect(body, `missing @keyframes ${name}`).toBeDefined();
      expect(body).toMatch(/(?:transform|opacity):/);
      expect(body).not.toMatch(/(?:top|right|bottom|left|width|height|filter|box-shadow):/);
    });
  });

  it('explicitly disables every new infinite hero animation for reduced motion', () => {
    const reducedMotionStart = stylesSource.indexOf('@media (prefers-reduced-motion: reduce)');
    const nextMediaQuery = stylesSource.indexOf('@media (max-width: 1280px)', reducedMotionStart);
    const reducedMotionRules = stylesSource.slice(reducedMotionStart, nextMediaQuery);

    expect(reducedMotionRules).toContain('.hero-browser-stage__scanner-particle');
    expect(reducedMotionRules).toContain('.hero-browser-stage__scanner');
    expect(reducedMotionRules).toContain('.widget-preview__shimmer');
    expect(reducedMotionRules).toContain('animation: none !important;');
    expect(heroOrbitSceneSource).toMatch(/motionComplete\s*&&\s*motionActive[\s\S]*?'rest'/);
  });

  it('isolates complete-rest drift to one card and settles the others', () => {
    expect(heroOrbitSceneSource).toMatch(
      /settled:\s*\{[\s\S]*?opacity:\s*1,[\s\S]*?x:\s*'0px',[\s\S]*?y:\s*'0px'/,
    );
    expect(heroOrbitSceneSource).toMatch(
      /motionComplete\s*&&\s*motionActive[\s\S]*?index\s*===\s*cycle\s*%\s*processCards\.length[\s\S]*?\?\s*'rest'\s*:\s*'settled'/,
    );
  });

  it('keeps the truthful landing Studio preview activity-gated and free of fake interactions', () => {
    expect(studioSectionSource).toContain('useMotionActivity<HTMLElement>()');
    expect(studioSectionSource).toContain("data-motion-active={active ? 'true' : 'false'}");
    expect(studioSectionSource).toContain('className="studio-demo__checklist"');
    expect(studioSectionSource).toContain('motionActive={active}');
    expect(studioSectionSource).toContain('reducedMotion={reducedMotion}');
    expect(studioSectionSource).not.toContain('className="studio-demo__ambient-cursor"');
    expect(studioSectionSource).not.toContain('className="studio-demo__preview-wipe"');
    expect(studioSectionSource).not.toContain('className="studio-demo__version-confirmation"');
    expect(studioSectionSource).not.toContain('setTimeout');
    expect(studioSectionSource).not.toContain('setInterval');
    expect(studioSectionSource).not.toContain('useEffect');

    [
      'studio-cursor-cycle',
      'studio-cursor-click',
      'studio-apply-press',
      'studio-version-confirmation',
      'studio-preview-refresh',
    ].forEach((name) => {
      expect(stylesSource).not.toContain(name);
    });
  });

  it('gives FAQ panels a real hidden spring start and alternates item entrances', () => {
    expect(faqSectionSource).toContain("initial={reducedMotion ? false : { height: 0, opacity: 0 }}");
    expect(faqSectionSource).toContain("animate={{ height: 'auto', opacity: 1 }}");
    expect(faqSectionSource).toContain("exit={reducedMotion ? undefined : { height: 0, opacity: 0 }}");
    expect(faqSectionSource).toContain("index % 2 === 0 ? 'fromLeft' : 'fromRight'");
    expect(faqSectionSource).toContain('delay={0.06 + index * 0.09}');
    expect(faqSectionSource).toContain('aria-expanded={open}');
    expect(faqSectionSource).toContain('aria-controls={panelId}');
    expect(faqSectionSource).not.toContain('setTimeout');
    expect(faqSectionSource).not.toContain('setInterval');
  });

  it('stages the final CTA once from one parent and leaves the reusable footer to the page shell', () => {
    expect(finalCtaSectionSource).toContain('className="landing-shell final-cta-content final-cta-motion"');
    expect(finalCtaSectionSource).toContain("initial={reducedMotion || !viewportMotionAvailable ? false : 'hidden'}");
    expect(finalCtaSectionSource).toContain('whileInView="visible"');
    expect(finalCtaSectionSource).toContain('viewport={{ once: true, amount: 0.18 }}');
    expect(finalCtaSectionSource).toContain('className="final-site-card__motion final-site-card__motion--before"');
    expect(finalCtaSectionSource).toContain('className="final-site-card__motion final-site-card__motion--after"');
    expect(finalCtaSectionSource).toContain('className="mini-site__widget-halo"');

    expect(finalCtaSectionSource).not.toContain('<footer className="site-footer">');
  });

  it('keeps the remaining Studio preview motion reduced-motion safe', () => {
    const reducedMotionStart = stylesSource.indexOf('@media (prefers-reduced-motion: reduce)');
    const nextMediaQuery = stylesSource.indexOf('@media (max-width: 1280px)', reducedMotionStart);
    const reducedMotionRules = stylesSource.slice(reducedMotionStart, nextMediaQuery);
    expect(reducedMotionRules).toContain('.mini-site__widget-halo');
    expect(reducedMotionRules).toContain('animation: none !important;');
    expect(reducedMotionRules).not.toContain('.studio-demo__ambient-cursor');
    expect(reducedMotionRules).not.toContain('.studio-demo__preview-wipe');
  });
});
