import { useEffect, useState } from 'react';

import { HeroSection } from './landing/HeroSection';

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
      <HeroSection />

      {landingSections.map(({ id, title, copy }) => (
        <section className="landing-placeholder" id={id} data-landing-section key={id}>
          <h2>{title}</h2>
          <p>{copy}</p>
        </section>
      ))}

      <section className="landing-placeholder" id="final-cta" data-landing-section>
        <h2>Начните с одной задачи</h2>
        <a href="/studio">Открыть студию</a>
      </section>
    </main>
  );
}

function StudioPage() {
  return (
    <main className="studio-placeholder">
      <h1>Студия Kaigo</h1>
      <p>Здесь начинается работа над вашим AI-инструментом.</p>
      <a href="/">Вернуться на главную</a>
    </main>
  );
}

function currentPathname() {
  return window.location.pathname.replace(/\/+$/, '') || '/';
}

export function App() {
  const [pathname, setPathname] = useState(currentPathname);

  useEffect(() => {
    const syncPathname = () => setPathname(currentPathname());
    window.addEventListener('popstate', syncPathname);
    return () => window.removeEventListener('popstate', syncPathname);
  }, []);

  return pathname === '/studio' ? <StudioPage /> : <LandingPage />;
}
