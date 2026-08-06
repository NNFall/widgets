import { ArrowRight, Sparkle } from '@phosphor-icons/react';
import type { ReactNode } from 'react';

type WidgetPreviewCardProps = {
  className?: string;
  compact?: boolean;
  launcher?: ReactNode;
  question?: string;
};

export function WidgetPreviewCard({
  className = '',
  compact = false,
  launcher,
  question = 'Чем я могу помочь?',
}: WidgetPreviewCardProps) {
  return (
    <div
      className={`widget-preview-card${compact ? ' widget-preview-card--compact' : ''}${className ? ` ${className}` : ''}`}
      data-widget-shape="vertical"
    >
      <div className="widget-preview-card__head">
        <span className="widget-preview-card__avatar" aria-hidden="true">K</span>
        <span>
          <strong>AI-консультант</strong>
          <small><i aria-hidden="true" />На связи</small>
        </span>
      </div>
      <div className="widget-preview-card__message">
        <Sparkle size={18} weight="fill" aria-hidden="true" />
        <span>
          <strong>Я изучил ваш сайт</strong>
          <small>Знаю услуги, условия и ответы на частые вопросы.</small>
        </span>
      </div>
      <div className="widget-preview-card__suggestion">
        <span>{question}</span>
        {launcher ?? (
          <span className="widget-preview__launcher" aria-hidden="true">
            <ArrowRight size={16} weight="bold" />
          </span>
        )}
      </div>
      <div className="widget-preview-card__input"><span>Введите вопрос</span><i aria-hidden="true" /></div>
      <small className="widget-preview-card__source">Ответы основаны на вашем сайте</small>
    </div>
  );
}
