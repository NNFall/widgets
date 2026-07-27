import { useEffect, useState } from 'react';

import { LandingPage } from './landing/LandingPage';
import { StudioPage } from './studio/StudioPage';

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
