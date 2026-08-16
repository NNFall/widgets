import { cleanup, render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it } from 'vitest';

import stylesSource from '../styles.css?raw';
import studioPageSource from './StudioPage.tsx?raw';
import { StudioProgress } from './StudioProgress';
import { StudioTimeline } from './StudioTimeline';
import type { BuilderEvent } from './types';

afterEach(cleanup);

const technicalEvent: BuilderEvent = {
  run_id: 'run-visual-repair',
  sequence: 12,
  timestamp: '2026-08-02T15:24:00Z',
  type: 'visual_repair.started',
  stage: 'motion_polish',
  status: 'running',
  message: 'Gemini bytes=991; C:\\secret\\trace.log',
  revision: 4,
  usage: { prompt_tokens: 0, output_tokens: 0, thinking_tokens: 0, total_tokens: 0 },
  issues: [{
    code: 'browser_gate_failed',
    field: 'body_html/css',
    message: "ValueError: evidence={'changing': False}",
  }],
  changes: ['javascript'],
  error_code: null,
};

describe('Studio accessibility contracts', () => {
  it('exposes ordered progress and one atomic polite activity region', () => {
    render(
      <StudioProgress
        status="running"
        progress={48}
        currentStage="conversation"
        lastCompletedStage="identity"
        events={[]}
        activityFallback="Настраиваем диалог"
      />,
    );

    expect(screen.getByRole('list', { name: 'Этапы создания виджета' }).tagName).toBe('OL');
    expect(screen.getAllByRole('listitem')).toHaveLength(7);
    expect(screen.getByRole('status')).toHaveAttribute('aria-live', 'polite');
    expect(screen.getByRole('status')).toHaveAttribute('aria-atomic', 'true');
  });

  it('keeps technical details secondary and never renders raw diagnostics', () => {
    render(<StudioTimeline events={[technicalEvent]} running />);

    const disclosure = screen.getByText('Технические детали').closest('details');
    expect(disclosure).not.toHaveAttribute('open');
    expect(screen.getByText(/Исправляем найденные визуальные детали/)).toBeInTheDocument();
    expect(screen.getByText(/шаг 12/)).toBeInTheDocument();
    expect(screen.queryByText(/Gemini|bytes=|secret|ValueError|evidence=|javascript/)).not.toBeInTheDocument();
  });

  it('keeps disclosure controls large enough for touch and keyboard users', () => {
    expect(stylesSource).toMatch(/\.studio-technical\s+summary\s*\{[^}]*min-height:\s*44px/s);
  });

  it('removes CSS and Motion spring movement when reduced motion is requested', () => {
    const reducedStart = stylesSource.indexOf('@media (prefers-reduced-motion: reduce) {\n  .studio-drawer,');
    const reducedEnd = stylesSource.indexOf('@keyframes studio-agent-pulse', reducedStart);
    const reducedMotionRules = stylesSource.slice(reducedStart, reducedEnd);
    expect(reducedStart).toBeGreaterThan(-1);
    expect(reducedMotionRules).toMatch(/\.studio-activity__message[\s\S]*animation:\s*none !important/);
    expect(reducedMotionRules).toMatch(/\.studio-library__project\s*>\s*button[\s\S]*transition:\s*none !important/);
    expect(studioPageSource).toMatch(/useReducedMotion\(\)/);
    expect(studioPageSource).toMatch(/reducedMotion\s*\?\s*\{\s*duration:\s*0\s*\}/);
  });

  it('locks the project workbench to the viewport and scrolls only inside its panes', () => {
    expect(stylesSource).toMatch(/\.studio-app--workbench\s*\{[^}]*height:\s*100dvh[^}]*overflow:\s*hidden/s);
    expect(stylesSource).toMatch(/\.studio-workbench\s*\{[^}]*grid-template-columns:\s*minmax\(520px,\s*4fr\)\s+minmax\(520px,\s*5fr\)/s);
    expect(stylesSource).toMatch(/\.studio-conversation__feed\s*\{[^}]*overflow-y:\s*auto/s);
    expect(stylesSource).toMatch(/\.studio-preview-pane\s*\{[^}]*min-height:\s*0[^}]*overflow:\s*hidden/s);
    expect(stylesSource).toMatch(/min-height:\s*44px/);
    expect(stylesSource).toMatch(/@media \(max-width:\s*390px\)/);
  });

  it('uses explicit chat and preview panes on compact screens', () => {
    const mobileRules = stylesSource.slice(stylesSource.indexOf('@media (max-width: 860px)'));
    expect(mobileRules).toMatch(/\.studio-workbench__mobile-tabs\s*\{[^}]*display:\s*grid/s);
    expect(mobileRules).toMatch(/\.studio-workbench\[data-mobile-pane='chat'\][\s\S]*\.studio-preview-pane\s*\{[^}]*display:\s*none/s);
    expect(mobileRules).toMatch(/\.studio-workbench\[data-mobile-pane='preview'\][\s\S]*\.studio-conversation\s*\{[^}]*display:\s*none/s);
  });

  it('gives the conversation visual priority and keeps preview metadata on one line', () => {
    expect(stylesSource).toMatch(/\.studio-message\s+p\s*\{[^}]*font-size:\s*14px/s);
    expect(stylesSource).toMatch(/\.studio-conversation\s+\.studio-stages\s+li\s*\{[^}]*font-size:\s*13px/s);
    expect(stylesSource).toMatch(/\.studio-conversation__composer\s+textarea\s*\{[^}]*font-size:\s*15px/s);
    expect(stylesSource).toMatch(/\.studio-preview__head--compact\s*\{[^}]*grid-template-columns:\s*minmax\(0,\s*1fr\)\s+auto/s);
    expect(stylesSource).toMatch(/\.studio-preview__title-row\s*\{[^}]*display:\s*flex[^}]*align-items:\s*center/s);
  });

  it('uses the Kaigo coral accent for the active conversation', () => {
    expect(stylesSource).toMatch(/\.studio-conversation__avatar\s*\{[^}]*background:\s*var\(--coral\)/s);
    expect(stylesSource).toMatch(/\.studio-conversation\s+\.studio-progress-card__bar\s+span\s*\{[^}]*background:\s*var\(--coral\)/s);
  });

  it('keeps the publication action legible through hover and press states', () => {
    expect(stylesSource).toMatch(
      /\.studio-app--workbench\s+\.studio-header__actions\s+\.studio-header__publish\[data-ready='true'\]:hover:not\(:disabled\)\s*\{[^}]*color:\s*#fff[^}]*background:\s*#a83f20/s,
    );
    expect(stylesSource).toMatch(
      /\.studio-app--workbench\s+\.studio-header__actions\s+\.studio-header__publish\[data-ready='false'\]:hover:not\(:disabled\)\s*\{[^}]*color:\s*#6f3826[^}]*background:\s*#ffe8dc/s,
    );
    expect(stylesSource).toMatch(
      /\.studio-app--workbench\s+\.studio-header__actions\s+\.studio-header__publish:active:not\(:disabled\)\s*\{[^}]*translateY\(0\)\s+scale\(\.98\)/s,
    );
  });

  it('keeps Studio support actions touch-safe and the contact drawer scrollable on mobile', () => {
    expect(stylesSource).toMatch(
      /\.studio-help-button\s*\{[^}]*min-height:\s*44px[^}]*min-width:\s*44px/s,
    );
    expect(stylesSource).toMatch(
      /\.studio-contact-panel\s*\{[^}]*min-width:\s*0/s,
    );
    expect(stylesSource).toMatch(
      /\.studio-drawer__body\s*\{[^}]*min-height:\s*0[^}]*overflow-y:\s*auto/s,
    );
    expect(stylesSource).toMatch(
      /@media \(max-width:\s*560px\)[\s\S]*\.studio-drawer__panel\s*\{[^}]*width:\s*100%/s,
    );
    expect(stylesSource).toMatch(
      /\.studio-contact-panel__telegram(?![\w-])[^}]*color:/s,
    );
    expect(stylesSource).toMatch(
      /\.studio-contact-panel__channel a\s*\{[^}]*min-height:\s*44px/s,
    );
    expect(stylesSource).toMatch(
      /\.auth-gate__support a\s*\{[^}]*min-height:\s*44px/s,
    );
  });

  it('keeps Studio contact drawer copy readable without changing form controls', () => {
    expect(stylesSource).toMatch(
      /\.studio-drawer__header p\s*\{[^}]*font-size:\s*14px/s,
    );
    expect(stylesSource).toMatch(
      /\.studio-contact-panel \.studio-kicker\s*\{[^}]*font-size:\s*12px/s,
    );
    expect(stylesSource).toMatch(
      /\.studio-contact-panel__intro\s*>\s*p:last-child\s*\{[^}]*font-size:\s*14px/s,
    );
    expect(stylesSource).toMatch(
      /\.studio-contact-panel__channel\s*>\s*span\s*\{[^}]*font-size:\s*12px/s,
    );
    expect(stylesSource).toMatch(
      /\.studio-contact-panel \.feedback-composer__consent\s*\{[^}]*font-size:\s*14px/s,
    );
    expect(stylesSource).toMatch(
      /\.studio-contact-panel \.feedback-composer__note\s*\{[^}]*font-size:\s*14px/s,
    );
  });

  it('keeps every compact workbench header action at a 44px touch target', () => {
    const baseRule = stylesSource.match(
      /\.studio-app--workbench \.studio-header__actions button\s*\{[^}]*\}/s,
    )?.[0] ?? '';
    expect(baseRule).toMatch(/min-height:\s*44px/);

    const compactStart = stylesSource.indexOf(
      '@media (max-width: 560px) {\n  .studio-app:not(.studio-app--workbench)',
    );
    const compactEnd = stylesSource.indexOf('@media (max-width: 390px)', compactStart);
    const compactRules = stylesSource.slice(compactStart, compactEnd);
    expect(compactRules).toMatch(
      /\.studio-app--workbench \.studio-header__actions button,\s*\.studio-app--workbench \.studio-header__actions \.studio-header__publish\s*\{[^}]*width:\s*44px;[^}]*min-height:\s*44px;/s,
    );
    expect(compactRules).toMatch(
      /\.studio-app--workbench \.studio-header__actions\s*\{[^}]*width:\s*auto;[^}]*display:\s*flex;[^}]*gap:\s*4px/s,
    );
    expect(compactRules).toMatch(
      /\.studio-app--workbench \.studio-header\s*\{[^}]*padding:\s*0 10px;[^}]*grid-template-columns:\s*auto minmax\(0,\s*1fr\) auto/s,
    );

    const narrowStart = stylesSource.indexOf('@media (max-width: 390px)');
    const narrowRules = stylesSource.slice(narrowStart);
    expect(narrowRules).toMatch(
      /\.studio-app--workbench \.studio-header \.kaigo-logo__wordmark\s*\{[^}]*display:\s*none/s,
    );
  });
});
