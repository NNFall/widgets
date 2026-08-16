export const CONTACT_CONFIG = {
  operatorStatus: 'самозанятый, плательщик НПД',
  consentDocumentPath: '/personal-data-consent/',
  consentDocumentVersion: 'feedback-v2',
} as const;

export const feedbackTopics = [
  { id: 'question', label: 'Вопрос' },
  { id: 'bug', label: 'Ошибка' },
  { id: 'improvement', label: 'Идея по улучшению' },
  { id: 'cooperation', label: 'Сотрудничество' },
] as const;

export type FeedbackTopicId = (typeof feedbackTopics)[number]['id'];

export function isFeedbackReady(message: string): boolean {
  return message.trim().length > 0;
}
