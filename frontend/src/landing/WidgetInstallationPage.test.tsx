import { cleanup, render, screen, within } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { WidgetInstallationPage } from './WidgetInstallationPage';

const installSnippet = '<script src="https://kaigo.space/embed/YOUR_WIDGET_KEY.js" async></script>';
const platformNames = [
  'Обычный HTML-сайт',
  'Tilda',
  'WordPress',
  'Webflow',
  'Wix и другие конструкторы',
] as const;

function articleNamed(name: (typeof platformNames)[number]) {
  return screen.getByRole('article', { name });
}

function storageContents(storage: Storage) {
  return Array.from({ length: storage.length }, (_, index) => {
    const key = storage.key(index);
    return key === null ? '' : `${key}=${storage.getItem(key) ?? ''}`;
  }).join('\n');
}

function expectPublishedWidgetAppears(article: HTMLElement, action: RegExp) {
  expect(article).toHaveTextContent(action);
  expect(article).toHaveTextContent(/проверьте|убедитесь|откройте.*сайт/i);
  expect(article).toHaveTextContent(
    /(?:кнопк.*виджет|лаунчер).*появ|появ.*(?:кнопк.*виджет|лаунчер)/i,
  );
}

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
  window.localStorage.clear();
  window.sessionStorage.clear();
  window.history.replaceState({}, '', '/');
});

describe('WidgetInstallationPage', () => {
  it('shows every supported installation path with a safe static sample', () => {
    const queryKey = 'REAL_LOOKING_PUBLIC_WIDGET_KEY_1234567890';
    window.localStorage.clear();
    window.sessionStorage.clear();
    window.history.replaceState({}, '', `/install?key=${queryKey}`);
    const fetchSpy = vi.fn(async () => new Response('{}'));
    const localStorageWriteSpy = vi.spyOn(window.localStorage, 'setItem');
    const sessionStorageWriteSpy = vi.spyOn(window.sessionStorage, 'setItem');
    vi.stubGlobal('fetch', fetchSpy);

    render(<WidgetInstallationPage />);

    expect(screen.getByRole('heading', { name: 'Как установить виджет Kaigo' })).toBeVisible();
    const platformArticles = platformNames.map((name) => {
      const article = articleNamed(name);
      expect(within(article).getByRole('heading', { name })).toBeVisible();
      return article;
    });
    expect(new Set(platformArticles).size).toBe(platformNames.length);

    const code = screen.getByText(installSnippet, { selector: 'code' });
    expect(code).toBeVisible();
    expect(code.closest('pre')).not.toBeNull();
    expect(document.body).not.toHaveTextContent(queryKey);
    expect(document.body.innerHTML).not.toContain(queryKey);
    expect(document.body.innerHTML).not.toMatch(
      /https:\/\/kaigo\.space\/embed\/(?!YOUR_WIDGET_KEY)[A-Za-z0-9_-]{20,}\.js/,
    );
    expect(fetchSpy).not.toHaveBeenCalled();
    expect(localStorageWriteSpy).not.toHaveBeenCalled();
    expect(sessionStorageWriteSpy).not.toHaveBeenCalled();
    expect(storageContents(window.localStorage)).not.toContain(queryKey);
    expect(storageContents(window.sessionStorage)).not.toContain(queryKey);
  });

  it('explains the concrete placement and publication steps for each platform', () => {
    render(<WidgetInstallationPage />);

    const htmlArticle = articleNamed('Обычный HTML-сайт');
    expect(htmlArticle).toHaveTextContent('</body>');
    expectPublishedWidgetAppears(htmlArticle, /опубликуйте|разверните/i);

    const tildaArticle = articleNamed('Tilda');
    expect(tildaArticle).toHaveTextContent('T123');
    expect(tildaArticle).toHaveTextContent(/HTML/i);
    expect(tildaArticle).toHaveTextContent(/настройк/i);
    expect(tildaArticle).toHaveTextContent(/сайта/i);
    expect(tildaArticle).toHaveTextContent(/страниц/i);
    expectPublishedWidgetAppears(tildaArticle, /переопубликуйте/i);

    const wordpressArticle = articleNamed('WordPress');
    expect(wordpressArticle).toHaveTextContent('Custom HTML');
    expect(wordpressArticle).toHaveTextContent(/WordPress\.com/i);
    expect(wordpressArticle).toHaveTextContent(/платн/i);
    expect(wordpressArticle).toHaveTextContent(/плагин/i);
    expect(wordpressArticle).toHaveTextContent(/самостоятельн|собственн.*хостинг|self-hosted/i);
    expect(wordpressArticle).toHaveTextContent(/доверенн|проверенн/i);
    expect(wordpressArticle).toHaveTextContent(/всего сайта|на всём сайте/i);
    expect(wordpressArticle).toHaveTextContent(/script/i);
    expectPublishedWidgetAppears(wordpressArticle, /сохраните|обновите|опубликуйте/i);

    const webflowArticle = articleNamed('Webflow');
    expect(webflowArticle).toHaveTextContent(/Before\s*<\/body>/i);
    expectPublishedWidgetAppears(webflowArticle, /опубликуйте/i);

    const wixArticle = articleNamed('Wix и другие конструкторы');
    expect(wixArticle).toHaveTextContent('Dashboard');
    expect(wixArticle).toHaveTextContent('Settings');
    expect(wixArticle).toHaveTextContent('Custom Code');
    expect(wixArticle).toHaveTextContent(
      /других конструкторах.*(?:Custom Code|HTML Embed)|(?:Custom Code|HTML Embed).*других конструкторах/i,
    );
    expect(wixArticle).toHaveTextContent(/Body end/i);
    expect(wixArticle).toHaveTextContent(/все страницы/i);
    expectPublishedWidgetAppears(wixArticle, /примените.*опубликуйте|опубликуйте.*примените/i);
  });

  it('links to official platform help in a separate safe tab', () => {
    render(<WidgetInstallationPage />);

    const officialLinks = [
      {
        section: 'Tilda',
        name: 'Официальная инструкция Tilda',
        href: 'https://help-ru.tilda.cc/html',
      },
      {
        section: 'WordPress',
        name: 'Официальная инструкция WordPress',
        href: 'https://wordpress.com/support/wordpress-editor/blocks/custom-html-block/',
      },
      {
        section: 'Webflow',
        name: 'Официальная инструкция Webflow',
        href: 'https://help.webflow.com/hc/en-us/articles/33961357265299-Custom-code-in-head-and-body-tags',
      },
      {
        section: 'Wix и другие конструкторы',
        name: 'Официальная инструкция Wix',
        href: 'https://support.wix.com/en/article/wix-editor-embedding-custom-code-on-your-site',
      },
    ] as const;

    for (const { section, name, href } of officialLinks) {
      const link = within(articleNamed(section)).getByRole('link', { name });
      expect(link).toHaveAttribute('href', href);
      expect(link).toHaveAttribute('target', '_blank');
      const relTokens = new Set((link.getAttribute('rel') ?? '').split(/\s+/).filter(Boolean));
      expect(relTokens.has('noopener')).toBe(true);
      expect(relTokens.has('noreferrer')).toBe(true);
    }
  });

  it('offers an accessible route back to Studio', () => {
    render(<WidgetInstallationPage />);

    expect(screen.getByRole('link', { name: 'Вернуться в студию' })).toHaveAttribute(
      'href',
      '/studio',
    );
  });
});
