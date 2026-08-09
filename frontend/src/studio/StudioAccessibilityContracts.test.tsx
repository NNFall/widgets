import { cleanup, render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';

import stylesSource from '../styles.css?raw';
import { StudioTimeline, studioTimelineEventMotion } from './StudioTimeline';

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

function relativeLuminance(hex: string) {
  const channels = [1, 3, 5].map((index) => Number.parseInt(hex.slice(index, index + 2), 16) / 255);
  const [red, green, blue] = channels.map((channel) => (
    channel <= 0.03928
      ? channel / 12.92
      : ((channel + 0.055) / 1.055) ** 2.4
  ));
  return (0.2126 * red) + (0.7152 * green) + (0.0722 * blue);
}

function contrastRatio(foreground: string, background: string) {
  const light = Math.max(relativeLuminance(foreground), relativeLuminance(background));
  const dark = Math.min(relativeLuminance(foreground), relativeLuminance(background));
  return (light + 0.05) / (dark + 0.05);
}

function ruleColor(selector: string) {
  const escapedSelector = selector.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
  const match = stylesSource.match(new RegExp(`${escapedSelector}\\s*\\{[^}]*color:\\s*(#[0-9a-f]{6})`, 's'));
  expect(match, `CSS rule ${selector} must declare a six-digit text color`).not.toBeNull();
  return match![1];
}

describe('Studio accessibility contracts', () => {
  it('exposes the generation signal with valid status semantics', () => {
    render(<StudioTimeline events={[]} running />);

    expect(screen.getByRole('status', { name: 'Генерация выполняется' })).toBeInTheDocument();
  });

  it('keeps the primary action and small event text readable', () => {
    expect(stylesSource).toMatch(/\.studio-create\s*\{[^}]*color:\s*var\(--ink\)/s);
    expect(contrastRatio(ruleColor('.studio-event__meta span'), '#fcfbfa')).toBeGreaterThanOrEqual(4.5);
    expect(contrastRatio(ruleColor('.studio-event__content > small'), '#fcfbfa')).toBeGreaterThanOrEqual(4.5);
  });

  it('stops every infinite Studio signal animation for reduced motion', () => {
    const reducedMotionRules = stylesSource.slice(stylesSource.lastIndexOf('@media (prefers-reduced-motion: reduce)'));

    expect(reducedMotionRules).toMatch(/\.studio-header__session strong::before,[\s\S]*\.studio-timeline__signal,[\s\S]*animation:\s*none !important/);
  });

  it('renders new timeline events immediately when reduced motion is requested', () => {
    expect(studioTimelineEventMotion(true)).toEqual({
      initial: false,
      animate: { opacity: 1, y: 0 },
      exit: undefined,
      transition: { duration: 0 },
    });

    Object.defineProperty(window, 'matchMedia', {
      configurable: true,
      value: vi.fn().mockReturnValue({
        matches: true,
        media: '(prefers-reduced-motion: reduce)',
        onchange: null,
        addEventListener: vi.fn(),
        removeEventListener: vi.fn(),
        addListener: vi.fn(),
        removeListener: vi.fn(),
        dispatchEvent: vi.fn(),
      }),
    });

    render(<StudioTimeline events={[{
      run_id: 'run-123',
      sequence: 1,
      timestamp: '2026-07-27T00:00:00Z',
      type: 'stage.started',
      stage: 'foundation',
      status: 'running',
      message: 'Собираем основу виджета',
      revision: 1,
      usage: { prompt_tokens: 1, output_tokens: 1, thinking_tokens: 1, total_tokens: 3 },
      issues: [],
      changes: [],
      error_code: null,
    }]} running />);

    const article = screen.getByText('Собираем основу виджета').closest('article');
    expect(article).not.toHaveStyle({ opacity: '0' });
    expect(article).not.toHaveStyle({ transform: 'translateY(10px)' });
  });

  it('does not expose technical browser-audit diagnostics to the user', () => {
    render(<StudioTimeline events={[{
      run_id: 'run-visual-repair',
      sequence: 12,
      timestamp: '2026-08-02T15:24:00Z',
      type: 'visual_repair.started',
      stage: 'motion_polish',
      status: 'running',
      message: 'Исправление после браузерной проверки: попытка 1/4',
      revision: 4,
      usage: { prompt_tokens: 0, output_tokens: 0, thinking_tokens: 0, total_tokens: 0 },
      issues: [{
        code: 'browser_gate_failed',
        field: 'body_html/css',
        message: "ValueError: no-preference attention state has no visible visual cue; evidence={'changing': False}",
      }],
      changes: [],
      error_code: null,
    }]} running />);

    expect(screen.getByText('Анимация кнопки виджета недостаточно заметна.')).toBeInTheDocument();
    expect(screen.queryByText(/ValueError|evidence=|no-preference/)).not.toBeInTheDocument();
  });

  it('shows the durable persona stage with a Russian user-facing label', () => {
    render(<StudioTimeline events={[{
      run_id: 'run-persona',
      sequence: 3,
      timestamp: '2026-08-09T15:24:00Z',
      type: 'stage.completed',
      stage: 'persona',
      status: 'completed',
      message: 'Сотрудник Мария выбран',
      revision: null,
      usage: { prompt_tokens: 1, output_tokens: 1, thinking_tokens: 0, total_tokens: 2 },
      issues: [],
      changes: [],
      error_code: null,
    }]} running={false} />);

    expect(screen.getByText('Выбор сотрудника')).toBeInTheDocument();
    expect(screen.getByText('Сотрудник Мария выбран')).toBeInTheDocument();
  });
});
