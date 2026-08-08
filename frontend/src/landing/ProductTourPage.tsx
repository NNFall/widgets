import { useEffect } from 'react';

import { KaigoLogo } from '../shared/KaigoLogo';
import { ProductTour } from './ProductTour';

export function ProductTourPage() {
  useEffect(() => {
    const previousTitle = document.title;
    document.title = 'Как работает Kaigo — от ссылки до AI-сотрудника';
    return () => {
      document.title = previousTitle;
    };
  }, []);

  return (
    <main className="product-tour-page">
      <header className="product-tour-page__header">
        <a href="/" aria-label="Kaigo — главная"><KaigoLogo tone="coral" /></a>
        <a href="/">Вернуться на лендинг</a>
      </header>
      <ProductTour standalone />
    </main>
  );
}
