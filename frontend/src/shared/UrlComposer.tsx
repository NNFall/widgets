import { LinkSimple } from '@phosphor-icons/react';
import { useId, useState, type FormEvent } from 'react';

const URL_ERROR = 'Введите полный адрес сайта с http:// или https://';

function isValidWebsiteUrl(value: string) {
  try {
    const url = new URL(value);
    return (url.protocol === 'https:' || url.protocol === 'http:') && Boolean(url.hostname);
  } catch {
    return false;
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
  const [value, setValue] = useState('');
  const [error, setError] = useState('');

  const handleSubmit = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const normalizedValue = value.trim();

    if (!isValidWebsiteUrl(normalizedValue)) {
      setError(URL_ERROR);
      return;
    }

    setError('');
    const destination = `/studio?url=${encodeURIComponent(normalizedValue)}`;
    window.history.pushState({}, '', destination);
    window.dispatchEvent(new PopStateEvent('popstate'));
  };

  return (
    <form className="url-composer" onSubmit={handleSubmit} noValidate>
      <label className="sr-only" htmlFor={inputId}>
        {ariaLabel}
      </label>
      <div className={`url-composer__control${error ? ' url-composer__control--error' : ''}`}>
        <LinkSimple size={25} weight="regular" aria-hidden="true" />
        <input
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
        <button type="submit" aria-label={submitAriaLabel}>Создать AI-виджет</button>
      </div>
      <p className="url-composer__error" id={errorId} role={error ? 'alert' : undefined}>
        {error}
      </p>
    </form>
  );
}
