import type { BuilderEvent } from './types';

export const STUDIO_ERROR_MESSAGES: Readonly<Record<string, string>> = {
  missing_api_key: 'Сервис генерации пока не настроен.',
  provider_unavailable: 'Gemini сейчас недоступен. Запуск можно повторить позже.',
  model_unavailable: 'Выбранная модель Gemini сейчас недоступна.',
  quota_exceeded: 'Лимит Gemini временно исчерпан. Попробуйте позже.',
  generation_timeout: 'Генерация заняла слишком много времени и была остановлена.',
  invalid_artifact: 'Полученную версию не удалось безопасно открыть.',
  visual_quality_failed: 'Финальная визуальная проверка не пройдена.',
  visual_review_inconclusive: 'Визуальную проверку не удалось завершить уверенно.',
  reference_capture_failed: 'Не удалось снять главную страницу сайта.',
  reference_capture_incomplete: 'Не удалось полностью снять главную страницу сайта.',
  reference_analysis_failed: 'Не удалось закончить анализ исходного сайта.',
  run_cancelled: 'Генерация отменена.',
  internal_error: 'Во время генерации произошла внутренняя ошибка.',
};

export const GENERIC_EVENT_ERROR = 'Этап завершился с ошибкой.';

export function russianErrorMessage(code: string | null, fallback: string) {
  return (code && STUDIO_ERROR_MESSAGES[code]) || fallback;
}

export function safeEventMessage(event: BuilderEvent) {
  const isError = event.status === 'failed'
    || event.status === 'cancelled'
    || event.type.endsWith('.failed');
  if (!isError) return event.message;
  if (event.status === 'cancelled') {
    return russianErrorMessage(event.error_code, 'Запуск отменён.');
  }
  return russianErrorMessage(event.error_code, GENERIC_EVENT_ERROR);
}
