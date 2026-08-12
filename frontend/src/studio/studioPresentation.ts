import type { BuilderRunStatus, BuilderStage } from './types';

export const STUDIO_STAGES: ReadonlyArray<{
  id: BuilderStage;
  label: string;
  activity: string;
}> = [
  {
    id: 'reference_analysis',
    label: 'Анализ сайта',
    activity: 'Загружаем сайт и изучаем его структуру, содержание и визуальный язык',
  },
  {
    id: 'persona',
    label: 'Выбор сотрудника',
    activity: 'Подбираем роль, характер и манеру общения сотрудника',
  },
  {
    id: 'art_direction',
    label: 'Образ и характер',
    activity: 'Изучаем структуру и содержание сайта',
  },
  {
    id: 'composition',
    label: 'Подбор шаблонов',
    activity: 'Подбираем подходящие элементы и анимации из библиотеки Kaigo',
  },
  {
    id: 'foundation',
    label: 'Основа виджета',
    activity: 'Собираем основу будущего виджета',
  },
  {
    id: 'identity',
    label: 'Стиль и бренд',
    activity: 'Настраиваем стиль под ваш бренд',
  },
  {
    id: 'conversation',
    label: 'Диалог',
    activity: 'Настраиваем полезный диалог с посетителем',
  },
  {
    id: 'motion_polish',
    label: 'Анимация и детали',
    activity: 'Дорабатываем движения и детали',
  },
  {
    id: 'validation',
    label: 'Проверка качества',
    activity: 'Проверяем результат перед показом',
  },
  {
    id: 'agent_build',
    label: 'Подготовка AI-консультанта',
    activity: 'Подготавливаем AI-консультанта к работе',
  },
];

const PROJECT_STATUS_LABELS: Record<string, string> = {
  draft: 'Черновик',
  created: 'Можно начинать',
  queued: 'Ожидает запуска',
  generating: 'Создаётся сейчас',
  running: 'Создаётся сейчас',
  free_result_ready: 'Готов к просмотру',
  completed: 'Готов к работе',
  published: 'Опубликован на сайте',
  failed: 'Нужен повторный запуск',
  cancelled: 'Создание остановлено',
};

const EVENT_ACTIVITY: Record<string, string> = {
  'run.created': 'Готовим проект к запуску',
  'run.failed': 'Создание остановлено — можно повторить запуск',
  'run.cancelled': 'Создание остановлено по вашему запросу',
  'reference.started': 'Изучаем структуру и содержание сайта',
  'reference.completed': 'Сайт изучен, переходим к виджету',
  'direction.judged': 'Выбрали подходящий образ виджета',
  'refinement.started': 'Начинаем доработку выбранной версии',
  'visual_audit.started': 'Проверяем виджет на разных экранах',
  'visual_audit.completed': 'Визуальная проверка завершена',
  'visual_audit.passed': 'Виджет прошёл визуальную проверку',
  'visual_repair.started': 'Исправляем найденные визуальные детали',
  'visual_repair.completed': 'Визуальные детали исправлены',
  'artifact.validated': 'Проверяем работу готового виджета',
  'artifact.committed': 'Сохраняем готовую версию',
  'run.completed': 'Виджет готов к просмотру',
};

const SAFE_PERSONA_ACTIVITY = /^Сотрудник\s+[А-ЯЁ][А-Яа-яЁё -]{0,48}\s+выбран[.!]?$/;

export function projectStatusLabel(status: string) {
  return PROJECT_STATUS_LABELS[status] ?? 'Состояние уточняется';
}

export function runStatusPresentation(status: BuilderRunStatus | null) {
  if (status === 'completed') {
    return {
      title: 'Виджет готов',
      detail: 'Можно проверить результат и внести изменения.',
    };
  }
  if (status === 'failed') {
    return {
      title: 'Нужен повторный запуск',
      detail: 'Сохранённые данные и доступный черновик не потеряны.',
    };
  }
  if (status === 'cancelled') {
    return {
      title: 'Создание остановлено',
      detail: 'Работу можно запустить снова.',
    };
  }
  if (status === 'running') {
    return {
      title: 'Создаём ваш виджет',
      detail: 'Можно оставить страницу открытой или вернуться позже.',
    };
  }
  if (status === 'created') {
    return {
      title: 'Можно начинать',
      detail: 'Запустите создание, когда будете готовы.',
    };
  }
  if (status === 'queued') {
    return {
      title: 'Готовим запуск',
      detail: 'Kaigo начнёт работу автоматически.',
    };
  }
  return {
    title: 'Проект готов к запуску',
    detail: 'Проверьте сайт и пожелание перед началом.',
  };
}

export function safeActivityForEvent(event: {
  type: string;
  stage: BuilderStage | null;
  message?: string | null;
}) {
  if (event.stage === 'persona' && event.message && SAFE_PERSONA_ACTIVITY.test(event.message.trim())) {
    return event.message.trim();
  }
  return EVENT_ACTIVITY[event.type]
    ?? STUDIO_STAGES.find(({ id }) => id === event.stage)?.activity
    ?? 'Продолжаем создавать ваш виджет';
}
