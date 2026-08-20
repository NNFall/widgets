import { cleanup, fireEvent, render, screen, within } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';

import type { PublicationRelease } from './api';
import { PublicationInstallView } from './PublicationInstallView';
import type { BillingSubscription } from './types';

afterEach(cleanup);

const publication: PublicationRelease = {
  publication_id: 'publication-123',
  release_id: 'release-5',
  artifact_id: 'artifact-456',
  project_version_id: 'version-5',
  stable_key: 'stable-widget',
  revision: 7,
  allowed_domains: ['https://example.com'],
  checksum: 'checksum-5',
  embed_url: 'https://widgets.kaigo.space/embed/stable-widget.js',
  runtime_url: 'https://widgets.kaigo.space/runtime/stable-widget',
};

const subscription: BillingSubscription = {
  id: 'subscription-123',
  plan_code: 'founder_14d',
  plan_title: 'Founder-пилот',
  status: 'active',
  current_period_start: '2026-08-20T12:00:00Z',
  current_period_end: '2026-09-03T12:00:00Z',
  auto_renew: false,
  next_renewal_at: null,
  generation_tokens_remaining: 1_500_000,
  access_kind: 'founder',
  next_charge: null,
};

const defaultProps = {
  publication,
  versionOrdinal: 5,
  subscription,
  copiedTarget: null,
  copyError: false,
  onCopyCode: vi.fn(),
  onCopyLink: vi.fn(),
} as const;

describe('PublicationInstallView', () => {
  it('renders the published handoff without owning a dialog', () => {
    render(<PublicationInstallView {...defaultProps} />);

    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
    expect(screen.getByRole('heading', { name: 'Виджет опубликован' })).toBeVisible();
    expect(screen.getByText(/Тариф действует до 3 сентября 2026 г/i)).toBeVisible();
    expect(screen.getByText(/Автопродление выключено/i)).toBeVisible();
    expect(screen.getByText('Доработки доступны в рамках тарифа.')).toBeVisible();
    expect(screen.getByRole('status')).toHaveTextContent(
      'Версия 5 опубликована и доступна на разрешённых сайтах.',
    );

    const handoff = screen.getByRole('region', { name: 'Установите виджет на сайт' });
    expect(within(handoff).getByRole('button', {
      name: 'Скопировать код установки',
    })).toBeEnabled();
    expect(within(handoff).getByRole('button', {
      name: 'Скопировать ссылку загрузчика',
    })).toBeEnabled();
    expect(within(handoff).getByRole('link', {
      name: 'Открыть инструкцию по установке',
    })).toHaveAttribute('href', '/install');

    const developerDetails = screen.getByText('Код для разработчика').closest('details');
    expect(developerDetails).not.toHaveAttribute('open');
    expect(screen.getByText(
      '<script src="https://widgets.kaigo.space/embed/stable-widget.js" async></script>',
    )).not.toBeVisible();
    expect(screen.getByText(publication.embed_url)).not.toBeVisible();
    expect(screen.getByText(publication.release_id)).not.toBeVisible();
    expect(screen.getByText(publication.artifact_id)).not.toBeVisible();
    expect(screen.queryByLabelText('Код установки для ручного копирования')).not.toBeInTheDocument();
    expect(screen.queryByLabelText('Ссылка загрузчика для ручного копирования')).not.toBeInTheDocument();
  });

  it('delegates both copy actions to the parent', () => {
    const onCopyCode = vi.fn();
    const onCopyLink = vi.fn();
    render(
      <PublicationInstallView
        {...defaultProps}
        onCopyCode={onCopyCode}
        onCopyLink={onCopyLink}
      />,
    );

    fireEvent.click(screen.getByRole('button', { name: 'Скопировать код установки' }));
    fireEvent.click(screen.getByRole('button', { name: 'Скопировать ссылку загрузчика' }));

    expect(onCopyCode).toHaveBeenCalledTimes(1);
    expect(onCopyLink).toHaveBeenCalledTimes(1);
  });

  it('distinguishes code and loader-link copy success', () => {
    const view = render(
      <PublicationInstallView {...defaultProps} copiedTarget="code" />,
    );

    expect(screen.getByRole('status', {
      name: 'Результат копирования',
    })).toHaveTextContent('Код скопирован. Его можно отправить разработчику.');
    expect(screen.queryByText('Ссылка загрузчика скопирована.')).not.toBeInTheDocument();

    view.rerender(
      <PublicationInstallView {...defaultProps} copiedTarget="link" />,
    );

    expect(screen.getByRole('status', {
      name: 'Результат копирования',
    })).toHaveTextContent('Ссылка загрузчика скопирована.');
    expect(screen.queryByText('Код скопирован. Его можно отправить разработчику.')).not.toBeInTheDocument();
  });

  it('exposes both literal values as readonly controls and selects them for manual copying', () => {
    render(<PublicationInstallView {...defaultProps} copyError />);

    expect(screen.getByRole('alert')).toHaveTextContent(/скопируйте вручную оба значения ниже/i);
    const code = screen.getByLabelText<HTMLTextAreaElement>('Код установки для ручного копирования');
    const loaderUrl = screen.getByLabelText<HTMLTextAreaElement>('Ссылка загрузчика для ручного копирования');
    expect(code).toHaveValue(
      '<script src="https://widgets.kaigo.space/embed/stable-widget.js" async></script>',
    );
    expect(loaderUrl).toHaveValue(publication.embed_url);
    expect(code).toHaveAttribute('readonly');
    expect(loaderUrl).toHaveAttribute('readonly');

    const selectCode = vi.spyOn(code, 'select');
    const selectLoaderUrl = vi.spyOn(loaderUrl, 'select');
    fireEvent.focus(code);
    fireEvent.click(loaderUrl);
    expect(selectCode).toHaveBeenCalledTimes(1);
    expect(selectLoaderUrl).toHaveBeenCalledTimes(1);
  });

  it('uses a generic published status and a safe access status when optional data is absent', () => {
    render(
      <PublicationInstallView
        {...defaultProps}
        versionOrdinal={undefined}
        subscription={null}
      />,
    );

    expect(screen.getByText('Доступ к публикации активен.')).toBeVisible();
    expect(screen.getByRole('status')).toHaveTextContent(
      'Виджет опубликован и доступен на разрешённых сайтах.',
    );
  });
});
