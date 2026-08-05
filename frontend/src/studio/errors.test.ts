import { describe, expect, it } from 'vitest';

import { STUDIO_ERROR_MESSAGES } from './errors';

describe('Studio provider errors', () => {
  it.each([
    ['provider_unavailable', 'Сервис генерации сейчас недоступен. Запуск можно повторить позже.'],
    ['model_unavailable', 'Выбранная модель сейчас недоступна.'],
    ['quota_exceeded', 'Лимит сервиса генерации временно исчерпан. Попробуйте позже.'],
    ['generation_credits_unavailable', 'Лимит доработок на тарифе исчерпан.'],
    ['route_exhausted', 'Ни один доступный сервис генерации не смог завершить запрос. Попробуйте позже.'],
  ])('uses a provider-neutral message for %s', (code, expected) => {
    const message = STUDIO_ERROR_MESSAGES[code];

    expect(message).toBe(expected);
    expect(message).not.toMatch(/gemini|agentrouter|qwen|glm|gpt/i);
  });
});
