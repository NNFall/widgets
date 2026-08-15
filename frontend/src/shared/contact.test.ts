import { describe, expect, it } from 'vitest';

import {
  CONTACT_CONFIG,
  composeFeedbackMail,
  feedbackTopics,
  isFeedbackReady,
  supportMailtoHref,
} from './contact';

describe('shared contact contract', () => {
  it('keeps provisional contact values explicit and the topic set finite', () => {
    expect(CONTACT_CONFIG.supportEmail).toBe('support@kaigo.space');
    expect(supportMailtoHref()).toBe('mailto:support@kaigo.space');
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
    expect(mail.href).toMatch(/^mailto:support@kaigo\.space\?/);
    expect(mail.href).not.toContain('%40');
    expect(mail.href).toContain('%0D%0A');
    expect(mail.subject).toContain('Ошибка');
    expect(mail.body).toContain('Не работает «Ответ» & /');
    expect(mail.body).toContain('\r\n');
    expect(parsed.searchParams.get('body')).toContain('Контекст Studio: preview-project-1');
    expect(parsed.searchParams.get('subject')).toBe(mail.subject);
    expect(parsed.searchParams.get('body')).toBe(mail.body);
    expect(mail.href).toContain(encodeURIComponent('Не работает «Ответ» & /'));
  });

  it('normalizes mixed user line endings to CRLF in the encoded mail body', () => {
    const mail = composeFeedbackMail({
      topic: 'question',
      message: 'message one\r\nmessage two\rmessage three\nmessage four',
      studioContext: 'studio one\nstudio two\r\nstudio three\rstudio four',
    });
    const encodedBody = mail.href.split('&body=')[1] ?? '';
    const decodedBody = decodeURIComponent(encodedBody);

    expect(encodedBody).toContain('%0D%0A');
    expect(encodedBody.replaceAll('%0D%0A', '')).not.toContain('%0A');
    expect(decodedBody).not.toMatch(/(^|[^\r])\n/);
    expect(decodedBody).not.toContain('\r\r\n');
    expect(decodedBody).toContain('message one\r\nmessage two\r\nmessage three\r\nmessage four');
    expect(decodedBody).toContain('Контекст Studio: studio one\r\nstudio two\r\nstudio three\r\nstudio four');
  });

  it('requires both a non-empty message and separate consent', () => {
    expect(isFeedbackReady('', false)).toBe(false);
    expect(isFeedbackReady('   ', true)).toBe(false);
    expect(isFeedbackReady('Вопрос', false)).toBe(false);
    expect(isFeedbackReady(' Вопрос ', true)).toBe(true);
  });
});
