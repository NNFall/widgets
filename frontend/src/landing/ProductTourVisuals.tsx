import {
  CheckCircle,
  ClockCountdown,
  Code,
  Desktop,
  DeviceMobile,
  LinkSimple,
  MagnifyingGlass,
  Sparkle,
} from '@phosphor-icons/react';
import type { ReactNode } from 'react';

const bakeryImage = '/assets/product-tour-bakery.webp';

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
  compact?: boolean;
  final?: boolean;
};

function TourWidget({ compact = false, final = false }: TourWidgetProps) {
  return (
    <div
      className={`tour-widget${compact ? ' tour-widget--compact' : ''}`}
      data-widget-shape="vertical"
    >
      <div className="tour-widget__head">
        <span><Sparkle size={17} weight="fill" aria-hidden /></span>
        <div><strong>Помощник пекарни</strong><small><i />На связи</small></div>
      </div>
      <div className="tour-widget__messages">
        <p className="tour-widget__question">
          {final ? 'Какие торты можно заказать к субботе?' : 'Есть доставка сегодня?'}
        </p>
        <p className="tour-widget__answer">
          {final
            ? 'Есть четыре начинки. Подскажите число гостей, и я помогу выбрать размер.'
            : 'Да, доставим после 16:00. Подскажите район и что хотите заказать.'}
        </p>
      </div>
      <div className="tour-widget__input"><span>Ваш вопрос</span><i aria-hidden>→</i></div>
    </div>
  );
}

function BakeryWebsite({ withWidget = false }: { withWidget?: boolean }) {
  return (
    <div className={`tour-bakery-site${withWidget ? ' tour-bakery-site--with-widget' : ''}`}>
      <img
        alt="Свежая выпечка на рабочем столе пекарни"
        decoding="async"
        loading="lazy"
        src={bakeryImage}
      />
      <div className="tour-bakery-site__nav">
        <strong>Тёплый хлеб</strong>
        <span>Выпечка</span><span>Торты</span><span>Доставка</span>
      </div>
      <div className="tour-bakery-site__copy">
        <span>Пекарня у дома</span>
        <h3>Хлеб, который<br />начинается утром</h3>
        <p>Свежая выпечка, торты на заказ и доставка по району.</p>
        <b>Посмотреть меню</b>
      </div>
      {withWidget && <TourWidget final />}
    </div>
  );
}

export function TourIntakeVisual() {
  return (
    <div className="tour-intake-visual">
      <TourWindow className="tour-window--site" title="Ваш сайт · teply-hleb.ru">
        <BakeryWebsite />
      </TourWindow>
      <div className="tour-intake-fields" role="group" aria-label="Данные для создания виджета">
        <div>
          <span>Ссылка на сайт</span>
          <strong><LinkSimple size={18} aria-hidden />https://teply-hleb.ru</strong>
        </div>
        <div>
          <span>Короткое пожелание</span>
          <strong className="tour-intake-fields__brief">Хочу красивого AI-консультанта для заказов<i aria-hidden /></strong>
        </div>
        <p><CheckCircle size={19} weight="fill" aria-hidden />Этого достаточно, длинная анкета не нужна</p>
      </div>
    </div>
  );
}

const analysisFacts = [
  '12 страниц изучено',
  '8 услуг и категорий найдено',
  'Стиль сайта определён',
] as const;

export function TourAnalysisVisual() {
  return (
    <TourWindow className="tour-window--studio" title="Kaigo Studio · Тёплый хлеб">
      <div className="tour-analysis">
        <aside className="tour-analysis__rail">
          <span className="tour-analysis__brand"><Sparkle size={17} weight="fill" aria-hidden />Kaigo</span>
          <ol aria-label="Ход сборки AI-сотрудника">
            <li data-complete="true"><i>1</i><span>Сайт открыт<small>teply-hleb.ru</small></span></li>
            <li data-active="true"><i>2</i><span>Анализируем<small>страницы и стиль</small></span></li>
            <li><i>3</i><span>Собираем чат<small>ответы и характер</small></span></li>
          </ol>
        </aside>
        <div className="tour-analysis__site">
          <img alt="Страница пекарни, которую анализирует Kaigo" src={bakeryImage} />
          <span className="tour-analysis__scan" aria-hidden />
          <strong><MagnifyingGlass size={19} aria-hidden />Изучаем эту страницу</strong>
        </div>
        <div className="tour-analysis__result">
          <div className="tour-analysis__time"><ClockCountdown size={22} aria-hidden /><span><small>Первая версия</small><strong>Обычно 10–20 минут</strong></span></div>
          <h3>Kaigo уже понимает ваш бизнес</h3>
          <ul>
            {analysisFacts.map((fact) => <li key={fact}><CheckCircle size={18} weight="fill" aria-hidden />{fact}</li>)}
          </ul>
          <div className="tour-analysis__prompt">
            <span>Сейчас</span>
            <strong>Собираем сценарий разговора</strong>
            <p>Как встретить посетителя, ответить про ассортимент и помочь оформить заказ.</p>
            <i aria-hidden><b /><b /><b /></i>
          </div>
        </div>
      </div>
    </TourWindow>
  );
}

export function TourStudioVisual() {
  return (
    <TourWindow className="tour-window--preview" title="Kaigo Studio · Предпросмотр">
      <div className="tour-preview">
        <div className="tour-preview__toolbar">
          <div><strong>Виджет готов к проверке</strong><small><i />Черновик, ещё не опубликован</small></div>
          <span><Desktop size={18} aria-hidden />Компьютер</span>
          <span><DeviceMobile size={18} aria-hidden />Телефон</span>
          <b>Первая генерация бесплатно</b>
        </div>
        <div className="tour-preview__canvas">
          <div className="tour-preview__desktop">
            <BakeryWebsite />
            <TourWidget compact />
          </div>
          <div className="tour-preview__phone" aria-label="Предпросмотр виджета на телефоне">
            <div className="tour-preview__phone-top" aria-hidden />
            <img alt="Мобильная версия сайта пекарни" src={bakeryImage} />
            <span><Sparkle size={15} weight="fill" aria-hidden /></span>
          </div>
        </div>
        <div className="tour-preview__request">
          <span>Пожелание к доработке</span>
          <strong>Сделайте ответы короче и дружелюбнее</strong>
          <i><CheckCircle size={17} weight="fill" aria-hidden />Можно проверить чат до оплаты</i>
        </div>
      </div>
    </TourWindow>
  );
}

export function TourPublishVisual() {
  return (
    <div className="tour-publish-visual">
      <TourWindow className="tour-window--published" title="teply-hleb.ru · AI-сотрудник подключён">
        <BakeryWebsite withWidget />
      </TourWindow>
      <div className="tour-install-line">
        <span><Code size={19} aria-hidden />Пример строки для установки</span>
        <code>{'<script src="https://cdn.kaigo.example/widget.js" data-widget="ваш-id"></script>'}</code>
        <strong><CheckCircle size={18} weight="fill" aria-hidden />Виджет появляется на выбранных страницах сайта</strong>
      </div>
    </div>
  );
}
