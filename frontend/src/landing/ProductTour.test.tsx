import { cleanup, render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, describe, expect, it } from 'vitest';

import stylesSource from '../styles.css?raw';
import { ProductTour } from './ProductTour';

afterEach(cleanup);

describe('ProductTour', () => {
  it('explains the complete path through four concrete product scenes', () => {
    render(<ProductTour />);

    const tour = screen.getByRole('region', { name: 'Как Kaigo создаёт AI-сотрудника' });
    const scenes = tour.querySelectorAll<HTMLElement>('[data-tour-step]');

    expect(scenes).toHaveLength(4);
    expect(scenes[0]).toHaveAttribute('data-active', 'true');
    expect(scenes[0]).toHaveAttribute('aria-hidden', 'false');
    expect(scenes[1]).toHaveAttribute('aria-hidden', 'true');

    expect(within(scenes[0]).getByText('https://teply-hleb.ru')).toBeInTheDocument();
    expect(within(scenes[0]).getByText('Хочу красивого AI-консультанта для заказов')).toBeInTheDocument();
    expect(within(scenes[0]).getByRole('img', { name: 'Свежая выпечка на рабочем столе пекарни' }))
      .toHaveAttribute('src', '/assets/product-tour-bakery.webp');

    expect(within(scenes[1]).getByText('Обычно 10–20 минут')).toBeInTheDocument();
    expect(within(scenes[1]).getByText('12 страниц изучено')).toBeInTheDocument();
    expect(within(scenes[1]).getByText('Собираем сценарий разговора')).toBeInTheDocument();

    expect(within(scenes[2]).getByText('Компьютер')).toBeInTheDocument();
    expect(within(scenes[2]).getByText('Телефон')).toBeInTheDocument();
    expect(within(scenes[2]).getByText('Первая генерация бесплатно')).toBeInTheDocument();
    expect(within(scenes[2]).getByText('Сделайте ответы короче и дружелюбнее')).toBeInTheDocument();

    expect(within(scenes[3]).getByText('Пример строки для установки')).toBeInTheDocument();
    expect(within(scenes[3]).getByText(/widget\.js/)).toBeInTheDocument();
    expect(within(scenes[3]).getByText('Какие торты можно заказать к субботе?')).toBeInTheDocument();
    expect(tour.querySelectorAll('[data-widget-shape="vertical"]')).toHaveLength(2);
  });

  it('lets a visitor move through the story without leaving the screen', async () => {
    const user = userEvent.setup();
    render(<ProductTour />);

    const tour = screen.getByRole('region', { name: 'Как Kaigo создаёт AI-сотрудника' });
    const next = within(tour).getByRole('button', { name: 'Следующий этап' });
    const previous = within(tour).getByRole('button', { name: 'Предыдущий этап' });

    expect(previous).toBeDisabled();
    await user.click(next);
    expect(tour).toHaveAttribute('data-active-step', '2');
    expect(screen.getByRole('heading', { name: 'Kaigo изучает сайт и собирает AI-сотрудника' }))
      .toBeVisible();

    await user.click(within(tour).getByRole('button', { name: /Показать этап 4:/ }));
    expect(tour).toHaveAttribute('data-active-step', '4');
    expect(next).toBeDisabled();
    expect(screen.getByRole('heading', { name: 'Одна строка кода, и AI-сотрудник уже на сайте' }))
      .toBeVisible();

    await user.click(previous);
    expect(tour).toHaveAttribute('data-active-step', '3');
  });

  it('keeps standalone mode focused on the story instead of linking to itself', () => {
    render(<ProductTour standalone />);

    expect(screen.queryByRole('link', { name: 'Открыть демонстрацию отдельно' })).not.toBeInTheDocument();
    expect(screen.getByRole('link', { name: 'Создать бесплатную версию' })).toHaveAttribute('href', '/studio');
  });

  it('reserves a stable viewport and accessible controls for the visual story', () => {
    const compactStart = stylesSource.indexOf('@media (max-width: 390px) {\n  .product-tour-page');
    const compactEnd = stylesSource.indexOf('@media (prefers-reduced-motion: reduce)', compactStart);
    const compactTourMedia = stylesSource.slice(compactStart, compactEnd);

    expect(compactStart).toBeGreaterThan(-1);
    expect(stylesSource).toMatch(
      /\.product-tour-section\s*\{[^}]*min-height:\s*100svh/s,
    );
    expect(stylesSource).toMatch(
      /\.product-tour__arrows button\s*\{[^}]*min-width:\s*44px;[^}]*min-height:\s*44px/s,
    );
    expect(stylesSource).toMatch(
      /@media \(prefers-reduced-motion: reduce\)[^{]*\{[\s\S]*?\.product-tour__scene[\s\S]*?animation:\s*none !important/s,
    );
    expect(stylesSource).toMatch(
      /@media \(max-width: 767px\)[\s\S]*?\.product-tour-section--standalone\s*\{[^}]*min-height:\s*calc\(100svh - 66px\)/s,
    );
    expect(stylesSource).toMatch(
      /@media \(max-width: 767px\)[\s\S]*?\.product-tour-section\s*\{[^}]*grid-template-rows:\s*auto auto auto;[^}]*align-content:\s*center/s,
    );
    expect(compactTourMedia).toMatch(
      /\.product-tour-page__header > a:last-child\s*\{[^}]*color:\s*var\(--tour-paper\)/s,
    );
  });
});
