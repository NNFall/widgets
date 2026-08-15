import { useMemo, useState } from 'react';

import {
  composeFeedbackMail,
  CONTACT_CONFIG,
  feedbackTopics,
  isFeedbackReady,
  type FeedbackTopicId,
} from './contact';
import { marketingHref } from './marketing';

type FeedbackComposerProps = {
  page?: string;
  studioContext?: string;
  className?: string;
};

export function FeedbackComposer({ page = '/contact', studioContext, className = '' }: FeedbackComposerProps) {
  const [topic, setTopic] = useState<FeedbackTopicId>('question');
  const [message, setMessage] = useState('');
  const [consentGiven, setConsentGiven] = useState(false);
  const ready = isFeedbackReady(message, consentGiven);
  const mail = useMemo(
    () => composeFeedbackMail({ topic, message, page, studioContext }),
    [message, page, studioContext, topic],
  );

  return (
    <form className={`feedback-composer ${className}`.trim()} onSubmit={(event) => event.preventDefault()}>
      <fieldset className="feedback-composer__topics">
        <legend>О чём хотите написать?</legend>
        <div className="feedback-topic-grid">
          {feedbackTopics.map((feedbackTopic) => (
            <label className="feedback-topic" key={feedbackTopic.id}>
              <input
                type="radio"
                name="feedback-topic"
                value={feedbackTopic.id}
                checked={topic === feedbackTopic.id}
                onChange={() => setTopic(feedbackTopic.id)}
              />
              <span>{feedbackTopic.label}</span>
            </label>
          ))}
        </div>
      </fieldset>

      <label className="feedback-composer__message">
        <span>Сообщение</span>
        <textarea
          value={message}
          onChange={(event) => setMessage(event.target.value)}
          placeholder="Например: хочу уточнить, как проверить виджет на сайте"
          rows={6}
        />
      </label>

      <label className="feedback-composer__consent">
        <input type="checkbox" checked={consentGiven} onChange={(event) => setConsentGiven(event.target.checked)} />
        <span>
          Я соглашаюсь на обработку сообщения в соответствии с{' '}
          <a href={marketingHref(CONTACT_CONFIG.consentDocumentPath)}>текстом согласия на обработку персональных данных</a>.
        </span>
      </label>

      <div className="feedback-composer__action-row">
        <a
          className={`primary-button feedback-composer__action${ready ? '' : ' is-disabled'}`}
          href={ready ? mail.href : undefined}
          aria-disabled={ready ? 'false' : 'true'}
          role="link"
          tabIndex={ready ? undefined : -1}
          onClick={(event) => {
            if (!ready) event.preventDefault();
          }}
        >
          Открыть письмо
        </a>
        <p className="feedback-composer__note">
          Откроется ваше почтовое приложение. Ничего не отправляется автоматически.
        </p>
      </div>

    </form>
  );
}
