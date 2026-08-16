import { cleanup, render, screen, waitFor } from '@testing-library/react';
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

  it('uses the server-provided message limit when bootstrapping an anonymous session', async () => {
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
    expect(message).toHaveAttribute('maxLength', '5120');
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

    await user.click(screen.getByRole('button', { name: 'Повторить' }));
    await waitFor(() => expect(screen.getByRole('status')).toBeInTheDocument());

    const firstPost = fetchMock.mock.calls[0] as [string, RequestInit];
    const retryPost = fetchMock.mock.calls[1] as [string, RequestInit];
    expect(postHeaders(retryPost).get('Idempotency-Key')).toBe(
      postHeaders(firstPost).get('Idempotency-Key'),
    );
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
