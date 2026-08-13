import { CheckCircle, PaperPlaneTilt, X } from '@phosphor-icons/react';
import { FormEvent, useEffect, useState } from 'react';

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
  useEffect(() => {
    if (!open) {
      setMessage('');
      setRating(undefined);
      setTestimonialAllowed(false);
    }
  }, [open]);
  if (!open) return null;
  const submit = (event: FormEvent) => {
    event.preventDefault();
    if (message.trim().length >= 10) onSubmit({ message: message.trim(), rating, testimonialAllowed });
  };
  return (
    <div className="publication-offer__backdrop" role="presentation">
      <form className="support-dialog" role="dialog" aria-modal="true" aria-labelledby="support-dialog-title" onSubmit={submit}>
        <header>
          <div><span>СВЯЗЬ С KAIGO</span><h2 id="support-dialog-title">{founder ? 'Расскажите, как прошёл пилот' : 'Опишите вопрос'}</h2></div>
          <button type="button" onClick={onClose} disabled={busy} aria-label="Закрыть"><X aria-hidden size={20} /></button>
        </header>
        {sent ? (
          <div className="support-dialog__sent" role="status"><CheckCircle aria-hidden size={28} weight="fill" /><p>Сообщение сохранено. Мы свяжемся с вами по адресу аккаунта.</p></div>
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
