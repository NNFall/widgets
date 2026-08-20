import { Copy } from '@phosphor-icons/react';

import type { PublicationRelease } from './api';
import type { BillingSubscription } from './types';

export type PublicationCopyTarget = 'code' | 'link' | null;

export interface PublicationInstallViewProps {
  publication: PublicationRelease;
  versionOrdinal?: number;
  subscription: BillingSubscription | null;
  copiedTarget: PublicationCopyTarget;
  copyError: boolean;
  onCopyCode: () => void;
  onCopyLink: () => void;
}

function subscriptionEndLabel(value: string | undefined) {
  if (!value) return 'конца оплаченного периода';
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return 'конца оплаченного периода';
  return new Intl.DateTimeFormat('ru-RU', {
    day: 'numeric',
    month: 'long',
    year: 'numeric',
  }).format(date).replace(/\.$/, '');
}

function rubles(amountMinor: number) {
  return `${new Intl.NumberFormat('ru-RU').format(amountMinor / 100)} ₽`;
}

function subscriptionStatus(subscription: BillingSubscription | null) {
  if (!subscription) return 'Доступ к публикации активен.';
  if (subscription.next_charge) {
    return `Следующее списание — ${rubles(subscription.next_charge.amount_minor)} ${subscriptionEndLabel(subscription.next_charge.at)}. Автопродление включено.`;
  }
  if (subscription.auto_renew) {
    return `Следующее продление — ${subscriptionEndLabel(subscription.next_renewal_at ?? subscription.current_period_end)}. Автопродление включено.`;
  }
  return `Тариф действует до ${subscriptionEndLabel(subscription.current_period_end)}. Автопродление выключено.`;
}

export function PublicationInstallView({
  publication,
  versionOrdinal,
  subscription,
  copiedTarget,
  copyError,
  onCopyCode,
  onCopyLink,
}: PublicationInstallViewProps) {
  const embedSnippet = `<script src="${publication.embed_url}" async></script>`;
  const publishedStatus = versionOrdinal
    ? `Версия ${versionOrdinal} опубликована и доступна на разрешённых сайтах.`
    : 'Виджет опубликован и доступен на разрешённых сайтах.';

  return (
    <section className="studio-upgrade__install-view" aria-labelledby="publication-install-title">
      <header>
        <h2 id="publication-install-title">Виджет опубликован</h2>
        <p>Скопируйте код установки или постоянную ссылку ниже.</p>
      </header>

      <div className="studio-upgrade__publication">
        <p className="studio-upgrade__renewal-status">{subscriptionStatus(subscription)}</p>
        {typeof subscription?.generation_tokens_remaining === 'number' && (
          <p className="studio-upgrade__renewal-status">
            {subscription.generation_tokens_remaining > 0
              ? 'Доработки доступны в рамках тарифа.'
              : 'Лимит доработок на тарифе исчерпан.'}
          </p>
        )}

        <p role="status">{publishedStatus}</p>

        <section className="studio-upgrade__handoff" aria-labelledby="publication-handoff-title">
          <span>Остался один шаг</span>
          <h3 id="publication-handoff-title">Установите виджет на сайт</h3>
          <p>
            Скопируйте код установки или передайте его человеку, который управляет
            сайтом. Последующие обновления будут приходить по тому же адресу.
          </p>

          <div className="studio-upgrade__handoff-actions">
            <button type="button" onClick={onCopyCode}>
              <Copy aria-hidden size={18} weight="bold" /> Скопировать код установки
            </button>
            <button type="button" onClick={onCopyLink}>
              <Copy aria-hidden size={18} weight="bold" /> Скопировать ссылку загрузчика
            </button>
          </div>

          <a className="studio-upgrade__install-guide" href="/install">
            Открыть инструкцию по установке
          </a>

          {copiedTarget === 'code' && (
            <p
              className="studio-upgrade__copy-status"
              role="status"
              aria-label="Результат копирования"
            >
              Код скопирован. Его можно отправить разработчику.
            </p>
          )}
          {copiedTarget === 'link' && (
            <p
              className="studio-upgrade__copy-status"
              role="status"
              aria-label="Результат копирования"
            >
              Ссылка загрузчика скопирована.
            </p>
          )}
          {copyError && (
            <div className="studio-upgrade__manual-copy">
              <p
                className="studio-upgrade__copy-status studio-upgrade__copy-status--error"
                role="alert"
              >
                Не получилось скопировать автоматически. Скопируйте вручную оба значения ниже.
              </p>
              <div>
                <label htmlFor="publication-install-code">Код установки</label>
                <textarea
                  id="publication-install-code"
                  readOnly
                  rows={3}
                  value={embedSnippet}
                  aria-label="Код установки для ручного копирования"
                  onFocus={(event) => event.currentTarget.select()}
                  onClick={(event) => event.currentTarget.select()}
                />
              </div>
              <div>
                <label htmlFor="publication-loader-url">Ссылка загрузчика</label>
                <textarea
                  id="publication-loader-url"
                  readOnly
                  rows={2}
                  value={publication.embed_url}
                  aria-label="Ссылка загрузчика для ручного копирования"
                  onFocus={(event) => event.currentTarget.select()}
                  onClick={(event) => event.currentTarget.select()}
                />
              </div>
            </div>
          )}
        </section>

        <details className="studio-upgrade__developer">
          <summary>Код для разработчика</summary>
          <div>
            <p>Передайте этот код человеку, который управляет сайтом.</p>
            <code>{embedSnippet}</code>
            <dl>
              <div>
                <dt>Постоянный адрес подключения</dt>
                <dd>{publication.embed_url}</dd>
              </div>
              <div>
                <dt>Публикация</dt>
                <dd>{publication.publication_id}</dd>
              </div>
              <div>
                <dt>Релиз</dt>
                <dd>{publication.release_id}</dd>
              </div>
              <div>
                <dt>Сборка</dt>
                <dd>{publication.artifact_id}</dd>
              </div>
              <div>
                <dt>Версия файла</dt>
                <dd>{publication.revision}</dd>
              </div>
              <div>
                <dt>Runtime URL</dt>
                <dd>{publication.runtime_url}</dd>
              </div>
              <div>
                <dt>Контрольная сумма</dt>
                <dd>{publication.checksum}</dd>
              </div>
            </dl>
          </div>
        </details>
      </div>
    </section>
  );
}
