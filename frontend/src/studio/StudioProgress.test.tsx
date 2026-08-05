import { cleanup, render, screen, within } from '@testing-library/react';
import { afterEach, describe, expect, it } from 'vitest';

import { StudioProgress } from './StudioProgress';

afterEach(cleanup);

describe('StudioProgress', () => {
  it('presents backend progress as seven understandable steps', () => {
    render(
      <StudioProgress
        status="running"
        progress={52}
        currentStage="conversation"
        lastCompletedStage="identity"
        events={[]}
        activityFallback="Продолжаем создание"
      />,
    );

    expect(screen.getByText('Сейчас идёт этап 4 из 7')).toBeInTheDocument();
    expect(screen.getByRole('heading', { name: 'Диалог' })).toBeInTheDocument();
    expect(screen.getByText('Настраиваем полезный диалог с посетителем')).toBeInTheDocument();
    const stages = screen.getByRole('list', { name: 'Этапы создания виджета' });
    expect(stages).toBeInTheDocument();
    expect(within(stages).getByText('Диалог').closest('li')).toHaveAttribute('aria-current', 'step');
    expect(within(stages).getByText('Диалог').closest('li')).toHaveAttribute('data-state', 'current');
    expect(within(stages).getByText('Стиль и бренд').closest('li')).toHaveAttribute('data-state', 'completed');
    expect(within(stages).getByText('Проверка качества').closest('li')).toHaveAttribute('data-state', 'upcoming');
    expect(screen.getByRole('progressbar')).toHaveAttribute('aria-valuenow', '52');
  });

  it('marks every step complete when the run is complete', () => {
    render(
      <StudioProgress
        status="completed"
        progress={100}
        currentStage="agent_build"
        lastCompletedStage="agent_build"
        events={[]}
        activityFallback="Виджет готов"
      />,
    );

    expect(screen.getAllByRole('listitem')).toHaveLength(7);
    for (const item of screen.getAllByRole('listitem')) {
      expect(item).toHaveAttribute('data-state', 'completed');
      expect(item).not.toHaveAttribute('aria-current');
    }
    expect(screen.getByText('Результат')).toBeInTheDocument();
    expect(screen.getByRole('heading', { name: 'Виджет готов' })).toBeInTheDocument();
  });

  it('uses only backend progress and clamps it to the progressbar range', () => {
    render(
      <StudioProgress
        status="running"
        progress={140}
        currentStage="validation"
        lastCompletedStage="motion_polish"
        events={[]}
        activityFallback="Проверяем результат"
      />,
    );

    expect(screen.getByRole('progressbar')).toHaveAttribute('aria-valuenow', '100');
    expect(screen.getByText('100%')).toBeInTheDocument();
  });

  it('keeps the live activity region hidden after terminal cancellation', () => {
    render(
      <StudioProgress
        status="cancelled"
        progress={34}
        currentStage="motion_polish"
        lastCompletedStage="conversation"
        events={[{
          run_id: 'run-cancelled',
          sequence: 1,
          timestamp: '2026-08-05T10:15:00Z',
          type: 'run.created',
          stage: null,
          status: 'queued',
          message: 'raw queued status',
          revision: null,
          usage: { prompt_tokens: 0, output_tokens: 0, thinking_tokens: 0, total_tokens: 0 },
          issues: [],
          changes: [],
          error_code: null,
        }]}
        activityFallback="Создание остановлено по вашему запросу"
      />,
    );

    expect(screen.queryByRole('status')).not.toBeInTheDocument();
    expect(screen.getByText('Работу можно запустить снова.')).toBeInTheDocument();
    expect(screen.queryByText('Готовим проект к запуску')).not.toBeInTheDocument();
  });

  it('shows live activity only while a run is queued or running', () => {
    const { rerender } = render(
      <StudioProgress
        status="created"
        progress={0}
        events={[]}
        activityFallback="Запуск создан"
      />,
    );

    expect(screen.queryByRole('status')).not.toBeInTheDocument();

    rerender(
      <StudioProgress
        status="queued"
        progress={0}
        events={[]}
        activityFallback="Готовим запуск"
      />,
    );
    expect(screen.getByRole('heading', { name: 'Готовим запуск' })).toBeInTheDocument();
    expect(screen.getByText('Kaigo начнёт работу автоматически.')).toBeInTheDocument();
    expect(screen.getByRole('list', { name: 'Этапы создания виджета' }).querySelector('[aria-current="step"]')).toBeNull();
    expect(screen.getByRole('status')).toHaveTextContent('Готовим запуск');

    rerender(
      <StudioProgress
        status="completed"
        progress={100}
        events={[]}
        activityFallback="Виджет готов"
      />,
    );
    expect(screen.queryByRole('status')).not.toBeInTheDocument();
  });
});
