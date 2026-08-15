import { cleanup, render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { StudioContactPanel } from './StudioContactPanel';

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe('StudioContactPanel', () => {
  it('offers all contact topics, a safe encoded Studio mail context, and truthful channels', async () => {
    const user = userEvent.setup();
    const fetchMock = vi.fn();
    vi.stubGlobal('fetch', fetchMock);

    render(<StudioContactPanel domain="example.com" projectId="project-123" />);

    expect(screen.getByRole('heading', { name: 'Помощь и обратная связь' })).toBeVisible();
    expect(screen.getByRole('radio', { name: 'Вопрос' })).toBeInTheDocument();
    expect(screen.getByRole('radio', { name: 'Ошибка' })).toBeInTheDocument();
    expect(screen.getByRole('radio', { name: 'Идея по улучшению' })).toBeInTheDocument();
    expect(screen.getByRole('radio', { name: 'Сотрудничество' })).toBeInTheDocument();
    expect(screen.getByRole('link', { name: 'support@kaigo.space' })).toHaveAttribute(
      'href',
      'mailto:support@kaigo.space',
    );
    expect(screen.getByText('Добавим после подтверждения контакта')).toBeVisible();
    expect(screen.queryByRole('link', { name: /Telegram/i })).not.toBeInTheDocument();
    expect(screen.getByText(/Ничего не отправляется автоматически/i)).toBeVisible();
    expect(screen.getByText(/Форма только готовит письмо/i)).toBeVisible();
    expect(screen.getByText(/Адрес временный для предпросмотра/i)).toBeVisible();
    expect(screen.queryByText(/Команда прочитает и ответит/i)).not.toBeInTheDocument();

    const action = screen.getByRole('link', { name: 'Открыть письмо' });
    expect(action).not.toHaveAttribute('href');

    await user.click(screen.getByRole('radio', { name: 'Ошибка' }));
    await user.type(screen.getByRole('textbox', { name: 'Сообщение' }), 'Не работает «Ответ» & /');
    await user.click(screen.getByRole('checkbox', { name: /соглашаюсь на обработку/i }));

    const href = action.getAttribute('href') ?? '';
    expect(href).toMatch(/^mailto:support@kaigo\.space\?/);
    expect(href).toContain(encodeURIComponent('Не работает «Ответ» & /'));
    expect(decodeURIComponent(href)).toContain('Тема: Ошибка');
    expect(decodeURIComponent(href)).toContain('Страница: /studio');
    expect(decodeURIComponent(href)).toContain('Домен: example.com');
    expect(decodeURIComponent(href)).toContain('ID проекта: project-123');
    expect(decodeURIComponent(href)).not.toContain('raw');
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it('does not add project or secret context when the panel is opened without a project', async () => {
    const user = userEvent.setup();
    render(<StudioContactPanel />);

    await user.type(screen.getByRole('textbox', { name: 'Сообщение' }), 'Вопрос без проекта');
    await user.click(screen.getByRole('checkbox', { name: /соглашаюсь на обработку/i }));

    const href = screen.getByRole('link', { name: 'Открыть письмо' }).getAttribute('href') ?? '';
    const decoded = decodeURIComponent(href);
    expect(decoded).toContain('Страница: /studio');
    expect(decoded).not.toContain('Домен:');
    expect(decoded).not.toContain('ID проекта:');
    expect(decoded).not.toContain('секрет');
  });
});
