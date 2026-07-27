import { CheckCircle, CircleNotch, WarningCircle } from '@phosphor-icons/react';
import { AnimatePresence, motion } from 'motion/react';

import type { BuilderEvent } from './types';

const EVENT_LABELS: Record<string, string> = {
  'run.created': 'Запуск создан',
  'run.completed': 'Запуск завершён',
  'run.failed': 'Запуск остановлен с ошибкой',
  'run.cancelled': 'Запуск отменён',
  'reference.started': 'Анализ сайта начат',
  'reference.completed': 'Анализ сайта завершён',
  'reference.failed': 'Анализ сайта не выполнен',
  'direction.judged': 'Направление выбрано',
  'refinement.started': 'Доработка начата',
  'stage.started': 'Этап начат',
  'stage.completed': 'Этап завершён',
  'stage.failed': 'Этап остановлен',
  'repair.started': 'Исправление начато',
  'repair.completed': 'Исправление завершено',
  'artifact.validated': 'Техническая проверка',
  'artifact.committed': 'Версия готова',
  'artifact.seeded': 'Исходная версия загружена',
  'visual_audit.started': 'Визуальная проверка начата',
  'visual_audit.completed': 'Визуальная проверка завершена',
  'visual_audit.passed': 'Визуальная проверка пройдена',
  'visual_audit.blocked': 'Нужна визуальная доработка',
  'visual_critic.completed': 'Отчёт визуального критика',
  'visual_judge.completed': 'Решение визуального судьи',
  'visual_repair.started': 'Визуальная доработка начата',
  'visual_repair.completed': 'Визуальная доработка завершена',
  'visual_repair.verifier_started': 'Проверка исправлений начата',
  'visual_repair.verifier_completed': 'Проверка исправлений завершена',
  'screenshot.captured': 'Снимок сделан',
};

const STAGE_LABELS: Record<string, string> = {
  art_direction: 'Арт-направление',
  foundation: 'Основа виджета',
  identity: 'Фирменный стиль',
  conversation: 'Диалог',
  motion_polish: 'Анимации и отделка',
  validation: 'Техническая проверка',
  agent_build: 'Агентская сборка',
};

const CHANGE_LABELS: Record<string, string> = {
  art_direction: 'арт-направление',
  body_html: 'структура',
  css: 'оформление',
  javascript: 'поведение',
  layout_contract: 'геометрия',
  theme_tokens: 'палитра',
};

function StatusIcon({ status }: { status: string }) {
  if (status === 'completed') return <CheckCircle aria-hidden size={18} weight="fill" />;
  if (status === 'failed' || status === 'cancelled') return <WarningCircle aria-hidden size={18} weight="fill" />;
  return <CircleNotch aria-hidden size={18} weight="bold" />;
}

export function StudioTimeline({ events, running }: { events: BuilderEvent[]; running: boolean }) {
  return (
    <section className="studio-timeline" aria-labelledby="studio-timeline-title">
      <div className="studio-timeline__head">
        <div>
          <p className="studio-kicker">Ход сборки</p>
          <h2 id="studio-timeline-title">Диалог с генератором</h2>
        </div>
        <span className="studio-timeline__signal" data-running={running} aria-label={running ? 'Генерация выполняется' : 'Генерация не выполняется'} />
      </div>
      <div className="studio-timeline__list" aria-live="polite">
        {events.length === 0 ? (
          <div className="studio-timeline__empty">
            <span aria-hidden />
            <strong>{running ? 'Kaigo готовит анализ сайта' : 'Здесь появятся этапы сборки'}</strong>
            <p>Каждое изменение и результат проверки будут сохранены целиком.</p>
          </div>
        ) : (
          <AnimatePresence initial={false}>
            {events.map((item) => (
              <motion.article
                className="studio-event"
                data-status={item.status}
                key={`${item.run_id}-${item.sequence}`}
                initial={{ opacity: 0, y: 10 }}
                animate={{ opacity: 1, y: 0 }}
                exit={{ opacity: 0 }}
                transition={{ type: 'spring', stiffness: 120, damping: 20 }}
              >
                <div className="studio-event__icon"><StatusIcon status={item.status} /></div>
                <div className="studio-event__content">
                  <div className="studio-event__meta">
                    <strong>{EVENT_LABELS[item.type] ?? 'Событие генерации'}</strong>
                    <span>{item.stage ? STAGE_LABELS[item.stage] : `Шаг ${item.sequence}`}</span>
                  </div>
                  <p>{item.message}</p>
                  {item.changes.length > 0 && (
                    <small>Изменено: {item.changes.map((change) => CHANGE_LABELS[change] ?? change).join(' · ')}</small>
                  )}
                  {item.issues.length > 0 && (
                    <ul>
                      {item.issues.map((issue) => <li key={`${issue.code}-${issue.field}-${issue.message}`}>{issue.message}</li>)}
                    </ul>
                  )}
                </div>
              </motion.article>
            ))}
          </AnimatePresence>
        )}
      </div>
    </section>
  );
}
