import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import stylesSource from '../styles.css?raw';
import { CONTACT_CONFIG } from './contact';
import { FeedbackComposer } from './FeedbackComposer';
import type { FeedbackSource } from './feedbackApi';

type ComposerProps = {
  source: FeedbackSource;
  csrfToken?: string | null;
  className?: string;
};

function renderComposer(props: ComposerProps) {
  return render(<FeedbackComposer {...props} />);
}

function sessionResponse(overrides: Partial<{
  csrf_token: string;
  consent_version: string;
  message_max_length: number;
}> = {}) {
  return new Response(JSON.stringify({
    csrf_token: 'session-csrf',
    consent_version: 'feedback-server-v3',
    message_max_length: 4000,
    ...overrides,
  }), {
    status: 200,
    headers: { 'Content-Type': 'application/json' },
  });
}

function receiptResponse(receiptId = 'receipt-1') {
  return new Response(JSON.stringify({
    receipt_id: receiptId,
    status: 'stored',
    received_at: '2026-08-17T10:00:00Z',
  }), {
    status: 201,
    headers: { 'Content-Type': 'application/json' },
  });
}

function failedResponse(status = 503) {
  return new Response(null, { status });
}

function postBody(call: [string, RequestInit]) {
  return JSON.parse(String(call[1].body)) as Record<string, unknown>;
}

function postHeaders(call: [string, RequestInit]) {
  return new Headers(call[1].headers);
}

beforeEach(() => {
  vi.stubGlobal('crypto', { randomUUID: vi.fn().mockReturnValue('uuid-1') });
});

afterEach(() => {
  cleanup();
  vi.useRealTimers();
  document.head.querySelectorAll('style[data-test-feedback-styles]').forEach((style) => style.remove());
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe('FeedbackComposer', () => {
  it('renders four topics, a required 4000-character message, and a disabled submit action', () => {
    renderComposer({ source: 'landing_contact' });

    expect(screen.getAllByRole('radio')).toHaveLength(4);
    expect(screen.getByRole('textbox', { name: 'Сообщение' })).toHaveAttribute('required');
    expect(screen.getByRole('textbox', { name: 'Сообщение' })).toHaveAttribute('maxLength', '4000');
    expect(screen.getByRole('button', { name: 'Отправить' })).toBeDisabled();
    expect(screen.queryByRole('checkbox')).not.toBeInTheDocument();
    expect(document.querySelector('a[href^="mailto:"]')).not.toBeInTheDocument();

    const consentLink = screen.getByRole('link', { name: 'текстом согласия на обработку персональных данных' });
    expect(consentLink).toHaveAttribute(
      'href',
      CONTACT_CONFIG.consentDocumentPath,
    );
    expect(screen.getByText(/Нажимая «Отправить», вы подтверждаете согласие/i)).toBeVisible();
  });

  it('loads a landing session before posting the exact consent-only payload', async () => {
    const user = userEvent.setup();
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(sessionResponse())
      .mockResolvedValueOnce(receiptResponse('landing-receipt'));
    vi.stubGlobal('fetch', fetchMock);
    renderComposer({ source: 'landing_contact' });

    await user.type(screen.getByRole('textbox', { name: 'Сообщение' }), '  Хочу улучшить импорт  ');
    await user.click(screen.getByRole('button', { name: 'Отправить' }));

    await waitFor(() => expect(screen.getByRole('status')).toHaveTextContent(
      'Спасибо. Сообщение сохранено и поможет улучшать Kaigo.',
    ));

    expect(fetchMock).toHaveBeenCalledTimes(2);
    const sessionCall = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(sessionCall[0]).toBe('/api/feedback/session');
    expect(sessionCall[1]).toMatchObject({
      credentials: 'include',
      headers: { Accept: 'application/json' },
    });

    const postCall = fetchMock.mock.calls[1] as [string, RequestInit];
    expect(postCall[0]).toBe('/api/feedback');
    expect(postHeaders(postCall).get('X-CSRF-Token')).toBe('session-csrf');
    expect(postHeaders(postCall).get('Idempotency-Key')).toBe('feedback-uuid-1');
    expect(postBody(postCall)).toEqual({
      topic: 'question',
      message: 'Хочу улучшить импорт',
      source: 'landing_contact',
      consent: { version: 'feedback-server-v3', accepted: true },
    });
    for (const forbiddenField of ['email', 'contact', 'name', 'project_id', 'run_id', 'user_id']) {
      expect(postBody(postCall)).not.toHaveProperty(forbiddenField);
    }
  });

  it('replays a failed landing attempt without refetching the session', async () => {
    const user = userEvent.setup();
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(sessionResponse({ consent_version: 'feedback-v4' }))
      .mockResolvedValueOnce(failedResponse(503))
      .mockResolvedValueOnce(receiptResponse('landing-replay-receipt'));
    vi.stubGlobal('fetch', fetchMock);
    renderComposer({ source: 'landing_contact' });

    await user.type(screen.getByRole('textbox', { name: 'Сообщение' }), '  Повторить точно  ');
    await user.click(screen.getByRole('button', { name: 'Отправить' }));
    await waitFor(() => expect(screen.getByRole('alert')).toHaveTextContent(
      'Не удалось отправить. Ваш текст остался в форме.',
    ));

    await user.click(screen.getByRole('button', { name: 'Повторить' }));
    await waitFor(() => expect(screen.getByRole('status')).toBeInTheDocument());

    expect(fetchMock).toHaveBeenCalledTimes(3);
    expect(fetchMock.mock.calls[0][0]).toBe('/api/feedback/session');
    const firstPost = fetchMock.mock.calls[1] as [string, RequestInit];
    const replayPost = fetchMock.mock.calls[2] as [string, RequestInit];
    expect(replayPost[0]).toBe('/api/feedback');
    expect(postBody(replayPost)).toEqual(postBody(firstPost));
    expect(postHeaders(replayPost).get('Idempotency-Key')).toBe(
      postHeaders(firstPost).get('Idempotency-Key'),
    );
    expect(postHeaders(replayPost).get('X-CSRF-Token')).toBe(
      postHeaders(firstPost).get('X-CSRF-Token'),
    );
  });

  it('refreshes a rotated Studio token without changing the failed attempt identity', async () => {
    const user = userEvent.setup();
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(failedResponse(503))
      .mockResolvedValueOnce(receiptResponse('rotated-token-receipt'));
    vi.stubGlobal('fetch', fetchMock);
    const view = renderComposer({ source: 'studio_account', csrfToken: 'studio-csrf-old' });

    const message = screen.getByRole('textbox', { name: 'Сообщение' });
    await user.type(message, 'Повторить с новым токеном');
    await user.click(screen.getByRole('button', { name: 'Отправить' }));
    await waitFor(() => expect(screen.getByRole('alert')).toHaveTextContent(
      'Не удалось отправить. Ваш текст остался в форме.',
    ));

    view.rerender(<FeedbackComposer source="studio_account" csrfToken="studio-csrf-new" />);
    await user.click(screen.getByRole('button', { name: 'Повторить' }));
    await waitFor(() => expect(screen.getByRole('status')).toBeInTheDocument());

    const firstPost = fetchMock.mock.calls[0] as [string, RequestInit];
    const replayPost = fetchMock.mock.calls[1] as [string, RequestInit];
    expect(postBody(replayPost)).toEqual(postBody(firstPost));
    expect(postHeaders(replayPost).get('Idempotency-Key')).toBe(
      postHeaders(firstPost).get('Idempotency-Key'),
    );
    expect(postHeaders(replayPost).get('X-CSRF-Token')).toBe('studio-csrf-new');
  });

  it('gives each composer instance a unique textarea id and label association', () => {
    render(
      <>
        <FeedbackComposer source="landing_contact" />
        <FeedbackComposer source="studio_account" csrfToken="studio-csrf" />
      </>,
    );

    const textareas = screen.getAllByRole('textbox', { name: 'Сообщение' });
    const ids = textareas.map((textarea) => textarea.id);
    expect(new Set(ids).size).toBe(2);
    for (const id of ids) {
      expect(id).not.toBe('');
      expect(document.querySelector(`label[for="${id}"]`)).toBeInTheDocument();
    }
  });

  it('caps a server limit above the product limit at 4000 characters', async () => {
    const user = userEvent.setup();
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(sessionResponse({ message_max_length: 5120 }))
      .mockResolvedValueOnce(receiptResponse('server-limit-receipt'));
    vi.stubGlobal('fetch', fetchMock);
    renderComposer({ source: 'landing_contact' });

    const message = screen.getByRole('textbox', { name: 'Сообщение' });
    await user.type(message, 'Сообщение с серверным лимитом');
    await user.click(screen.getByRole('button', { name: 'Отправить' }));

    await waitFor(() => expect(screen.getByRole('status')).toBeInTheDocument());
    expect(message).toHaveAttribute('maxLength', '4000');
  });

  it('retains an oversized message and skips POST after applying a lower server limit', async () => {
    const user = userEvent.setup();
    const fetchMock = vi.fn().mockResolvedValueOnce(sessionResponse({ message_max_length: 12 }));
    vi.stubGlobal('fetch', fetchMock);
    renderComposer({ source: 'landing_contact' });

    const message = screen.getByRole('textbox', { name: 'Сообщение' });
    await user.type(message, ' 1234567890123 ');
    await user.click(screen.getByRole('button', { name: 'Отправить' }));

    await waitFor(() => expect(screen.getByRole('alert')).toHaveTextContent(
      'Сократите сообщение до 12 символов.',
    ));
    expect(fetchMock).toHaveBeenCalledOnce();
    expect(message).toHaveValue(' 1234567890123 ');
    expect(message).toHaveAttribute('maxLength', '12');
  });

  it('posts Studio feedback directly with the configured consent version', async () => {
    const user = userEvent.setup();
    const fetchMock = vi.fn().mockResolvedValue(receiptResponse('studio-receipt'));
    vi.stubGlobal('fetch', fetchMock);
    renderComposer({ source: 'studio_account', csrfToken: 'studio-csrf' });

    await user.click(screen.getByRole('radio', { name: 'Ошибка' }));
    await user.type(screen.getByRole('textbox', { name: 'Сообщение' }), 'Не открывается проект.');
    await user.click(screen.getByRole('button', { name: 'Отправить' }));

    await waitFor(() => expect(screen.getByRole('status')).toHaveTextContent(
      'Спасибо. Сообщение сохранено и поможет улучшать Kaigo.',
    ));

    expect(fetchMock).toHaveBeenCalledOnce();
    const postCall = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(postCall[0]).toBe('/api/feedback');
    expect(postHeaders(postCall).get('X-CSRF-Token')).toBe('studio-csrf');
    expect(postBody(postCall)).toEqual({
      topic: 'bug',
      message: 'Не открывается проект.',
      source: 'studio_account',
      consent: { version: CONTACT_CONFIG.consentDocumentVersion, accepted: true },
    });
  });

  it('marks every form control busy and disabled while a request is in flight', async () => {
    const user = userEvent.setup();
    let resolvePost!: (response: Response) => void;
    const pendingPost = new Promise<Response>((resolve) => {
      resolvePost = resolve;
    });
    const fetchMock = vi.fn().mockReturnValue(pendingPost);
    vi.stubGlobal('fetch', fetchMock);
    renderComposer({ source: 'studio_account', csrfToken: 'studio-csrf' });

    await user.type(screen.getByRole('textbox', { name: 'Сообщение' }), 'Проверка загрузки');
    await user.click(screen.getByRole('button', { name: 'Отправить' }));

    expect(document.querySelector('form')).toHaveAttribute('aria-busy', 'true');
    expect(screen.getByRole('button', { name: 'Отправляем…' })).toBeDisabled();
    expect(screen.getByRole('textbox', { name: 'Сообщение' })).toBeDisabled();
    for (const radio of screen.getAllByRole('radio')) {
      expect(radio).toBeDisabled();
    }

    resolvePost(receiptResponse('loading-receipt'));
    await waitFor(() => expect(screen.getByRole('status')).toBeInTheDocument());
  });

  it('clears the message only after a valid receipt and offers a fresh compose action', async () => {
    const user = userEvent.setup();
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(receiptResponse('valid-receipt')));
    renderComposer({ source: 'landing_contact', csrfToken: 'landing-csrf' });

    const message = screen.getByRole('textbox', { name: 'Сообщение' });
    await user.type(message, 'Сообщение для сохранения');
    await user.click(screen.getByRole('button', { name: 'Отправить' }));

    await waitFor(() => expect(screen.getByRole('status')).toHaveTextContent(
      'Спасибо. Сообщение сохранено и поможет улучшать Kaigo.',
    ));
    expect(message).toHaveValue('');
    expect(screen.getByRole('button', { name: 'Отправить ещё' })).toBeEnabled();

    await user.click(screen.getByRole('button', { name: 'Отправить ещё' }));
    expect(screen.getByRole('button', { name: 'Отправить' })).toBeDisabled();
  });

  it('keeps the message and reuses the idempotency key when retrying unchanged content', async () => {
    const user = userEvent.setup();
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(failedResponse())
      .mockResolvedValueOnce(receiptResponse('retry-receipt'));
    vi.stubGlobal('fetch', fetchMock);
    renderComposer({ source: 'studio_auth_error', csrfToken: 'auth-csrf' });

    const message = screen.getByRole('textbox', { name: 'Сообщение' });
    await user.type(message, '  Повторить отправку  ');
    await user.click(screen.getByRole('button', { name: 'Отправить' }));

    await waitFor(() => expect(screen.getByRole('alert')).toHaveTextContent(
      'Не удалось отправить. Ваш текст остался в форме.',
    ));
    expect(message).toHaveValue('  Повторить отправку  ');
    expect(screen.getByRole('button', { name: 'Повторить' })).toBeEnabled();

    await user.type(message, '   ');
    await user.click(screen.getByRole('button', { name: 'Повторить' }));
    await waitFor(() => expect(screen.getByRole('status')).toBeInTheDocument());

    const firstPost = fetchMock.mock.calls[0] as [string, RequestInit];
    const retryPost = fetchMock.mock.calls[1] as [string, RequestInit];
    expect(postHeaders(retryPost).get('Idempotency-Key')).toBe(
      postHeaders(firstPost).get('Idempotency-Key'),
    );
  });

  it('creates a new idempotency key when the failed topic changes', async () => {
    const user = userEvent.setup();
    const randomUUID = vi.fn()
      .mockReturnValueOnce('uuid-1')
      .mockReturnValueOnce('uuid-2');
    vi.stubGlobal('crypto', { randomUUID });
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(failedResponse())
      .mockResolvedValueOnce(receiptResponse('changed-topic-receipt'));
    vi.stubGlobal('fetch', fetchMock);
    renderComposer({ source: 'studio_account', csrfToken: 'studio-csrf' });

    await user.type(screen.getByRole('textbox', { name: 'Сообщение' }), 'Одинаковый текст');
    await user.click(screen.getByRole('button', { name: 'Отправить' }));
    await waitFor(() => expect(screen.getByRole('alert')).toBeInTheDocument());

    await user.click(screen.getByRole('radio', { name: 'Ошибка' }));
    await user.click(screen.getByRole('button', { name: 'Повторить' }));
    await waitFor(() => expect(screen.getByRole('status')).toBeInTheDocument());

    const firstPost = fetchMock.mock.calls[0] as [string, RequestInit];
    const changedTopicPost = fetchMock.mock.calls[1] as [string, RequestInit];
    expect(postHeaders(firstPost).get('Idempotency-Key')).toBe('feedback-uuid-1');
    expect(postHeaders(changedTopicPost).get('Idempotency-Key')).toBe('feedback-uuid-2');
  });

  it('creates a new idempotency key after the failed fingerprint changes', async () => {
    const user = userEvent.setup();
    const randomUUID = vi.fn()
      .mockReturnValueOnce('uuid-1')
      .mockReturnValueOnce('uuid-2');
    vi.stubGlobal('crypto', { randomUUID });
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(failedResponse())
      .mockResolvedValueOnce(receiptResponse('changed-retry-receipt'));
    vi.stubGlobal('fetch', fetchMock);
    renderComposer({ source: 'studio_account', csrfToken: 'studio-csrf' });

    const message = screen.getByRole('textbox', { name: 'Сообщение' });
    await user.type(message, 'Первый текст');
    await user.click(screen.getByRole('button', { name: 'Отправить' }));
    await waitFor(() => expect(screen.getByRole('alert')).toBeInTheDocument());

    await user.clear(message);
    await user.type(message, 'Изменённый текст');
    await user.click(screen.getByRole('button', { name: 'Повторить' }));
    await waitFor(() => expect(screen.getByRole('status')).toBeInTheDocument());

    const firstPost = fetchMock.mock.calls[0] as [string, RequestInit];
    const changedPost = fetchMock.mock.calls[1] as [string, RequestInit];
    expect(postHeaders(firstPost).get('Idempotency-Key')).toBe('feedback-uuid-1');
    expect(postHeaders(changedPost).get('Idempotency-Key')).toBe('feedback-uuid-2');
  });

  it('shows a safe recovery action for a bad request', async () => {
    const user = userEvent.setup();
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(failedResponse(400)));
    renderComposer({ source: 'studio_account', csrfToken: 'studio-csrf' });

    await user.type(screen.getByRole('textbox', { name: 'Сообщение' }), 'Неверный запрос');
    await user.click(screen.getByRole('button', { name: 'Отправить' }));

    await waitFor(() => expect(screen.getByRole('alert')).toHaveTextContent(
      'Проверьте текст и попробуйте ещё раз.',
    ));
    expect(screen.getByRole('button', { name: 'Повторить' })).toBeEnabled();
  });

  it('treats HTTP 422 as validation and starts a fresh keyed attempt', async () => {
    const user = userEvent.setup();
    const randomUUID = vi.fn()
      .mockReturnValueOnce('uuid-1')
      .mockReturnValueOnce('uuid-2');
    vi.stubGlobal('crypto', { randomUUID });
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(failedResponse(422))
      .mockResolvedValueOnce(receiptResponse('unprocessable-recovery-receipt'));
    vi.stubGlobal('fetch', fetchMock);
    renderComposer({ source: 'studio_account', csrfToken: 'studio-csrf' });

    await user.type(screen.getByRole('textbox', { name: 'Сообщение' }), 'Нужно проверить формат');
    await user.click(screen.getByRole('button', { name: 'Отправить' }));
    await waitFor(() => expect(screen.getByRole('alert')).toHaveTextContent(
      'Проверьте текст и попробуйте ещё раз.',
    ));

    await user.click(screen.getByRole('button', { name: 'Повторить' }));
    await waitFor(() => expect(screen.getByRole('status')).toBeInTheDocument());

    const firstPost = fetchMock.mock.calls[0] as [string, RequestInit];
    const recoveredPost = fetchMock.mock.calls[1] as [string, RequestInit];
    expect(postHeaders(firstPost).get('Idempotency-Key')).toBe('feedback-uuid-1');
    expect(postHeaders(recoveredPost).get('Idempotency-Key')).toBe('feedback-uuid-2');
  });

  it('blocks an immediate retry after rate limiting and explains the wait', async () => {
    const user = userEvent.setup();
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(null, {
      status: 429,
      headers: { 'Retry-After': '17' },
    })));
    renderComposer({ source: 'studio_account', csrfToken: 'studio-csrf' });

    await user.type(screen.getByRole('textbox', { name: 'Сообщение' }), 'Слишком часто');
    await user.click(screen.getByRole('button', { name: 'Отправить' }));

    await waitFor(() => expect(screen.getByRole('alert')).toHaveTextContent(
      'Слишком много запросов. Повторите позже.',
    ));
    expect(screen.getByRole('alert')).not.toHaveTextContent('через 17 с.');
    expect(screen.getByText('Повторите через 17 с.')).toHaveAttribute('aria-live', 'off');
    expect(screen.getByRole('button', { name: 'Повторить через 17 с' })).toBeDisabled();
  });

  it('counts down Retry-After, unlocks, and replays the exact attempt', async () => {
    vi.useFakeTimers();
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(new Response(null, {
        status: 429,
        headers: { 'Retry-After': '2' },
      }))
      .mockResolvedValueOnce(receiptResponse('rate-limit-replay-receipt'));
    vi.stubGlobal('fetch', fetchMock);
    renderComposer({ source: 'studio_account', csrfToken: 'studio-csrf' });

    const message = screen.getByRole('textbox', { name: 'Сообщение' });
    fireEvent.change(message, { target: { value: 'Повторить после лимита' } });
    fireEvent.submit(message.closest('form') as HTMLFormElement);
    await act(async () => { await Promise.resolve(); await Promise.resolve(); });
    expect(screen.getByRole('button', { name: 'Повторить через 2 с' })).toBeDisabled();

    await act(async () => { vi.advanceTimersByTime(1000); await Promise.resolve(); });
    expect(screen.getByRole('button', { name: 'Повторить через 1 с' })).toBeDisabled();
    expect(screen.getByRole('alert')).toHaveTextContent('Слишком много запросов. Повторите позже.');
    expect(screen.getByRole('alert')).not.toHaveTextContent('через 1 с.');
    expect(screen.getByText('Повторите через 1 с.')).toHaveAttribute('aria-live', 'off');

    await act(async () => { vi.advanceTimersByTime(1000); await Promise.resolve(); });
    expect(screen.getByRole('button', { name: 'Повторить' })).toBeEnabled();
    expect(screen.getByRole('alert')).toHaveTextContent('Слишком много запросов. Повторите позже.');
    expect(screen.getByRole('status')).toHaveTextContent('Теперь можно повторить отправку.');
    expect(screen.queryByText('Повторите через 0 с.')).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: 'Повторить' }));
    await act(async () => { await Promise.resolve(); await Promise.resolve(); });
    expect(screen.getByRole('status')).toBeInTheDocument();
    expect(fetchMock).toHaveBeenCalledTimes(2);
    const firstPost = fetchMock.mock.calls[0] as [string, RequestInit];
    const replayPost = fetchMock.mock.calls[1] as [string, RequestInit];
    expect(postBody(replayPost)).toEqual(postBody(firstPost));
    expect(postHeaders(replayPost).get('Idempotency-Key')).toBe(
      postHeaders(firstPost).get('Idempotency-Key'),
    );
  });

  it('allows an immediate retry when Retry-After is zero', async () => {
    vi.useFakeTimers();
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(new Response(null, {
        status: 429,
        headers: { 'Retry-After': '0' },
      }))
      .mockResolvedValueOnce(receiptResponse('zero-rate-limit-receipt'));
    vi.stubGlobal('fetch', fetchMock);
    renderComposer({ source: 'studio_account', csrfToken: 'studio-csrf' });

    const message = screen.getByRole('textbox', { name: 'Сообщение' });
    fireEvent.change(message, { target: { value: 'Повторить сразу' } });
    fireEvent.submit(message.closest('form') as HTMLFormElement);
    await act(async () => { await Promise.resolve(); await Promise.resolve(); });
    expect(screen.getByRole('button', { name: 'Повторить' })).toBeEnabled();
    expect(screen.getByRole('alert')).toHaveTextContent('Слишком много запросов. Повторите позже.');
    expect(screen.getByRole('status')).toHaveTextContent('Теперь можно повторить отправку.');

    fireEvent.click(screen.getByRole('button', { name: 'Повторить' }));
    await act(async () => { await Promise.resolve(); await Promise.resolve(); });
    expect(screen.getByRole('status')).toBeInTheDocument();
    expect(fetchMock).toHaveBeenCalledTimes(2);
  });

  it.each([
    { label: 'missing', retryAfterHeader: null },
    { label: 'invalid', retryAfterHeader: 'later' },
  ])('uses a bounded 60-second fallback for $label Retry-After', async ({ retryAfterHeader }) => {
    vi.useFakeTimers();
    const headers: Record<string, string> = retryAfterHeader === null
      ? {}
      : { 'Retry-After': retryAfterHeader };
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(new Response(null, { status: 429, headers }))
      .mockResolvedValueOnce(receiptResponse(`${retryAfterHeader ?? 'missing'}-fallback-receipt`));
    vi.stubGlobal('fetch', fetchMock);
    renderComposer({ source: 'studio_account', csrfToken: 'studio-csrf' });

    const message = screen.getByRole('textbox', { name: 'Сообщение' });
    fireEvent.change(message, { target: { value: 'Подождать ограничение' } });
    fireEvent.submit(message.closest('form') as HTMLFormElement);
    await act(async () => { await Promise.resolve(); await Promise.resolve(); });

    expect(screen.getByRole('button', { name: 'Повторить через 60 с' })).toBeDisabled();
    expect(screen.getByText('Повторите через 60 с.')).toHaveAttribute('aria-live', 'off');
    await act(async () => { vi.advanceTimersByTime(60_000); await Promise.resolve(); });
    expect(screen.getByRole('button', { name: 'Повторить' })).toBeEnabled();
    expect(screen.getByRole('status')).toHaveTextContent('Теперь можно повторить отправку.');

    fireEvent.click(screen.getByRole('button', { name: 'Повторить' }));
    await act(async () => { await Promise.resolve(); await Promise.resolve(); });
    expect(screen.getByRole('status')).toHaveTextContent(
      'Спасибо. Сообщение сохранено и поможет улучшать Kaigo.',
    );
  });

  it('unlocks from an absolute deadline when Date.now jumps ahead', async () => {
    vi.useFakeTimers();
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(new Response(null, {
        status: 429,
        headers: { 'Retry-After': '5' },
      }))
      .mockResolvedValueOnce(receiptResponse('absolute-deadline-receipt'));
    vi.stubGlobal('fetch', fetchMock);
    renderComposer({ source: 'studio_account', csrfToken: 'studio-csrf' });

    const message = screen.getByRole('textbox', { name: 'Сообщение' });
    fireEvent.change(message, { target: { value: 'Проверить абсолютный срок' } });
    fireEvent.submit(message.closest('form') as HTMLFormElement);
    await act(async () => { await Promise.resolve(); await Promise.resolve(); });
    expect(screen.getByRole('button', { name: 'Повторить через 5 с' })).toBeDisabled();

    vi.setSystemTime(Date.now() + 10_000);
    await act(async () => { vi.advanceTimersByTime(1000); await Promise.resolve(); });
    expect(screen.getByRole('button', { name: 'Повторить' })).toBeEnabled();
    expect(screen.getByRole('status')).toHaveTextContent('Теперь можно повторить отправку.');

    fireEvent.click(screen.getByRole('button', { name: 'Повторить' }));
    await act(async () => { await Promise.resolve(); await Promise.resolve(); });
    expect(screen.getByRole('status')).toHaveTextContent(
      'Спасибо. Сообщение сохранено и поможет улучшать Kaigo.',
    );
  });

  it('starts a fresh landing session and key after CSRF recovery from 403', async () => {
    const user = userEvent.setup();
    const randomUUID = vi.fn()
      .mockReturnValueOnce('uuid-1')
      .mockReturnValueOnce('uuid-2');
    vi.stubGlobal('crypto', { randomUUID });
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(sessionResponse({ csrf_token: 'csrf-old', consent_version: 'feedback-old' }))
      .mockResolvedValueOnce(failedResponse(403))
      .mockResolvedValueOnce(sessionResponse({ csrf_token: 'csrf-new', consent_version: 'feedback-new' }))
      .mockResolvedValueOnce(receiptResponse('csrf-recovery-receipt'));
    vi.stubGlobal('fetch', fetchMock);
    renderComposer({ source: 'landing_contact' });

    await user.type(screen.getByRole('textbox', { name: 'Сообщение' }), 'Обновить сессию');
    await user.click(screen.getByRole('button', { name: 'Отправить' }));
    await waitFor(() => expect(screen.getByRole('alert')).toHaveTextContent(
      'Сессия обратной связи устарела.',
    ));

    await user.click(screen.getByRole('button', { name: 'Повторить' }));
    await waitFor(() => expect(screen.getByRole('status')).toBeInTheDocument());

    expect(fetchMock.mock.calls.filter(([url]) => url === '/api/feedback/session')).toHaveLength(2);
    const firstPost = fetchMock.mock.calls[1] as [string, RequestInit];
    const recoveredPost = fetchMock.mock.calls[3] as [string, RequestInit];
    expect(postHeaders(firstPost).get('Idempotency-Key')).toBe('feedback-uuid-1');
    expect(postHeaders(recoveredPost).get('Idempotency-Key')).toBe('feedback-uuid-2');
    expect(postHeaders(recoveredPost).get('X-CSRF-Token')).toBe('csrf-new');
    expect(postBody(recoveredPost)).toMatchObject({
      message: 'Обновить сессию',
      consent: { version: 'feedback-new', accepted: true },
    });
  });

  it('does not resubmit an injected Studio token after 403 and offers a page refresh', async () => {
    const user = userEvent.setup();
    const fetchMock = vi.fn().mockResolvedValue(failedResponse(403));
    vi.stubGlobal('fetch', fetchMock);
    renderComposer({ source: 'studio_account', csrfToken: 'stale-studio-csrf' });

    await user.type(screen.getByRole('textbox', { name: 'Сообщение' }), 'Studio сессия');
    await user.click(screen.getByRole('button', { name: 'Отправить' }));
    await waitFor(() => expect(screen.getByRole('alert')).toHaveTextContent(
      'Сессия Studio устарела. Обновите страницу и попробуйте снова.',
    ));

    expect(screen.queryByRole('button', { name: 'Повторить' })).not.toBeInTheDocument();
    const refresh = screen.getByRole('link', { name: 'Обновить страницу' });
    expect(refresh).toHaveAttribute('href', window.location.href);
    expect(fetchMock).toHaveBeenCalledOnce();
  });

  it('uses a fresh key after an idempotency conflict', async () => {
    const user = userEvent.setup();
    const randomUUID = vi.fn()
      .mockReturnValueOnce('uuid-1')
      .mockReturnValueOnce('uuid-2');
    vi.stubGlobal('crypto', { randomUUID });
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(failedResponse(409))
      .mockResolvedValueOnce(receiptResponse('conflict-recovery-receipt'));
    vi.stubGlobal('fetch', fetchMock);
    renderComposer({ source: 'studio_account', csrfToken: 'studio-csrf' });

    await user.type(screen.getByRole('textbox', { name: 'Сообщение' }), 'Конфликт ключа');
    await user.click(screen.getByRole('button', { name: 'Отправить' }));
    await waitFor(() => expect(screen.getByRole('alert')).toHaveTextContent(
      'новой попытки',
    ));

    await user.click(screen.getByRole('button', { name: 'Повторить' }));
    await waitFor(() => expect(screen.getByRole('status')).toBeInTheDocument());

    const firstPost = fetchMock.mock.calls[0] as [string, RequestInit];
    const recoveredPost = fetchMock.mock.calls[1] as [string, RequestInit];
    expect(postHeaders(firstPost).get('Idempotency-Key')).toBe('feedback-uuid-1');
    expect(postHeaders(recoveredPost).get('Idempotency-Key')).toBe('feedback-uuid-2');
  });

  it('aborts an in-flight request when the composer unmounts', async () => {
    const user = userEvent.setup();
    let resolvePost!: (response: Response) => void;
    const pendingPost = new Promise<Response>((resolve) => {
      resolvePost = resolve;
    });
    const fetchMock = vi.fn().mockReturnValue(pendingPost);
    vi.stubGlobal('fetch', fetchMock);
    const view = renderComposer({ source: 'studio_account', csrfToken: 'studio-csrf' });

    await user.type(screen.getByRole('textbox', { name: 'Сообщение' }), 'Отменить при закрытии');
    await user.click(screen.getByRole('button', { name: 'Отправить' }));
    await waitFor(() => expect(fetchMock).toHaveBeenCalledOnce());

    const signal = (fetchMock.mock.calls[0] as [string, RequestInit])[1].signal as AbortSignal;
    view.unmount();
    expect(signal.aborted).toBe(true);

    resolvePost(receiptResponse('unmounted-receipt'));
  });

  it('keeps the action and legal-link geometry while adding busy and outcome styles', () => {
    const style = document.createElement('style');
    style.dataset.testFeedbackStyles = 'true';
    style.textContent = stylesSource;
    document.head.append(style);
    renderComposer({ source: 'landing_contact' });

    const consent = document.querySelector('.feedback-composer__consent');
    const consentLink = screen.getByRole('link', { name: 'текстом согласия на обработку персональных данных' });
    expect(consent).not.toBeNull();
    expect(getComputedStyle(consent as HTMLElement).display).toBe('block');
    expect(getComputedStyle(consentLink).display).toBe('inline-flex');
    expect(getComputedStyle(consentLink).minHeight).toBe('44px');
    expect(getComputedStyle(consentLink).whiteSpace).toBe('normal');
    expect(getComputedStyle(consentLink).overflowWrap).toBe('anywhere');
    expect(stylesSource).toMatch(/\.feedback-composer__action\s*\{[^}]*min-height:\s*56px;/s);
    expect(stylesSource).toMatch(/\.feedback-composer__consent a\s*\{[^}]*min-height:\s*44px;[^}]*display:\s*inline-flex;[^}]*overflow-wrap:\s*anywhere;/s);
    expect(stylesSource).toMatch(/\.feedback-composer\[aria-busy=["']true["']\]\s+\.feedback-composer__topics\s*\{[^}]*opacity:/s);
    expect(stylesSource).toMatch(/\.feedback-composer\[aria-busy=["']true["']\]\s+\.feedback-composer__message\s*\{[^}]*opacity:/s);
    expect(stylesSource).toMatch(/\.feedback-composer__result\[role=["']status["']\]\s*\{[^}]*color:/s);
    expect(stylesSource).toMatch(/\.feedback-composer__result\[role=["']alert["']\]\s*\{[^}]*color:/s);
    expect(stylesSource).toMatch(/\.feedback-composer__status\s*\{[^}]*color:/s);
    expect(stylesSource).toMatch(/\.feedback-composer__error\s*\{[^}]*color:/s);
    expect(stylesSource).not.toMatch(/\.feedback-composer[^}]*transition:\s*(?:margin|padding|width|height|top|left|right|bottom)/s);
  });
});
