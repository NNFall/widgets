import { cleanup, render, screen } from '@testing-library/react';
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

    expect(screen.getByRole('heading', { name: 'Создаём ваш виджет' })).toBeInTheDocument();
    expect(screen.getByRole('list', { name: 'Этапы создания виджета' })).toBeInTheDocument();
    expect(screen.getByText('Диалог').closest('li')).toHaveAttribute('aria-current', 'step');
    expect(screen.getByText('Диалог').closest('li')).toHaveAttribute('data-state', 'current');
    expect(screen.getByText('Стиль и бренд').closest('li')).toHaveAttribute('data-state', 'completed');
    expect(screen.getByText('Проверка качества').closest('li')).toHaveAttribute('data-state', 'upcoming');
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
});
