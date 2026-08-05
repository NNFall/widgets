import { describe, expect, it } from 'vitest';

import {
  STUDIO_STAGES,
  projectStatusLabel,
  runStatusPresentation,
  safeActivityForEvent,
} from './studioPresentation';

describe('Studio presentation', () => {
  it('maps every primary stage in backend order', () => {
    expect(STUDIO_STAGES.map(({ id }) => id)).toEqual([
      'art_direction',
      'foundation',
      'identity',
      'conversation',
      'motion_polish',
      'validation',
      'agent_build',
    ]);
    expect(STUDIO_STAGES.map(({ label }) => label)).toEqual([
      'Образ и характер',
      'Основа виджета',
      'Стиль и бренд',
      'Диалог',
      'Анимация и детали',
      'Проверка качества',
      'Подготовка AI-консультанта',
    ]);
  });

  it('maps project and run states to plain Russian copy', () => {
    expect(projectStatusLabel('completed')).toBe('Готов к работе');
    expect(projectStatusLabel('internal_pending')).toBe('Состояние уточняется');
    expect(runStatusPresentation('failed')).toEqual({
      title: 'Нужен повторный запуск',
      detail: 'Сохранённые данные и доступный черновик не потеряны.',
    });
  });

  it('uses a safe stage message instead of unknown raw event content', () => {
    expect(safeActivityForEvent({
      type: 'provider.internal',
      stage: 'identity',
      message: 'Gemini bytes=991 teal launcher',
    })).toBe('Настраиваем стиль под ваш бренд');
  });
});
