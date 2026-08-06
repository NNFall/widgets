import { cleanup, render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it } from 'vitest';

import { WidgetPreviewCard } from './WidgetPreviewCard';

afterEach(cleanup);

describe('WidgetPreviewCard', () => {
  it('presents the demo as a readable vertical AI widget', () => {
    const { container } = render(
      <WidgetPreviewCard question="Какая выпечка есть сегодня?" />,
    );

    expect(container.firstChild).toHaveAttribute('data-widget-shape', 'vertical');
    expect(screen.getByText('AI-консультант')).toBeVisible();
    expect(screen.getByText('На связи')).toBeVisible();
    expect(screen.getByText('Я изучил ваш сайт')).toBeVisible();
    expect(screen.getByText('Какая выпечка есть сегодня?')).toBeVisible();
    expect(screen.getByText('Введите вопрос')).toBeVisible();
    expect(screen.getByText('Ответы основаны на вашем сайте')).toBeVisible();
  });
});
