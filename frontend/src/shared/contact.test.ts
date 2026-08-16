import { describe, expect, it } from 'vitest';

import { CONTACT_CONFIG, feedbackTopics, isFeedbackReady } from './contact';

describe('shared feedback contact contract', () => {
  it('keeps only the operator and consent metadata', () => {
    expect('supportEmail' in CONTACT_CONFIG).toBe(false);
    expect('subjectPrefix' in CONTACT_CONFIG).toBe(false);
    expect(CONTACT_CONFIG.operatorStatus).toBe('самозанятый, плательщик НПД');
    expect(CONTACT_CONFIG.consentDocumentPath).toBe('/personal-data-consent/');
    expect(CONTACT_CONFIG.consentDocumentVersion).toBe('feedback-v2');
    expect(feedbackTopics.map(({ id }) => id)).toEqual([
      'question',
      'bug',
      'improvement',
      'cooperation',
    ]);
  });

  it('enables feedback whenever the message contains non-whitespace text', () => {
    expect(isFeedbackReady('')).toBe(false);
    expect(isFeedbackReady('   ')).toBe(false);
    expect(isFeedbackReady('\n\t')).toBe(false);
    expect(isFeedbackReady(' Идея ')).toBe(true);
  });
});
