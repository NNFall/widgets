import { cleanup, render, screen, within } from '@testing-library/react';
import { afterEach, describe, expect, it } from 'vitest';

import stylesSource from '../styles.css?raw';
import { LandingPage } from './LandingPage';

afterEach(cleanup);

describe('Landing visual contracts', () => {
  it('uses one metallic promise surface instead of three detached cards', () => {
    render(<LandingPage />);

    const section = document.querySelector('.free-result-section');
    expect(section).toHaveAttribute('data-surface', 'metal');
    expect(screen.getByRole('heading', {
      name: 'Сначала посмотрите результат. Оплатите только публикацию.',
    })).toBeVisible();

    const promise = screen.getByRole('list', { name: 'Что входит в бесплатный старт' });
    expect(within(promise).getAllByRole('listitem')).toHaveLength(3);
    expect(within(promise).getByText('Экспресс-версия бесплатно')).toBeVisible();
    expect(within(promise).getByText('Проверьте виджет в чате')).toBeVisible();
    expect(within(promise).getByText('Оплата только перед запуском')).toBeVisible();
  });

  it('keeps creation and publication inside one coherent process surface', () => {
    render(<LandingPage />);

    const board = document.querySelector('.how-process-board');
    expect(board).toHaveAttribute('data-journey-layout', 'unified');
    expect(board?.querySelector('.how-route')).toBeNull();
    expect(within(board as HTMLElement).getByTestId('how-live-preview')).toBeVisible();

    const launch = within(board as HTMLElement).getByTestId('how-publish-path');
    expect(launch).toHaveAttribute('data-launch-integrated', 'true');
    const action = within(launch).getByTestId('how-publish-action');
    expect(within(action).getByRole('link', { name: 'Создать бесплатную версию' })).toBeVisible();
    expect(within(action).getByText('Без карты. Оплата только перед публикацией.')).toBeVisible();
  });

  it('frames the after state as an embedded AI experience with a supported action', () => {
    render(<LandingPage />);

    expect(screen.getByRole('heading', { name: 'Один сайт. Два опыта.' })).toBeVisible();
    expect(screen.getByText('Теперь ваш сайт отвечает посетителю через AI')).toBeVisible();
    expect(document.querySelector('[data-widget-placement="embedded"]')).not.toBeNull();

    const action = screen.getByTestId('case-action');
    expect(within(action).getByRole('link', { name: 'Создать виджет для своего сайта' })).toBeVisible();
    expect(within(action).getByText('Сначала получите бесплатный предпросмотр')).toBeVisible();
  });

  it('uses a split final composition with clearly named before and after sites', () => {
    render(<LandingPage />);

    const finalContent = document.querySelector('.final-cta-content');
    expect(finalContent).toHaveAttribute('data-layout', 'split');
    expect(screen.getByText('Сайт без Kaigo')).toBeVisible();
    expect(screen.getByText('Сайт с AI-консультантом')).toBeVisible();
    expect(screen.getByText('Ссылка нужна только для анализа. Карту не попросим.')).toBeVisible();
  });

  it('defines one white, metallic and orange landing palette', () => {
    expect(stylesSource).toMatch(/--landing-paper:\s*oklch\(/);
    expect(stylesSource).toMatch(/--landing-metal:\s*oklch\(/);
    expect(stylesSource).toMatch(/--landing-metal-deep:\s*oklch\(/);
    expect(stylesSource).toMatch(/--landing-accent:\s*oklch\(/);
  });
});
