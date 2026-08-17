# Captures: stored feedback frontend

Папка содержит реальные локальные browser-captures, снятые основным агентом
через in-app browser против явного локального contract mock API. Они не являются
production-снимками и не доказывают наличие backend-хранилища. Хэши намеренно
не фиксируются.

Фактические captures:

- `01-landing-feedback-mobile-filled.png` — заполненная форма лендинга,
  375×811, локальный mock;
- `02-landing-feedback-mobile-success.png` — успешное состояние `stored`,
  375×811, локальный mock;
- `03-landing-feedback-mobile-error.png` — состояние временной ошибки с
  сохранённым текстом, 375×811, локальный mock;
- `04-studio-feedback-mobile.png` — Studio drawer и форма обратной связи,
  390×843, локальный mock.

Отдельных desktop-captures в этой подборке нет. Не подменяйте captures снимками
из production и не утверждайте, что contract mock доказывает серверное
хранение.
