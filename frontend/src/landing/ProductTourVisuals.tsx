import {
  ArrowRight,
  CheckCircle,
  ChatCircleDots,
  Code,
  LinkSimple,
  PaperPlaneTilt,
  Sparkle,
} from '@phosphor-icons/react';
import type { ReactNode } from 'react';

const digitalCoreAvatarImage = '/assets/product-tour-digital-core-avatar.webp';
const digitalCoreImage = '/assets/product-tour-digital-core.webp';

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

type TourWidgetProps = {
  final?: boolean;
};

function TourWidget({ final = false }: TourWidgetProps) {
  return (
    <div className={`tour-widget${final ? ' tour-widget--final' : ''}`} data-widget-shape="vertical">
      <div className="tour-widget__head">
        <span className="tour-widget__avatar">
          <img alt="" aria-hidden decoding="async" loading="lazy" src={digitalCoreAvatarImage} />
        </span>
        <div><strong>Nova, AI-аналитик</strong><small><i />Онлайн</small></div>
        <Sparkle size={18} weight="fill" aria-hidden />
      </div>
      <div className="tour-widget__intro">
        <strong>Помогу разобраться в данных и автоматизации</strong>
        <p>Задайте вопрос обычным языком. Я отвечу по материалам NovaFlow.</p>
      </div>
      <div className="tour-widget__messages">
        <p className="tour-widget__question">
          {final ? 'Можно подключить отчёты для руководителя?' : 'Что можно автоматизировать в отделе продаж?'}
        </p>
        <p className="tour-widget__answer">
          {final
            ? 'Да. Соберём показатели из CRM и покажем их в одном еженедельном отчёте. Подскажите, какой системой вы пользуетесь?'
            : 'Начнём с заявок и повторных касаний. Я покажу, какие шаги команда делает вручную и что можно передать системе.'}
        </p>
      </div>
      <div className="tour-widget__suggestions" aria-label="Примеры вопросов">
        <span>Интеграции</span><span>Срок запуска</span>
      </div>
      <div className="tour-widget__input"><span>Напишите вопрос</span><i aria-hidden><ArrowRight size={15} /></i></div>
    </div>
  );
}

function DigitalWebsite({ withWidget = false }: { withWidget?: boolean }) {
  return (
    <div className={`tour-digital-site${withWidget ? ' tour-digital-site--with-widget' : ''}`}>
      <div className="tour-digital-site__nav">
        <strong><i aria-hidden>N</i>NovaFlow</strong>
        <span>Решения</span><span>Интеграции</span><span>Кейсы</span>
        <b>Обсудить задачу</b>
      </div>
      <div className="tour-digital-site__hero">
        <div className="tour-digital-site__copy">
          <span>Автоматизация для растущих команд</span>
          <h3>Бизнес-процессы<br />без ручной рутины</h3>
          <p>Связываем CRM, отчёты и рабочие сервисы в одну понятную систему.</p>
          <b>Посмотреть решения <ArrowRight size={14} aria-hidden /></b>
        </div>
        <div className="tour-digital-site__art">
          <img alt="Металлическое цифровое ядро NovaFlow" decoding="async" loading="lazy" src={digitalCoreImage} />
          <span>CRM<small>синхронизировано</small></span>
          <span>24/7<small>процессы работают</small></span>
          <span>12 ч<small>экономии в неделю</small></span>
        </div>
      </div>
      <div className="tour-digital-site__foot">
        <span>Продажи</span><span>Аналитика</span><span>Поддержка</span><strong>Все процессы в одном контуре</strong>
      </div>
      {withWidget && <TourWidget final />}
    </div>
  );
}

export function TourIntakeVisual() {
  return (
    <div className="tour-intake-visual">
      <TourWindow className="tour-window--site" title="Ваш сайт · novaflow.ru">
        <DigitalWebsite />
      </TourWindow>
      <div className="tour-intake-dock" role="group" aria-label="Данные для создания AI-сотрудника">
        <div><span>Ссылка на сайт</span><strong><LinkSimple size={17} aria-hidden />https://novaflow.ru</strong></div>
        <div><span>Пожелание, если оно есть</span><strong>Нужен понятный консультант по автоматизации</strong></div>
        <i aria-hidden><PaperPlaneTilt size={18} weight="fill" /></i>
      </div>
    </div>
  );
}

export function TourStudioVisual() {
  return (
    <TourWindow className="tour-window--studio" title="Kaigo Studio · NovaFlow">
      <div className="tour-studio-visual">
        <aside className="tour-studio-visual__chat">
          <div className="tour-studio-visual__brand"><Sparkle size={18} weight="fill" aria-hidden /><strong>Kaigo Studio</strong></div>
          <div className="tour-studio-visual__ready"><CheckCircle size={20} weight="fill" aria-hidden /><span><strong>Первая версия готова</strong><small>Проверка результата бесплатна</small></span></div>
          <div className="tour-studio-visual__message"><i>K</i><p>Я изучил страницы NovaFlow. Виджет уже можно бесплатно проверить; доработки доступны после выбора тарифа.</p></div>
          <div className="tour-studio-visual__message tour-studio-visual__message--user"><p>Хочу сделать ответы короче и добавить вопросы про CRM.</p></div>
          <div className="tour-studio-visual__composer"><span>Пожелание к следующей версии</span><i aria-hidden><PaperPlaneTilt size={15} weight="fill" /></i></div>
        </aside>
        <div className="tour-studio-visual__preview">
          <div className="tour-studio-visual__preview-head"><span><ChatCircleDots size={17} aria-hidden />Предпросмотр виджета</span><strong>Черновик</strong></div>
          <div className="tour-studio-visual__canvas"><TourWidget /></div>
          <p><CheckCircle size={16} weight="fill" aria-hidden />Здесь можно сразу поговорить с AI как посетитель сайта</p>
        </div>
      </div>
    </TourWindow>
  );
}

export function TourPublishVisual() {
  return (
    <div className="tour-publish-visual">
      <TourWindow className="tour-window--published" title="novaflow.ru · AI-сотрудник подключён">
        <DigitalWebsite withWidget />
      </TourWindow>
      <div className="tour-install-line">
        <span><Code size={19} aria-hidden />Пример строки для установки</span>
        <code>{'<script src="https://cdn.kaigo.example/widget.js" data-widget="ваш-id"></script>'}</code>
        <strong><CheckCircle size={18} weight="fill" aria-hidden />Виджет появится только после вашего подтверждения</strong>
      </div>
    </div>
  );
}
