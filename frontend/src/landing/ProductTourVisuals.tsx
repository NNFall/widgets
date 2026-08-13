import {
  CheckCircle,
  ChatCircleDots,
  Code,
  LinkSimple,
  PaperPlaneTilt,
} from '@phosphor-icons/react';
import type { ReactNode } from 'react';

const formaBeforeImage = '/assets/forma-site-before.webp';
const formaStudioImage = '/assets/forma-widget-answer.webp';
const formaPublishedImage = '/assets/forma-site-widget.webp';

type TourWindowProps = {
  children: ReactNode;
  className?: string;
  title: string;
};

function TourWindow({ children, className = '', title }: TourWindowProps) {
  return (
    <div className={`tour-window ${className}`}>
      <div className="tour-window__chrome">
        <span className="tour-window__controls" aria-hidden><i /><i /><i /></span>
        <span className="tour-window__address"><LinkSimple size={15} aria-hidden />{title}</span>
        <span className="tour-window__menu" aria-hidden><i /><i /><i /></span>
      </div>
      {children}
    </div>
  );
}

function FormaSiteImage({ className = '', src, alt }: { className?: string; src: string; alt: string }) {
  return (
    <div className={`tour-forma-site${className ? ` ${className}` : ''}`}>
      <img alt={alt} decoding="async" loading="lazy" src={src} />
    </div>
  );
}

export function TourIntakeVisual() {
  return (
    <div className="tour-intake-visual">
      <TourWindow className="tour-window--site" title="Ваш сайт · forma-demo.ru">
        <div className="tour-site-frame tour-site-frame--before">
          <div className="tour-site-frame__brand"><strong>FORMA</strong><span>Пространства для жизни</span></div>
          <FormaSiteImage src={formaBeforeImage} alt="Главная страница сайта FORMA до подключения AI-консультанта" />
        </div>
      </TourWindow>
      <div className="tour-intake-dock" role="group" aria-label="Данные для создания AI-консультанта">
        <div><span>Ссылка на сайт</span><strong><LinkSimple size={17} aria-hidden />https://forma-demo.ru</strong></div>
        <div><span>Пожелание, если оно есть</span><strong>Нужен спокойный консультант по проектам и стоимости</strong></div>
        <i aria-hidden><PaperPlaneTilt size={18} weight="fill" /></i>
      </div>
    </div>
  );
}

export function TourStudioVisual() {
  return (
    <TourWindow className="tour-window--studio" title="Kaigo Studio · FORMA">
      <div className="tour-studio-visual">
        <aside className="tour-studio-visual__chat">
          <div className="tour-studio-visual__brand"><ChatCircleDots size={18} weight="fill" aria-hidden /><strong>Kaigo Studio</strong></div>
          <div className="tour-studio-visual__ready"><CheckCircle size={20} weight="fill" aria-hidden /><span><strong>Предпросмотр открыт</strong><small>Проверка результата доступна бесплатно</small></span></div>
          <div className="tour-studio-visual__message"><i>F</i><p>Я изучил страницы FORMA. Можно проверить, как консультант отвечает на вопросы о проектах и стоимости.</p></div>
          <div className="tour-studio-visual__message tour-studio-visual__message--user"><p>Хочу сделать ответы короче и добавить вопросы про полный дизайн-проект.</p></div>
          <div className="tour-studio-visual__composer"><span>Пожелание к следующей версии</span><i aria-hidden><PaperPlaneTilt size={15} weight="fill" /></i></div>
        </aside>
        <div className="tour-studio-visual__preview">
          <div className="tour-studio-visual__preview-head"><span><ChatCircleDots size={17} aria-hidden />Предпросмотр виджета</span><strong>Готовый виджет</strong></div>
          <FormaSiteImage className="tour-studio-visual__capture" src={formaStudioImage} alt="Реальный ответ готового виджета FORMA в предпросмотре Kaigo Studio" />
          <p><CheckCircle size={16} weight="fill" aria-hidden />Здесь можно сразу поговорить с AI как посетитель сайта</p>
        </div>
      </div>
    </TourWindow>
  );
}

export function TourPublishVisual() {
  return (
    <div className="tour-publish-visual">
      <TourWindow className="tour-window--published" title="forma-demo.ru · AI-консультант подключён">
        <div className="tour-site-frame tour-site-frame--published">
          <div className="tour-site-frame__brand"><strong>FORMA</strong><span>Пространства для жизни</span></div>
          <FormaSiteImage src={formaPublishedImage} alt="Сайт FORMA с подключённым AI-консультантом" />
        </div>
      </TourWindow>
      <div className="tour-install-line">
        <span><Code size={19} aria-hidden />Одна строка — и виджет на сайте</span>
        <code>{'<script src="https://cdn.kaigo.ru/widget.js" data-widget="forma"></script>'}</code>
        <div className="tour-install-line__badges" aria-label="Варианты установки">
          <strong> Tilda </strong><strong> HTML </strong><strong> CMS </strong>
        </div>
        <p><CheckCircle size={18} weight="fill" aria-hidden />На Tilda добавьте строку в «Настройки сайта → Ещё → HTML-код для вставки». На другом сайте — перед закрывающим тегом body.</p>
      </div>
    </div>
  );
}
