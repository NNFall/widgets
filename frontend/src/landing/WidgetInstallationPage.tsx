import { useEffect, type ReactNode } from 'react';

import { KaigoLogo } from '../shared/KaigoLogo';

const INSTALL_SNIPPET = '<script src="https://kaigo.space/embed/YOUR_WIDGET_KEY.js" async></script>';

type GuideArticleProps = {
  id: string;
  number: string;
  title: string;
  summary: string;
  children: ReactNode;
  officialLink?: {
    href: string;
    label: string;
  };
};

function GuideArticle({
  id,
  number,
  title,
  summary,
  children,
  officialLink,
}: GuideArticleProps) {
  const titleId = `${id}-title`;

  return (
    <article className="installation-guide" id={id} aria-labelledby={titleId}>
      <div className="installation-guide__number" aria-hidden="true">{number}</div>
      <div className="installation-guide__content">
        <header>
          <h2 id={titleId}>{title}</h2>
          <p>{summary}</p>
        </header>
        {children}
        {officialLink && (
          <a
            className="installation-guide__official-link"
            href={officialLink.href}
            target="_blank"
            rel="noopener noreferrer"
          >
            {officialLink.label}
            <span aria-hidden="true">↗</span>
          </a>
        )}
      </div>
    </article>
  );
}

export function WidgetInstallationPage() {
  useEffect(() => {
    const previousTitle = document.title;
    document.title = 'Установка виджета Kaigo';
    return () => {
      document.title = previousTitle;
    };
  }, []);

  return (
    <main className="installation-page">
      <header className="installation-page__header">
        <a className="installation-page__brand" href="/" aria-label="Kaigo, главная">
          <KaigoLogo tone="coral" />
        </a>
        <a className="installation-page__studio-link" href="/studio">
          <span aria-hidden="true">←</span>
          Вернуться в студию
        </a>
      </header>

      <section className="installation-page__intro" aria-labelledby="installation-page-title">
        <div className="installation-page__intro-copy">
          <span className="installation-page__kicker">Установка после публикации</span>
          <h1 id="installation-page-title">Как установить виджет Kaigo</h1>
          <p>
            Скопируйте персональный код в окне публикации Studio, вставьте его на сайт один раз
            и опубликуйте изменения. Программировать ничего не нужно.
          </p>
        </div>

        <div className="installation-page__snippet" aria-label="Пример кода установки">
          <div>
            <span>Безопасный пример</span>
            <strong>Ваш код будет в Studio</strong>
          </div>
          <pre><code>{INSTALL_SNIPPET}</code></pre>
          <p>
            Не вставляйте этот шаблон: <code>YOUR_WIDGET_KEY</code> показывает только место
            персонального ключа.
          </p>
        </div>
      </section>

      <div className="installation-page__body">
        <aside className="installation-page__aside">
          <div>
            <span>Выберите платформу</span>
            <nav aria-label="Разделы инструкции">
              <a href="#install-html">Обычный HTML-сайт</a>
              <a href="#install-tilda">Tilda</a>
              <a href="#install-wordpress">WordPress</a>
              <a href="#install-webflow">Webflow</a>
              <a href="#install-wix">Wix и другие</a>
            </nav>
          </div>
          <div className="installation-page__security-note">
            <strong>Почему важен адрес сайта</strong>
            <p>
              Kaigo разрешает виджет только на точном HTTPS-адресе сайта проекта. Если рабочий
              домен отличается, откройте проект в Studio и нажмите «Связаться с Kaigo».
            </p>
          </div>
        </aside>

        <section className="installation-page__guides" aria-label="Инструкции для платформ">
          <GuideArticle
            id="install-html"
            number="01"
            title="Обычный HTML-сайт"
            summary="Подходит, если у вас есть доступ к шаблону страницы или общему layout сайта."
          >
            <ol>
              <li>Вставьте скопированный в Studio <code>&lt;script&gt;</code> перед закрывающим тегом <code>&lt;/body&gt;</code>.</li>
              <li>Сохраните изменения и опубликуйте или разверните сайт.</li>
              <li>Откройте опубликованный сайт и убедитесь, что кнопка виджета появилась в нижнем углу.</li>
            </ol>
          </GuideArticle>

          <GuideArticle
            id="install-tilda"
            number="02"
            title="Tilda"
            summary="Код можно поставить на одну страницу или сразу на весь сайт."
            officialLink={{
              href: 'https://help-ru.tilda.cc/html',
              label: 'Официальная инструкция Tilda',
            }}
          >
            <ol>
              <li>Для одной страницы добавьте блок T123 «HTML-код» и вставьте код из Studio.</li>
              <li>Для всего сайта или выбранных страниц используйте области HTML в Настройках сайта или Настройках страницы, описанные в справке Tilda.</li>
              <li>Сохраните изменения и переопубликуйте страницу или весь сайт.</li>
              <li>Откройте опубликованный сайт и проверьте, что кнопка виджета появилась.</li>
            </ol>
          </GuideArticle>

          <GuideArticle
            id="install-wordpress"
            number="03"
            title="WordPress"
            summary="Способ зависит от WordPress.com или самостоятельно размещённого сайта."
            officialLink={{
              href: 'https://wordpress.com/support/wordpress-editor/blocks/custom-html-block/',
              label: 'Официальная инструкция WordPress',
            }}
          >
            <ol>
              <li>На WordPress.com добавьте блок Custom HTML на нужную страницу или в общий шаблон. Для установки на всём сайте нужен общий шаблон либо другой общий механизм, доступный вашему тарифу. Теги <code>script</code> работают на платном плане с функциями хостинга и могут быть удалены на неподдерживаемом плане.</li>
              <li>На самостоятельно размещённом WordPress используйте доверенный и проверенный плагин вставки кода или механизм темы, который добавляет <code>script</code> на всём сайте перед <code>&lt;/body&gt;</code>.</li>
              <li>Сохраните, обновите или опубликуйте изменения.</li>
              <li>Откройте опубликованный сайт и убедитесь, что кнопка виджета появилась.</li>
            </ol>
          </GuideArticle>

          <GuideArticle
            id="install-webflow"
            number="04"
            title="Webflow"
            summary="Добавьте код в настройки страницы или всего сайта."
            officialLink={{
              href: 'https://help.webflow.com/hc/en-us/articles/33961357265299-Custom-code-in-head-and-body-tags',
              label: 'Официальная инструкция Webflow',
            }}
          >
            <ol>
              <li>Убедитесь, что у проекта есть подходящий платный Site или Workspace план с доступом к Custom code.</li>
              <li>Откройте Page settings или Site settings, затем Custom code.</li>
              <li>Вставьте код в поле <code>Before &lt;/body&gt;</code> и сохраните.</li>
              <li>Опубликуйте сайт на нужном домене.</li>
              <li>Откройте опубликованный сайт и проверьте, что кнопка виджета появилась.</li>
            </ol>
          </GuideArticle>

          <GuideArticle
            id="install-wix"
            number="05"
            title="Wix и другие конструкторы"
            summary="Используйте системный раздел для пользовательского кода, а не видимый текстовый блок."
            officialLink={{
              href: 'https://support.wix.com/en/article/wix-editor-embedding-custom-code-on-your-site',
              label: 'Официальная инструкция Wix',
            }}
          >
            <ol>
              <li>Сначала опубликуйте сайт Wix и подключите домен, затем откройте Dashboard → Settings → Custom Code → Add Custom Code.</li>
              <li>Выберите место Body end и применение на все страницы.</li>
              <li>Примените настройки и переопубликуйте сайт.</li>
              <li>В других конструкторах найдите Custom Code или HTML Embed, добавьте код в конец body и включите его на всех страницах.</li>
              <li>Откройте опубликованный сайт и убедитесь, что кнопка виджета появилась.</li>
            </ol>
          </GuideArticle>
        </section>
      </div>

      <footer className="installation-page__footer">
        <p>Персональный код уже ждёт в публикации проекта.</p>
        <a href="/studio">Открыть Studio</a>
      </footer>
    </main>
  );
}
