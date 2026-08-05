import { cleanup, render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it } from 'vitest';

import stylesSource from '../styles.css?raw';
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

  it('removes activity animation when reduced motion is requested', () => {
    const reducedMotionRules = stylesSource.slice(stylesSource.lastIndexOf('@media (prefers-reduced-motion: reduce)'));
    expect(reducedMotionRules).toMatch(/\.studio-activity__message[\s\S]*animation:\s*none !important/);
  });

  it('uses page scrolling, readable controls, and a compact responsive shell', () => {
    expect(stylesSource).toMatch(/\.studio-shell\s*\{[^}]*max-width:\s*1480px/s);
    expect(stylesSource).toMatch(/\.studio-rail\s*\{[^}]*overflow-y:\s*visible/s);
    expect(stylesSource).not.toMatch(/\.studio-timeline__list\s*\{[^}]*max-height:\s*330px/s);
    expect(stylesSource).toMatch(/min-height:\s*44px/);
    expect(stylesSource).toMatch(/@media \(max-width:\s*390px\)/);
  });
});
