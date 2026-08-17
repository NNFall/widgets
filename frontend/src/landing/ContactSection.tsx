import { FeedbackComposer } from '../shared/FeedbackComposer';

export function ContactSection() {
  return (
    <section className="landing-section contact-section" id="contact" data-landing-section aria-label="Связаться с командой Kaigo">
      <div className="landing-shell contact-section__layout">
        <div className="contact-section__copy">
          <p className="section-kicker">Связь с командой</p>
          <h2 id="contact-title">Есть вопрос или идея?</h2>
          <p>Напишите сообщение. Оно сохранится в Kaigo, контактные данные указывать не нужно.</p>
        </div>
        <FeedbackComposer source="landing_contact" />
      </div>
    </section>
  );
}
