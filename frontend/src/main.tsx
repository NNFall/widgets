import '@fontsource/manrope/400.css';
import '@fontsource/manrope/600.css';
import '@fontsource/manrope/700.css';
import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';

import { App } from './App';
import './styles.css';

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <App />
  </StrictMode>,
);
