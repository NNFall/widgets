# Kaigo: публичный сайт и Studio

Дата: 2026-07-27
Статус: выпущено и проверено на production

## Что изменилось для пользователя

- На <https://kaigo.space/> появился новый публичный сайт Kaigo из восьми
  последовательных экранов: оффер, процесс, анализ, кейс, возможности, Studio,
  ответы на вопросы и финальный призыв к действию.
- Первый экран воспроизводит утверждённую идею `Perspective Orbit`: макет сайта
  проходит сканирование, уменьшается и смещается, после чего появляются три
  шага и готовый AI-виджет.
- URL можно ввести прямо на первом или последнем экране; после отправки он
  переносится в Studio.
- `/studio` получил единый интерфейс с диалогом, лентой этапов, метриками,
  desktop/mobile preview, отменой, повтором и доработкой запуска.
- Активный запуск Studio восстанавливается после обновления страницы.
- Публичная главная адаптирована под desktop и mobile. Studio пока остаётся
  закрытой существующей Builder Basic Auth.

## Что проверено

- Vitest: 49 из 49 тестов.
- Playwright: 10 из 10 сценариев на `1920x1080`, `390x844` и в режиме
  `prefers-reduced-motion`.
- Отдельно проверены axe-аудит главной и Studio, мобильные размеры интерактивных
  элементов, отсутствие горизонтального переполнения, восстановление запуска,
  конкурирующие запросы и ошибки авторизации.
- TypeScript, ESLint, production build и `git diff --check` проходят.
- Production smoke: `/` — `200`, `/studio` и `/builder/` — `401`, старые
  `/w/demka`, `/builder-demo/` и `/real-time/` — `200`.
- Хешированные JS/CSS ассеты отдаются с `immutable`; активный релиз:
  `/var/www/kaigo-marketing/releases/ba5831c`.
- Финальная публичная страница вручную открыта во встроенном браузере Codex на
  desktop и mobile.

## Визуальные доказательства

1. [Финальный hero на desktop](../../evidence/marketing-site/hero-desktop-final.png)
2. [Финальный hero на mobile](../../evidence/marketing-site/hero-mobile-final.png)
3. [Фазы hero-анимации](../../evidence/marketing-site/hero-motion-contact-sheet.jpg)
4. [Все секции сайта](../../evidence/marketing-site/landing-sections-inapp-contact-sheet.jpg)
5. [Studio на desktop](../../evidence/marketing-site/studio-desktop-current.png)
6. [Studio на mobile](../../evidence/marketing-site/studio-mobile-current.png)
7. [Сравнение hero с референсом](../../evidence/marketing-site/hero-reference-diff.png)

## Техническая фиксация

- Код релиза: `ba5831ca3de918a4e0abae96c498345f85a34037`.
- Ветка: `codex/gemini-technical-foundation`.
- Frontend: React, TypeScript, Vite, Motion и Phosphor Icons.
- Статическая сборка переключается атомарным symlink на версионный каталог;
  Nginx сохраняет старые маршруты приложения как fallback.

## Честные ограничения

- В рамках визуального релиза не запускалась новая платная генерация Gemini:
  автоматические Studio E2E используют локальный fake API и не расходуют токены.
- Кнопка публикации виджета пока показывает состояние «Скоро».
- Studio остаётся лабораторным интерфейсом под Basic Auth, а не публичным
  пользовательским кабинетом.
- Серверное хранилище запусков Builder пока не является полноценной
  многопользовательской SaaS-базой.

## Идеи для публикации

- «Сайт Kaigo теперь можно открыть: от референса до живого production».
- «Как один экран превращается из макета сайта в готового AI-сотрудника».
- «Почему мы тестируем интерфейс на desktop, mobile и reduced motion до релиза».
