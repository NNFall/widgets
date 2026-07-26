const landingSections = [
  { id: 'how-it-works', title: 'Как это работает', copy: 'Три ясных шага от идеи до результата.' },
  { id: 'analysis', title: 'Анализ', copy: 'Находим задачу, где AI принесёт пользу бизнесу.' },
  { id: 'case-study', title: 'История проекта', copy: 'Показываем путь от запроса до работающего решения.' },
  { id: 'capabilities', title: 'Возможности', copy: 'Собираем AI-инструменты под реальные процессы.' },
  { id: 'studio-showcase', title: 'Студия', copy: 'Создавайте и проверяйте решения в одном месте.' },
  { id: 'faq', title: 'Частые вопросы', copy: 'Коротко отвечаем о запуске, сроках и результате.' },
] as const;

function LandingPage() {
  return (
    <main>
      <section id="hero" data-landing-section>
        <p>Kaigo</p>
        <h1>Через 10 минут вы сможете сказать: наш бизнес использует AI</h1>
        <a href="/studio">Перейти в студию</a>
      </section>

      {landingSections.map(({ id, title, copy }) => (
        <section id={id} data-landing-section key={id}>
          <h2>{title}</h2>
          <p>{copy}</p>
        </section>
      ))}

      <section id="final-cta" data-landing-section>
        <h2>Начните с одной задачи</h2>
        <a href="/studio">Открыть студию</a>
      </section>
    </main>
  );
}

function StudioPage() {
  return (
    <main>
      <h1>Студия Kaigo</h1>
      <p>Здесь начинается работа над вашим AI-инструментом.</p>
      <a href="/">Вернуться на главную</a>
    </main>
  );
}

export function App() {
  const pathname = window.location.pathname.replace(/\/+$/, '') || '/';

  return pathname === '/studio' ? <StudioPage /> : <LandingPage />;
}
