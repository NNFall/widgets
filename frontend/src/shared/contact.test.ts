import { describe, expect, it } from 'vitest';

import {
  CONTACT_CONFIG,
  composeFeedbackMail,
  feedbackTopics,
  isFeedbackReady,
} from './contact';

describe('shared contact contract', () => {
  it('keeps provisional contact values explicit and the topic set finite', () => {
    expect(CONTACT_CONFIG.supportEmail).toBe('support@kaigo.space');
    expect(CONTACT_CONFIG.telegramUrl).toBeNull();
    expect(CONTACT_CONFIG.consentDocumentPath).toBe('/personal-data-consent/');
    expect(CONTACT_CONFIG.consentDocumentVersion).toBeTruthy();
    expect(feedbackTopics.map((topic) => topic.id)).toEqual([
      'question',
      'bug',
      'improvement',
      'cooperation',
    ]);
  });

  it('composes a URL-safe mailto subject and body without submitting anything', () => {
    const mail = composeFeedbackMail({
      topic: 'bug',
      message: 'Не работает «Ответ» & /',
      page: '/contact',
      studioContext: 'preview-project-1',
    });
    const parsed = new URL(mail.href);

    expect(parsed.protocol).toBe('mailto:');
    expect(decodeURIComponent(parsed.pathname)).toBe(CONTACT_CONFIG.supportEmail);
    expect(mail.subject).toContain('Ошибка');
    expect(mail.body).toContain('Не работает «Ответ» & /');
    expect(parsed.searchParams.get('body')).toContain('Контекст Studio: preview-project-1');
    expect(parsed.searchParams.get('subject')).toBe(mail.subject);
    expect(parsed.searchParams.get('body')).toBe(mail.body);
    expect(mail.href).toContain(encodeURIComponent('Не работает «Ответ» & /'));
  });

  it('requires both a non-empty message and separate consent', () => {
    expect(isFeedbackReady('', false)).toBe(false);
    expect(isFeedbackReady('   ', true)).toBe(false);
    expect(isFeedbackReady('Вопрос', false)).toBe(false);
    expect(isFeedbackReady(' Вопрос ', true)).toBe(true);
  });
});
