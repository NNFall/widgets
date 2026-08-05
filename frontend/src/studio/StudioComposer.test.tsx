import { cleanup, render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { StudioComposer } from './StudioComposer';

afterEach(cleanup);

describe('StudioComposer', () => {
  it('explains the whole path before the owner creates a project', () => {
    render(
      <StudioComposer
        sourceUrl=""
        brief=""
        pending={false}
        error={null}
        onSourceUrlChange={vi.fn()}
        onBriefChange={vi.fn()}
        onSubmit={vi.fn()}
      />,
    );

    const journey = screen.getByRole('list', { name: 'Путь до запуска' });
    expect(journey).toBeVisible();
    expect(screen.getByText('Добавьте сайт')).toBeVisible();
    expect(screen.getByText('Проверьте виджет')).toBeVisible();
    expect(screen.getByText('Опубликуйте')).toBeVisible();
  });
});
