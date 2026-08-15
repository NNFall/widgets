import { FeedbackComposer } from '../shared/FeedbackComposer';
import { CONTACT_CONFIG } from '../shared/contact';

export function ContactSection() {
  return (
    <section className="landing-section contact-section" id="contact" data-landing-section aria-label="Связаться с командой Kaigo">
      <div className="landing-shell contact-section__layout">
        <div className="contact-section__copy">
          <p className="section-kicker">Связь с командой</p>
          <h2 id="contact-title">Есть вопрос или идея?</h2>
          <p>Напишите, если хотите разобраться с первым запуском, сообщить об ошибке, предложить улучшение или обсудить сотрудничество. Сообщения читает команда Kaigo.</p>
          <div className="contact-section__channels" aria-label="Каналы связи">
            <div className="contact-channel">
              <span>Поддержка</span>
              <a href={`mailto:${encodeURIComponent(CONTACT_CONFIG.supportEmail)}`}>{CONTACT_CONFIG.supportEmail}</a>
              <small>Адрес временный для предпросмотра, владелец должен подтвердить, что он принимает письма.</small>
            </div>
            <div className="contact-channel contact-channel--pending">
              <span>Telegram</span>
              <small>Добавим после подтверждения контакта</small>
            </div>
          </div>
        </div>
        <FeedbackComposer page="/contact" />
      </div>
    </section>
  );
}
