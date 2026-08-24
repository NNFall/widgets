import { cleanup, render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { StudioLibrary } from './StudioLibrary';

function response(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  });
}

function project(
  id: string,
  status: string,
  sourceUrl: string,
  updatedAt = '2026-08-05T10:15:00Z',
  brief: string | null = null,
  ownerEmail: string | null = null,
) {
  return {
    id,
    tenant_id: 2,
    owner_user_id: 4,
    owner_email: ownerEmail,
    source_url: sourceUrl,
    brief,
    status,
    active_revision: status === 'completed' ? 3 : null,
    active_version_id: null,
    active_run: null,
    created_at: '2026-08-01T10:15:00Z',
    updated_at: updatedAt,
  };
}

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe('StudioLibrary', () => {
  it('shows owner projects with safe Russian states, domain and local date', async () => {
    const payload = {
      projects: [
        project(
          'project-ready',
          'completed',
          'https://atelier.ru/catalog',
          '2026-08-05T10:15:00Z',
          'Помогает подобрать услугу и записаться на консультацию.',
          'owner@example.com',
        ),
        project('project-running', 'running', 'https://shop.example.com/'),
        project('project-unknown', 'internal_pending', 'not a url'),
      ],
    };
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(response(payload)));
    const onOpenProject = vi.fn();
    const onCreateProject = vi.fn();
    const user = userEvent.setup();

    render(<StudioLibrary onOpenProject={onOpenProject} onCreateProject={onCreateProject} />);

    expect(await screen.findByText('atelier.ru')).toBeInTheDocument();
    expect(screen.getByText('shop.example.com')).toBeInTheDocument();
    expect(screen.getByText('Сайт проекта')).toBeInTheDocument();
    expect(screen.getByText('Готов к работе')).toBeInTheDocument();
    expect(screen.getByText('Создаётся сейчас')).toBeInTheDocument();
    expect(screen.getByText('Состояние уточняется')).toBeInTheDocument();
    expect(screen.queryByText('internal_pending')).not.toBeInTheDocument();
    expect(screen.getByText('Помогает подобрать услугу и записаться на консультацию.')).toBeInTheDocument();
    expect(screen.getByText('Владелец: owner@example.com')).toBeInTheDocument();
    expect(screen.getAllByText(new Intl.DateTimeFormat('ru-RU', {
      day: 'numeric',
      month: 'long',
      year: 'numeric',
      hour: '2-digit',
      minute: '2-digit',
    }).format(new Date('2026-08-05T10:15:00Z')))).toHaveLength(3);

    expect(screen.getByRole('button', { name: 'Посмотреть' })).toBeVisible();
    expect(screen.getByRole('button', { name: 'Следить' })).toBeVisible();
    await user.click(screen.getByRole('button', { name: 'Следить' }));
    expect(onOpenProject).toHaveBeenCalledWith('project-running');

    await user.click(screen.getByRole('button', { name: 'Новый виджет' }));
    expect(onCreateProject).toHaveBeenCalledTimes(1);
  });

  it('offers a clear first-widget action for an empty library', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(response({ projects: [] })));
    const onCreateProject = vi.fn();
    const user = userEvent.setup();

    render(<StudioLibrary onOpenProject={vi.fn()} onCreateProject={onCreateProject} />);

    await user.click(await screen.findByRole('button', { name: 'Создать первый виджет' }));
    expect(onCreateProject).toHaveBeenCalledTimes(1);
  });

  it('lets the owner retry a failed library request', async () => {
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(response({ error: 'temporary' }, 503))
      .mockResolvedValueOnce(response({ projects: [] }));
    vi.stubGlobal('fetch', fetchMock);
    const user = userEvent.setup();

    render(<StudioLibrary onOpenProject={vi.fn()} onCreateProject={vi.fn()} />);

    expect(await screen.findByRole('alert')).toHaveTextContent('Не удалось загрузить ваши виджеты');
    await user.click(screen.getByRole('button', { name: 'Попробовать ещё раз' }));

    await screen.findByRole('button', { name: 'Создать первый виджет' });
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(2));
  });

  it('shows a recoverable error when the projects response is malformed', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(response({ enabled: false })));

    render(<StudioLibrary onOpenProject={vi.fn()} onCreateProject={vi.fn()} />);

    expect(await screen.findByRole('alert')).toHaveTextContent('Не удалось загрузить ваши виджеты');
    expect(screen.queryByText('Здесь появятся ваши виджеты')).not.toBeInTheDocument();
  });
});
