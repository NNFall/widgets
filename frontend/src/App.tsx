import { useEffect, useState } from 'react';

import { LandingPage } from './landing/LandingPage';

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
