import { cleanup, render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, describe, expect, it } from 'vitest';

import { LandingPage } from './LandingPage';

afterEach(cleanup);

describe('LandingPage sections', () => {
  it('renders the free-result promise immediately after the hero with truthful limits', () => {
    render(<LandingPage />);

    const sections = Array.from(document.querySelectorAll('section[data-landing-section]'));
    expect(sections).toHaveLength(9);
    expect(sections[1]).toHaveClass('product-tour-section');
    expect(sections[2]).toHaveClass('free-result-section');
    expect(screen.getByRole('textbox', { name: 'Ссылка на действующий сайт' })).toBeVisible();
    expect(screen.queryByRole('link', { name: 'Открыть демонстрацию отдельно' })).not.toBeInTheDocument();
    expect(screen.getByRole('heading', { name: 'Сначала посмотрите результат. Оплатите только публикацию.' })).toBeVisible();
    expect(screen.getByText(/экспресс-версия бесплатно/i)).toBeVisible();
    expect(screen.getByText(/Google или Яндекс/i)).toBeVisible();
    expect(screen.getByText(/карта не нужна для генерации/i)).toBeVisible();
    expect(screen.getByText(/проверьте виджет в чате/i)).toBeVisible();
    expect(screen.getByText(/тариф понадобится, когда решите опубликовать/i)).toBeVisible();
    expect(screen.getByText(/одна на подтверждённый аккаунт/i)).toBeVisible();
    expect(screen.getByText(/обычно 10–20 минут, сложные сайты дольше/i)).toBeVisible();
    expect(screen.getAllByRole('textbox', { name: 'Пожелание к AI-виджету' })).toHaveLength(2);
  });

  it('uses the agreed B2B promise and free-version copy in the hero', () => {
    render(<LandingPage />);

    expect(screen.getByRole('heading', { name: /Через 10 минут.*наш бизнес.*использует AI/i })).toBeVisible();
    expect(screen.getByText(/Kaigo бесплатно создаст первую версию AI-виджета/i)).toBeVisible();
    expect(screen.getByText(/Сначала посмотрите результат и проверьте ответы/i)).toBeVisible();
    expect(screen.getByText(/Оплата нужна только перед публикацией/i)).toBeVisible();
    expect(screen.getByRole('heading', { name: /Получите бесплатную экспресс-версию.*проверьте её сами/i })).toBeInTheDocument();
    expect(screen.getAllByRole('button', { name: 'Получить бесплатную версию' })).toHaveLength(2);
  });

  it('uses different business examples and one consistent vertical widget model', () => {
    render(<LandingPage />);

    expect(document.querySelector('main.landing-page')).not.toBeNull();
    expect(document.querySelector('[data-site-variant="bakery"]')).not.toBeNull();
    expect(document.querySelector('[data-site-variant="ceramics"]')).not.toBeNull();
    expect(document.querySelector('[data-site-variant="architecture"]')).not.toBeNull();
    expect(screen.getAllByText('Тёплый хлеб').length).toBeGreaterThan(0);
    expect(screen.getAllByText('Тихая форма').length).toBeGreaterThan(0);

    const widgets = document.querySelectorAll('[data-widget-shape="vertical"]');
    expect(widgets.length).toBeGreaterThanOrEqual(4);
  });

  it('describes the creation path as three readable stages in the real FORMA case', () => {
    render(<LandingPage />);

    const tour = screen.getByRole('region', { name: 'Как Kaigo создаёт AI-сотрудника' });
    expect(within(tour).getByText('Реальный кейс · FORMA')).toBeVisible();
    const stages = within(tour).getByRole('navigation', { name: 'Этапы создания AI-сотрудника' });
    expect(within(stages).getAllByRole('button')).toHaveLength(3);
    expect(within(stages).getByRole('button', { name: /этап 1.*Дайте Kaigo ссылку/i })).toBeVisible();
    expect(within(stages).getByRole('button', { name: /этап 2.*проверьте результат/i })).toBeVisible();
    expect(within(stages).getByRole('button', { name: /этап 3.*AI-консультанта/i })).toBeVisible();
  });

  it('renders the complete marketing narrative', () => {
    render(<LandingPage />);

    expect(screen.getByRole('heading', { name: 'Дайте Kaigo ссылку на ваш сайт' })).toBeVisible();
    expect(screen.getByText('От одной ссылки до AI-консультанта на сайте')).toBeVisible();
    expect(screen.getByText(/Можно начать бесплатно. Карта не нужна/i)).toBeVisible();
    expect(screen.getByRole('heading', { name: 'Что видит Kaigo' })).toBeVisible();
    expect(screen.getByRole('heading', { name: /Не просто чат/ })).toBeVisible();
    expect(screen.getByRole('heading', { name: 'Готовый вариант — под вашим контролем' })).toBeVisible();
    expect(document.querySelectorAll('section[data-landing-section]')).toHaveLength(9);
    expect(screen.getAllByRole('textbox', { name: /ссылка на.*сайт/i })).toHaveLength(2);
  });

  it('exposes an accessible before and after case switch', async () => {
    const user = userEvent.setup();
    render(<LandingPage />);

    const before = screen.getByRole('button', { name: 'До' });
    const after = screen.getByRole('button', { name: 'После' });

    expect(after).toHaveAttribute('aria-pressed', 'true');
    await user.click(before);
    expect(before).toHaveAttribute('aria-pressed', 'true');
    expect(after).toHaveAttribute('aria-pressed', 'false');
  });

  it('opens and closes FAQ answers with native buttons', async () => {
    const user = userEvent.setup();
    render(<LandingPage />);

    const timeQuestion = screen.getByRole('button', { name: /Сколько времени/ });
    expect(timeQuestion).toHaveAttribute('aria-expanded', 'false');
    expect(timeQuestion).toHaveAttribute('aria-controls', 'faq-answer-1');
    expect(document.getElementById('faq-answer-1')).not.toBeInTheDocument();

    await user.click(timeQuestion);
    expect(timeQuestion).toHaveAttribute('aria-expanded', 'true');
    const timeAnswer = document.getElementById('faq-answer-1');
    expect(timeAnswer).toBeInTheDocument();
    expect(timeAnswer).toHaveTextContent(/первую версию/i);

    await user.click(timeQuestion);
    expect(timeQuestion).toHaveAttribute('aria-expanded', 'false');
  });

  it('does not expose demo-only controls or unavailable legal pages as actions', () => {
    render(<LandingPage />);

    expect(screen.queryByRole('button', { name: 'Какие торты можно заказать к субботе?' })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Предпросмотр' })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Опубликовать' })).not.toBeInTheDocument();
    expect(screen.getByText('Какие торты можно заказать к субботе?', { selector: 'span' })).toBeVisible();
    expect(screen.getByText('Предпросмотр', { selector: 'span' })).toBeVisible();
    expect(screen.getByText('Опубликовать', { selector: 'span' })).toBeVisible();
    expect(screen.queryByRole('link', { name: 'Политика конфиденциальности' })).not.toBeInTheDocument();
    expect(screen.queryByRole('link', { name: 'Условия использования' })).not.toBeInTheDocument();
  });

  it('promises only preview, dialogue checks, publication and connection', () => {
    render(<LandingPage />);

    const studioDemo = document.querySelector('.studio-demo');
    expect(studioDemo).not.toBeNull();
    expect(screen.getByText('Предпросмотр', { selector: '.studio-demo__checklist strong' })).toBeVisible();
    expect(screen.getByText('Проверка диалога', { selector: '.studio-demo__checklist strong' })).toBeVisible();
    expect(screen.getByText('Публикация и подключение', { selector: '.studio-demo__checklist strong' })).toBeVisible();
    expect(screen.queryByRole('textbox', { name: 'Пожелание к виджету' })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Применить изменение' })).not.toBeInTheDocument();
    expect(screen.queryByText('История версий')).not.toBeInTheDocument();
    expect(screen.queryByText('Можно дорабатывать')).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Можно изменить ответы и характер общения?' })).not.toBeInTheDocument();
    expect(screen.queryByText(/готов к проверке и доработке/i)).not.toBeInTheDocument();
    expect(screen.queryByText(/уточните поведение/i)).not.toBeInTheDocument();
    expect(screen.queryByText(/дальнейшую доработку/i)).not.toBeInTheDocument();
  });
});
