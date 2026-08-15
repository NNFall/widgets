export const CONTACT_CONFIG = {
  supportEmail: 'support@kaigo.space',
  telegramUrl: null,
  consentDocumentPath: '/personal-data-consent/',
  consentDocumentVersion: 'preview-2026-08-15',
  subjectPrefix: 'Kaigo, обратная связь',
} as const;

export const feedbackTopics = [
  { id: 'question', label: 'Вопрос' },
  { id: 'bug', label: 'Ошибка' },
  { id: 'improvement', label: 'Идея по улучшению' },
  { id: 'cooperation', label: 'Сотрудничество' },
] as const;

export type FeedbackTopicId = (typeof feedbackTopics)[number]['id'];

export type FeedbackMailInput = {
  topic: FeedbackTopicId;
  message: string;
  page?: string;
  studioContext?: string;
};

export type FeedbackMail = {
  href: string;
  subject: string;
  body: string;
};

function topicLabel(topic: FeedbackTopicId): string {
  return feedbackTopics.find((candidate) => candidate.id === topic)?.label ?? feedbackTopics[0].label;
}

export function isFeedbackReady(message: string, consentGiven: boolean): boolean {
  return consentGiven && message.trim().length > 0;
}

export function composeFeedbackMail({ topic, message, page = '/', studioContext }: FeedbackMailInput): FeedbackMail {
  const subject = `${CONTACT_CONFIG.subjectPrefix}: ${topicLabel(topic)}`;
  const bodyLines = [
    `Тема: ${topicLabel(topic)}`,
    `Страница: ${page || '/'}`,
    '',
    'Сообщение:',
    message.trim(),
    '',
    `Согласие с документом: ${CONTACT_CONFIG.consentDocumentVersion}`,
  ];

  if (studioContext?.trim()) {
    bodyLines.push(`Контекст Studio: ${studioContext.trim()}`);
  }

  const body = bodyLines.join('\n');
  const query = `subject=${encodeURIComponent(subject)}&body=${encodeURIComponent(body)}`;
  return {
    href: `mailto:${encodeURIComponent(CONTACT_CONFIG.supportEmail)}?${query}`,
    subject,
    body,
  };
}
