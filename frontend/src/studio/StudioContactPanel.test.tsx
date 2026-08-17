import { cleanup, render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { StudioContactPanel } from './StudioContactPanel';

function storedResponse() {
  return new Response(JSON.stringify({
    status: 'stored',
    receipt_id: 'receipt-studio-1',
    received_at: '2026-08-17T08:00:00Z',
  }), {
    status: 201,
    headers: { 'Content-Type': 'application/json' },
  });
}

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe('StudioContactPanel', () => {
  it('explains stored feedback without asking for contact details or advertising unconfigured channels', () => {
    render(<StudioContactPanel csrfToken="csrf-studio" />);

    expect(screen.getByRole('heading', { name: 'Помощь и обратная связь' })).toBeVisible();
    expect(screen.getByText(/сохранится в Kaigo/i)).toBeVisible();
    expect(screen.getByText(/Контактные данные не нужны/i)).toBeVisible();
    expect(screen.getByRole('radio', { name: 'Вопрос' })).toBeInTheDocument();
    expect(screen.getByRole('radio', { name: 'Ошибка' })).toBeInTheDocument();
    expect(screen.getByRole('radio', { name: 'Идея по улучшению' })).toBeInTheDocument();
    expect(screen.getByRole('radio', { name: 'Сотрудничество' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Отправить' })).toBeVisible();
    expect(screen.queryByText('support@kaigo.space')).not.toBeInTheDocument();
    expect(screen.queryByText('Telegram')).not.toBeInTheDocument();
    expect(screen.queryByRole('link', { name: /Открыть письмо/i })).not.toBeInTheDocument();
    expect(screen.queryByRole('checkbox')).not.toBeInTheDocument();
  });

  it('submits one stored Studio message with only the shared feedback contract', async () => {
    const user = userEvent.setup();
    const requests: Array<{ input: RequestInfo | URL; init?: RequestInit }> = [];
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      requests.push({ input, init });
      return storedResponse();
    });
    vi.stubGlobal('fetch', fetchMock);

    render(<StudioContactPanel csrfToken="csrf-studio" />);
    await user.click(screen.getByRole('radio', { name: 'Ошибка' }));
    await user.type(screen.getByRole('textbox', { name: 'Сообщение' }), 'Кнопка не открывает виджет');
    await user.click(screen.getByRole('button', { name: 'Отправить' }));

    await waitFor(() => expect(screen.getByRole('status')).toHaveTextContent(/Сообщение сохранено/i));
    expect(fetchMock).toHaveBeenCalledTimes(1);

    const request = requests[0];
    expect(String(request.input)).toBe('/api/feedback');
    expect(request.init?.method).toBe('POST');
    expect(new Headers(request.init?.headers).get('X-CSRF-Token')).toBe('csrf-studio');
    const body = JSON.parse(String(request.init?.body)) as Record<string, unknown>;
    expect(body).toEqual({
      topic: 'bug',
      message: 'Кнопка не открывает виджет',
      source: 'studio_account',
      consent: { version: 'feedback-v2', accepted: true },
    });
    expect(JSON.stringify(body)).not.toMatch(/email|contact|name|project_id|run_id|domain|page|context/i);
  });
});
