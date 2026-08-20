import { lazy, Suspense, useEffect, useState } from 'react';

import { LandingPage } from './landing/LandingPage';
import { ProductTourPage } from './landing/ProductTourPage';
import { WidgetInstallationPage } from './landing/WidgetInstallationPage';

const StudioRoute = lazy(async () => {
  const module = await import('./studio/StudioRoute');
  return { default: module.StudioRoute };
});

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

  if (pathname === '/tour') {
    return <ProductTourPage />;
  }

  if (pathname === '/install') {
    return <WidgetInstallationPage />;
  }

  if (pathname !== '/studio') {
    return <LandingPage />;
  }

  return (
    <Suspense fallback={<main className="auth-gate auth-gate--loading" role="status">Загружаем студию…</main>}>
      <StudioRoute />
    </Suspense>
  );
}
