import { CheckCircle, PaperPlaneTilt, X } from '@phosphor-icons/react';
import {
  FormEvent,
  KeyboardEvent,
  useEffect,
  useLayoutEffect,
  useRef,
  useState,
} from 'react';

interface SupportDialogProps {
  open: boolean;
  founder: boolean;
  busy: boolean;
  error: string | null;
  sent: boolean;
  onClose: () => void;
  onSubmit: (input: { message: string; rating?: number; testimonialAllowed: boolean }) => void;
}

export function SupportDialog({ open, founder, busy, error, sent, onClose, onSubmit }: SupportDialogProps) {
  const [message, setMessage] = useState('');
  const [rating, setRating] = useState<number | undefined>();
  const [testimonialAllowed, setTestimonialAllowed] = useState(false);
  const dialogRef = useRef<HTMLFormElement>(null);
  const sentStatusRef = useRef<HTMLDivElement>(null);
  const previousFocusRef = useRef<HTMLElement | null>(null);

  useEffect(() => {
    if (!open) {
      setMessage('');
      setRating(undefined);
      setTestimonialAllowed(false);
      return undefined;
    }

    previousFocusRef.current = document.activeElement instanceof HTMLElement
      ? document.activeElement
      : null;
    const dialog = dialogRef.current;
    const initialFocus = dialog?.querySelector<HTMLElement>(
      'button:not([disabled]), textarea:not([disabled]), input:not([disabled])',
    );
    (initialFocus ?? dialog)?.focus();

    return () => {
      const previousFocus = previousFocusRef.current;
      previousFocusRef.current = null;
      if (previousFocus?.isConnected) previousFocus.focus();
    };
  }, [open]);

  useLayoutEffect(() => {
    if (!open || !sent || busy) return;
    sentStatusRef.current?.focus();
  }, [busy, open, sent]);

  if (!open) return null;
  const submit = (event: FormEvent) => {
    event.preventDefault();
    if (message.trim().length >= 10) onSubmit({ message: message.trim(), rating, testimonialAllowed });
  };
  const handleKeyDown = (event: KeyboardEvent<HTMLFormElement>) => {
    if (event.key === 'Escape') {
      event.preventDefault();
      event.stopPropagation();
      if (!busy) onClose();
      return;
    }
    if (event.key !== 'Tab') return;
    const focusable = Array.from(dialogRef.current?.querySelectorAll<HTMLElement>(
      'button:not([disabled]), textarea:not([disabled]), input:not([disabled]), a[href], [tabindex]:not([tabindex="-1"])',
    ) ?? []).filter((element) => !element.hasAttribute('hidden'));
    if (focusable.length === 0) {
      event.preventDefault();
      dialogRef.current?.focus();
      return;
    }
    const first = focusable[0];
    const last = focusable.at(-1);
    if (event.shiftKey && document.activeElement === first) {
      event.preventDefault();
      last?.focus();
    } else if (!event.shiftKey && document.activeElement === last) {
      event.preventDefault();
      first.focus();
    }
  };
  return (
    <div className="publication-offer__backdrop" role="presentation">
      <form
        ref={dialogRef}
        className="support-dialog"
        role="dialog"
        aria-modal="true"
        aria-labelledby="support-dialog-title"
        tabIndex={-1}
        onKeyDown={handleKeyDown}
        onSubmit={submit}
      >
        <header>
          <div><span>СВЯЗЬ С KAIGO</span><h2 id="support-dialog-title">{founder ? 'Расскажите, как прошёл пилот' : 'Связаться с Kaigo'}</h2></div>
          <button type="button" onClick={onClose} disabled={busy} aria-label="Закрыть"><X aria-hidden size={20} /></button>
        </header>
        {sent ? (
          <div ref={sentStatusRef} className="support-dialog__sent" role="status" tabIndex={-1}><CheckCircle aria-hidden size={28} weight="fill" /><p>Сообщение сохранено. Мы свяжемся с вами по адресу аккаунта.</p></div>
        ) : (
          <>
            {founder && <fieldset><legend>Насколько полезным оказался виджет?</legend><div>{[1, 2, 3, 4, 5].map((value) => <label key={value}><input type="radio" name="rating" value={value} checked={rating === value} onChange={() => setRating(value)} /><span>{value}</span></label>)}</div></fieldset>}
            <label htmlFor="support-message">Сообщение</label>
            <textarea id="support-message" value={message} onChange={(event) => setMessage(event.target.value)} minLength={10} maxLength={4000} required placeholder="Что понравилось, что мешает публикации или что хочется изменить?" />
            {founder && <label className="support-dialog__consent"><input type="checkbox" checked={testimonialAllowed} onChange={(event) => setTestimonialAllowed(event.target.checked)} /><span>Можно использовать мой отзыв в материалах Kaigo. Контакты без отдельного согласия не публикуются.</span></label>}
            {error && <p className="publication-offer__error" role="alert">{error}</p>}
            <button type="submit" disabled={busy || message.trim().length < 10}><PaperPlaneTilt aria-hidden size={18} />{busy ? 'Отправляем…' : 'Отправить'}</button>
          </>
        )}
      </form>
    </div>
  );
}
