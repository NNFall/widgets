import { LinkSimple } from '@phosphor-icons/react';
import { useEffect, useId, useRef, useState, type FormEvent } from 'react';

import { campaignFromSearch, studioHrefWithDraft } from './campaign';
import { ensureLandingJourney } from './journey';
import { LANDING_MOBILE_MEDIA_QUERY } from './mobileLayout';

const URL_ERROR = 'Введите публичный HTTPS-адрес без параметров и авторизации';
const PRIVATE_SUFFIXES = ['.internal', '.localhost', '.local', '.lan', '.home'];

export function canonicalWebsiteUrl(value: string): string | null {
  const raw = value.trim();
  if (
    raw.length > 2_048
    || raw.includes('?')
    || raw.includes('#')
    || raw.includes('\\')
  ) return null;
  try {
    const url = new URL(value);
    const hostname = url.hostname.toLowerCase().replace(/\.$/, '');
    if (
      url.protocol !== 'https:'
      || url.username
      || url.password
      || url.port
      || !hostname.includes('.')
      || hostname === 'localhost'
      || PRIVATE_SUFFIXES.some((suffix) => hostname.endsWith(suffix))
      || hostname.includes(':')
      || /^\d+(?:\.\d+){3}$/.test(hostname)
      || hostname.split('.').some((label) => !/^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$/.test(label))
    ) return null;
    url.hostname = hostname;
    return url.href;
  } catch {
    return null;
  }
}

type UrlComposerProps = {
  ariaLabel?: string;
  submitAriaLabel?: string;
};

export function UrlComposer({
  ariaLabel = 'Ссылка на действующий сайт',
  submitAriaLabel,
}: UrlComposerProps) {
  const inputId = useId();
  const errorId = useId();
  const briefId = useId();
  const inputRef = useRef<HTMLInputElement>(null);
  const errorRef = useRef<HTMLParagraphElement>(null);
  const shouldFocusInputRef = useRef(false);
  const [value, setValue] = useState('');
  const [brief, setBrief] = useState('');
  const [error, setError] = useState('');
  const [pending, setPending] = useState(false);

  useEffect(() => {
    if (!error || !shouldFocusInputRef.current) return;

    shouldFocusInputRef.current = false;
    inputRef.current?.focus({ preventScroll: true });

    const mediaQuery = typeof window !== 'undefined' && typeof window.matchMedia === 'function'
      ? window.matchMedia(LANDING_MOBILE_MEDIA_QUERY)
      : null;
    const isMobileViewport = Boolean(mediaQuery?.matches);
    const errorElement = errorRef.current;
    if (isMobileViewport && typeof errorElement?.scrollIntoView === 'function') {
      errorElement.scrollIntoView({ block: 'nearest', behavior: 'smooth' });
    }
  }, [error]);

  const handleSubmit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const normalizedValue = canonicalWebsiteUrl(value);

    if (!normalizedValue) {
      shouldFocusInputRef.current = true;
      inputRef.current?.focus({ preventScroll: true });
      setError(URL_ERROR);
      return;
    }

    setError('');
    setPending(true);
    try {
      await ensureLandingJourney();
      const campaign = campaignFromSearch();
      const response = await fetch('/api/drafts', {
        method: 'POST',
        credentials: 'include',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          url: normalizedValue,
          brief: brief.trim(),
          ...(Object.keys(campaign).length > 0 ? { campaign } : {}),
        }),
      });
      if (!response.ok) throw new Error(`draft:${response.status}`);
      const draft = await response.json() as { id?: unknown };
      if (typeof draft.id !== 'string' || !draft.id) throw new Error('draft:invalid');
      window.history.pushState({}, '', studioHrefWithDraft(draft.id));
      window.dispatchEvent(new PopStateEvent('popstate'));
    } catch {
      setError('Не удалось сохранить заявку. Попробуйте ещё раз.');
    } finally {
      setPending(false);
    }
  };

  return (
    <form className="url-composer" onSubmit={handleSubmit} noValidate>
      <label className="sr-only" htmlFor={inputId}>
        {ariaLabel}
      </label>
      <div className={`url-composer__control${error ? ' url-composer__control--error' : ''}`}>
        <LinkSimple size={25} weight="regular" aria-hidden="true" />
        <input
          ref={inputRef}
          id={inputId}
          type="url"
          inputMode="url"
          autoComplete="url"
          spellCheck="false"
          placeholder="Ссылка на действующий сайт"
          value={value}
          aria-invalid={Boolean(error)}
          aria-describedby={error ? errorId : undefined}
          onChange={(event) => {
            setValue(event.target.value);
            if (error) setError('');
          }}
        />
        <button type="submit" aria-label={submitAriaLabel} disabled={pending}>
          {pending ? 'Сохраняем…' : 'Получить бесплатную версию'}
        </button>
      </div>
      <p className="url-composer__error" id={errorId} role={error ? 'alert' : undefined} ref={errorRef}>
        {error}
      </p>
      <label className="sr-only" htmlFor={briefId}>
        Пожелание к AI-виджету
      </label>
      <textarea
        className="url-composer__brief"
        id={briefId}
        maxLength={4_000}
        placeholder="Необязательное пожелание к AI-виджету"
        value={brief}
        onChange={(event) => setBrief(event.target.value)}
      />
    </form>
  );
}
