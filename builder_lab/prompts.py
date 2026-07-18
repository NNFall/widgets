from __future__ import annotations

import json

from .models import BuilderRequest, Stage, ValidationIssue, WidgetArtifact


ARTIFACT_JSON_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": [
        "schema_version",
        "revision",
        "stage",
        "art_direction",
        "body_html",
        "css",
        "theme_tokens",
        "suggested_actions",
    ],
    "properties": {
        "schema_version": {"type": "string", "enum": ["1.0"]},
        "revision": {"type": "integer", "minimum": 1},
        "stage": {"type": "string", "enum": [stage.value for stage in Stage]},
        "art_direction": {"type": "string", "maxLength": 8000},
        "body_html": {"type": "string", "maxLength": 65536},
        "css": {"type": "string", "maxLength": 98304},
        "theme_tokens": {
            "type": "object",
            "additionalProperties": {"type": "string"},
        },
        "suggested_actions": {
            "type": "array",
            "maxItems": 8,
            "items": {"type": "string", "maxLength": 120},
        },
    },
}


STAGE_GUIDANCE = {
    Stage.ART_DIRECTION: (
        "Определи единую визуальную метафору, палитру, типографический характер, "
        "пространственную композицию и язык движения. Уже верни полный минимально "
        "работоспособный виджет, а не текстовый план."
    ),
    Stage.FOUNDATION: (
        "Сделай выразительный фон сцены, геометрию launcher и panel, адаптивную "
        "композицию и сильный силуэт. Сохрани выбранную метафору."
    ),
    Stage.IDENTITY: (
        "Доработай личность AI-сотрудника, header, статус, визуальную подпись, "
        "глубину и осмысленные декоративные детали."
    ),
    Stage.CONVERSATION: (
        "Доработай messages, suggestions и composer: ясная иерархия, полезные "
        "русские тексты и ощущение живого профессионального сотрудника."
    ),
    Stage.MOTION_POLISH: (
        "Добавь аккуратные входные, hover, focus и ambient-анимации. Каждая "
        "анимация конечна, длится не более 20 секунд и имеет reduced-motion fallback."
    ),
    Stage.VALIDATION: (
        "Исправь только перечисленные детерминированные ошибки, сохранив сильную "
        "арт-дирекцию и вернув полный исправленный артефакт."
    ),
    Stage.AGENT_BUILD: "Верни полный безопасный артефакт после агентской сборки.",
}


def build_stage_prompt(
    *,
    request: BuilderRequest,
    stage: Stage,
    revision: int,
    previous_artifact: WidgetArtifact | None,
    repair_issues: tuple[ValidationIssue, ...] = (),
) -> str:
    previous = (
        json.dumps(previous_artifact.to_dict(), ensure_ascii=False, separators=(",", ":"))
        if previous_artifact
        else "null"
    )
    issue_payload = [issue.to_dict() for issue in repair_issues]
    mode = "repair" if repair_issues else "generation"
    guidance = (
        STAGE_GUIDANCE[Stage.VALIDATION]
        if repair_issues
        else STAGE_GUIDANCE[stage]
    )
    return f"""Ты — ведущий digital art director и frontend-дизайнер Kaigo.

Создай премиальный, индивидуальный AI-виджет на русском языке. Он должен выглядеть
как естественный AI-сотрудник конкретного бизнеса, а не как типовой чат-бот.
Запрещены бездумный фиолетовый градиент, случайный glassmorphism, стандартный
круглый bubble и эффекты без единой идеи. Выбери смелую, но цельную визуальную
метафору и развивай её от предыдущей ревизии.

Безопасный контракт обязателен:
- верни только JSON по заданной схеме;
- body_html содержит полный HTML-фрагмент, css содержит полный stylesheet;
- никакого JavaScript, script, iframe, form, внешних URL, @import или url();
- корневой класс всех CSS-селекторов — .kaigo-widget;
- обязательны data-region: root, launcher, panel, header, messages, suggestions, composer;
- launcher и composer имеют понятный aria-label;
- используются только системные шрифты и inline CSS-графика;
- бесконечные анимации запрещены на любом этапе: не используй `infinite`;
- `animation-iteration-count` допускает не более 12 повторов, длительность одной
  анимации — не более 20 секунд;
- любая анимация обязательно имеет `@media (prefers-reduced-motion: reduce)` с
  отключением animation и transition;
- текущая revision строго {revision}, stage строго {stage.value}.

Этап: {stage.value}
Режим: {mode}
Задача этапа: {guidance}
Локаль: {request.locale}
Viewport: {', '.join(request.viewport_targets)}
Бриф пользователя:
{request.brief}

Предыдущий полный артефакт:
{previous}

Конкретные ошибки валидатора для repair:
{json.dumps(issue_payload, ensure_ascii=False, separators=(',', ':'))}

Верни полный renderable-кандидат, а не фрагмент и не объяснение.
"""
