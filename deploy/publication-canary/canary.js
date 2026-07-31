(() => {
  'use strict';

  const status = document.getElementById('canary-status');
  const params = new URLSearchParams(window.location.search);
  const key = params.get('key') || '';
  const validKey = /^[A-Za-z0-9_-]{20,128}$/;

  if (!validKey.test(key)) {
    if (status) status.textContent = 'Публичный ключ отсутствует или некорректен.';
    return;
  }

  const script = document.createElement('script');
  script.src = `https://kaigo.space/embed/${key}.js`;
  script.async = true;
  script.addEventListener('load', () => {
    if (status) status.textContent = 'Публичный загрузчик виджета подключён.';
  }, { once: true });
  script.addEventListener('error', () => {
    if (status) status.textContent = 'Публичный загрузчик виджета отклонён.';
  }, { once: true });
  document.head.appendChild(script);
})();
