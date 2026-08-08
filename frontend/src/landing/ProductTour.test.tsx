import { act, cleanup, fireEvent, render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, describe, expect, it, vi } from 'vitest';

import stylesSource from '../styles.css?raw';
import { ProductTour } from './ProductTour';

const motionState = {
  active: true,
  reducedMotion: false,
};

vi.mock('../shared/MotionActivity', () => ({
  useMotionActivity: () => ({
    active: motionState.active,
    reducedMotion: motionState.reducedMotion,
    ref: { current: null },
  }),
}));

afterEach(() => {
  cleanup();
  vi.useRealTimers();
  motionState.active = true;
  motionState.reducedMotion = false;
});

describe('ProductTour', () => {
  it('explains the complete customer path through three plain-language stages', () => {
    render(<ProductTour />);

    const tour = screen.getByRole('region', { name: 'Как Kaigo создаёт AI-сотрудника' });
    const scenes = tour.querySelectorAll<HTMLElement>('[data-tour-step]');

    expect(scenes).toHaveLength(3);
    expect(scenes[0]).toHaveAttribute('data-active', 'true');
    expect(scenes[1]).toHaveAttribute('aria-hidden', 'true');

    expect(within(scenes[0]).getByRole('heading', { name: 'Вставьте ссылку на ваш сайт' })).toBeVisible();
    expect(within(scenes[0]).getByText('Вставьте ссылку на действующий сайт.')).toBeInTheDocument();
    expect(within(scenes[0]).getByText('Если есть пожелания, опишите их обычным текстом.')).toBeInTheDocument();
    expect(within(scenes[0]).getByText('https://novaflow.ru')).toBeInTheDocument();
    expect(within(scenes[0]).getByRole('img', { name: 'Металлическое цифровое ядро NovaFlow' }))
      .toHaveAttribute('src', '/assets/product-tour-digital-core.webp');
    expect(within(scenes[0]).getByRole('img', { name: 'Металлическое цифровое ядро NovaFlow' }))
      .toHaveAttribute('loading', 'lazy');

    expect(within(scenes[1]).getByRole('heading', {
      hidden: true,
      name: 'Через 10–20 минут проверьте результат в Studio',
    })).toBeInTheDocument();
    expect(within(scenes[1]).getByText('Откройте готовый виджет прямо в Studio.')).toBeInTheDocument();
    expect(within(scenes[1]).getByText('Задайте ему несколько вопросов как клиент.')).toBeInTheDocument();
    expect(within(scenes[1]).getByText('Первая версия готова')).toBeInTheDocument();
    expect(within(scenes[1]).getByText('Проверка результата бесплатна')).toBeInTheDocument();
    expect(within(scenes[1]).getByText('Сохраните пожелание. Доработки доступны после выбора тарифа.')).toBeInTheDocument();
    expect(within(scenes[1]).queryByText('Телефон')).not.toBeInTheDocument();
    expect(within(scenes[1]).queryByRole('img', { name: /сайт/i })).not.toBeInTheDocument();

    expect(within(scenes[2]).getByRole('heading', {
      hidden: true,
      name: 'Добавьте AI-сотрудника на сайт',
    })).toBeInTheDocument();
    expect(within(scenes[2]).getByText('Скопируйте одну строку кода или передайте её разработчику.')).toBeInTheDocument();
    expect(within(scenes[2]).getByText('Пример строки для установки')).toBeInTheDocument();
    expect(within(scenes[2]).getByText(/widget\.js/)).toBeInTheDocument();
    expect(within(scenes[2]).getByText('Можно подключить отчёты для руководителя?')).toBeInTheDocument();
    expect(tour.querySelectorAll('[data-widget-shape="vertical"]')).toHaveLength(2);
    expect(tour.querySelectorAll('img[src="/assets/product-tour-digital-core-avatar.webp"]')).toHaveLength(2);

    expect(tour).not.toHaveTextContent(/пекар|выпеч|торт/i);
    expect(screen.queryByRole('link', { name: 'Открыть демонстрацию отдельно' })).not.toBeInTheDocument();
  });

  it('keeps arrows and three explicit stages available while manual navigation loops', async () => {
    const user = userEvent.setup();
    render(<ProductTour />);

    const tour = screen.getByRole('region', { name: 'Как Kaigo создаёт AI-сотрудника' });
    const next = within(tour).getByRole('button', { name: 'Следующий этап' });
    const previous = within(tour).getByRole('button', { name: 'Предыдущий этап' });

    expect(previous).toBeEnabled();
    expect(next).toBeEnabled();
    expect(within(tour).getAllByRole('button', { name: /Показать этап/ })).toHaveLength(3);

    await user.click(next);
    expect(tour).toHaveAttribute('data-active-step', '2');

    await user.click(within(tour).getByRole('button', { name: /Показать этап 3:/ }));
    expect(tour).toHaveAttribute('data-active-step', '3');

    await user.click(next);
    expect(tour).toHaveAttribute('data-active-step', '1');

    await user.click(previous);
    expect(tour).toHaveAttribute('data-active-step', '3');
  });

  it('advances automatically, loops, and pauses while the visitor is interacting', () => {
    vi.useFakeTimers();
    render(<ProductTour />);

    const tour = screen.getByRole('region', { name: 'Как Kaigo создаёт AI-сотрудника' });
    expect(tour).toHaveAttribute('data-active-step', '1');
    expect(tour).toHaveAttribute('data-autoplay', 'true');

    act(() => vi.advanceTimersByTime(8_000));
    expect(tour).toHaveAttribute('data-active-step', '2');
    act(() => vi.advanceTimersByTime(8_000));
    expect(tour).toHaveAttribute('data-active-step', '3');
    act(() => vi.advanceTimersByTime(8_000));
    expect(tour).toHaveAttribute('data-active-step', '1');

    fireEvent.pointerEnter(tour);
    act(() => vi.advanceTimersByTime(16_000));
    expect(tour).toHaveAttribute('data-active-step', '1');

    fireEvent.pointerLeave(tour);
    act(() => vi.advanceTimersByTime(8_000));
    expect(tour).toHaveAttribute('data-active-step', '2');

    fireEvent.click(within(tour).getByRole('button', { name: 'Остановить автолистание' }));
    expect(tour).toHaveAttribute('data-autoplay', 'false');
    act(() => vi.advanceTimersByTime(16_000));
    expect(tour).toHaveAttribute('data-active-step', '2');

    fireEvent.click(within(tour).getByRole('button', { name: 'Продолжить автолистание' }));
    act(() => vi.advanceTimersByTime(8_000));
    expect(tour).toHaveAttribute('data-active-step', '3');
  });

  it('does not auto-advance when motion is inactive or reduced', () => {
    vi.useFakeTimers();
    motionState.active = false;
    motionState.reducedMotion = true;
    render(<ProductTour />);

    const tour = screen.getByRole('region', { name: 'Как Kaigo создаёт AI-сотрудника' });
    expect(tour).toHaveAttribute('data-autoplay', 'false');
    expect(within(tour).getByRole('button', { name: 'Автолистание отключено настройками системы' })).toBeDisabled();
    expect(within(tour).getByText('Автолистание отключено')).toBeInTheDocument();
    act(() => vi.advanceTimersByTime(24_000));
    expect(tour).toHaveAttribute('data-active-step', '1');
  });

  it('stays paused until both pointer hover and keyboard focus have ended', () => {
    vi.useFakeTimers();
    render(<ProductTour />);

    const tour = screen.getByRole('region', { name: 'Как Kaigo создаёт AI-сотрудника' });
    const next = within(tour).getByRole('button', { name: 'Следующий этап' });

    fireEvent.pointerEnter(tour);
    fireEvent.focus(next);
    fireEvent.pointerLeave(tour);
    expect(tour).toHaveAttribute('data-autoplay', 'false');
    act(() => vi.advanceTimersByTime(8_000));
    expect(tour).toHaveAttribute('data-active-step', '1');

    fireEvent.blur(next, { relatedTarget: null });
    expect(tour).toHaveAttribute('data-autoplay', 'true');

    fireEvent.focus(next);
    fireEvent.pointerEnter(tour);
    fireEvent.blur(next, { relatedTarget: null });
    expect(tour).toHaveAttribute('data-autoplay', 'false');
    fireEvent.pointerLeave(tour);
    expect(tour).toHaveAttribute('data-autoplay', 'true');
  });

  it('keeps standalone mode focused on the same story', () => {
    render(<ProductTour standalone />);

    expect(screen.getByRole('link', { name: 'Создать бесплатную версию' })).toHaveAttribute('href', '/studio');
  });

  it('uses restrained headings and visible progress controls for the visual story', () => {
    expect(stylesSource).toMatch(/\.product-tour__copy h2\s*\{[^}]*font-size:\s*clamp\(30px,\s*2\.4vw,\s*44px\)/s);
    expect(stylesSource).toMatch(/\.product-tour__instructions\s*\{[^}]*font-size:\s*clamp\(15px,\s*1\.05vw,\s*18px\)/s);
    expect(stylesSource).toMatch(/\.product-tour__arrows button\s*\{[^}]*min-width:\s*44px;[^}]*min-height:\s*44px/s);
    expect(stylesSource).toMatch(/\.product-tour-section\[data-autoplay='true'\][^{]*\.product-tour__steps button\[data-active='true'\]::after\s*\{[^}]*animation:\s*product-tour-progress 8000ms linear/s);
    expect(stylesSource).toMatch(/\.product-tour-section\[data-autoplay='false'\][^{]*\.product-tour__steps button\[data-active='true'\]::after\s*\{[^}]*animation:\s*none;[^}]*transform:\s*scaleX\(0\)/s);
    expect(stylesSource).toMatch(/@media \(max-width: 1180px\)[\s\S]*?\.product-tour__scene\s*\{[^}]*grid-template-columns:\s*minmax\(280px,\s*\.85fr\) minmax\(0,\s*1\.15fr\)/s);
    expect(stylesSource).toMatch(/@media \(prefers-reduced-motion: reduce\)[^{]*\{[\s\S]*?\.product-tour__steps button::after[\s\S]*?animation:\s*none !important/s);
  });
});
