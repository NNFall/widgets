from __future__ import annotations

import json

from .models import (
    BuilderRequest,
    DirectionProposal,
    DirectionRole,
    Stage,
    ValidationIssue,
    WidgetArtifact,
)


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


DIRECTION_PROPOSAL_JSON_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["title", "art_direction", "interaction_model", "safeguards"],
    "properties": {
        "title": {"type": "string", "minLength": 1, "maxLength": 80},
        "art_direction": {"type": "string", "minLength": 1, "maxLength": 1200},
        "interaction_model": {"type": "string", "minLength": 1, "maxLength": 800},
        "safeguards": {
            "type": "array",
            "maxItems": 8,
            "items": {"type": "string", "minLength": 1, "maxLength": 160},
        },
    },
}


DIRECTION_JUDGE_JSON_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["selected_proposal_id", "rationale"],
    "properties": {
        "selected_proposal_id": {
            "type": "string",
            "enum": ["candidate-1", "candidate-2", "candidate-3"],
        },
        "rationale": {"type": "string", "minLength": 1, "maxLength": 1200},
    },
}


_DIRECTION_ROLE_BRIEFS = {
    DirectionRole.BRAND_ARCHAEOLOGIST: (
        "brand archaeologist: extract the site's visual grammar, hierarchy, type, "
        "spacing and surfaces without copying page content or inventing facts"
    ),
    DirectionRole.INTERACTION_INVENTOR: (
        "interaction inventor: propose one non-generic but feasible compact interaction "
        "that remains subordinate to the host page"
    ),
    DirectionRole.HOSTILE_CONVERSION_ACCESSIBILITY_CRITIC: (
        "hostile conversion/accessibility critic: expose fake actions, generic chat UI, "
        "oversized geometry, responsive failures and accessibility barriers, then turn "
        "those objections into a defensible direction"
    ),
}


def build_direction_proposal_prompt(
    *,
    request: BuilderRequest,
    role: DirectionRole,
) -> str:
    return f"""You are the independent {_DIRECTION_ROLE_BRIEFS[role]}.

Work alone. Do not simulate a panel, judge, recursive agent, or other proposals.
Return exactly one bounded JSON proposal. The implementation will be generated later.
The proposal must describe a truthful AI widget rather than claiming unavailable actions.

Non-negotiable product bounds: desktop open width 372px and height no more than 68dvh;
mobile height no more than 70dvh and no fullscreen; default state closed; no more than
two first-open suggestions; no fake actions. Generated content has no JavaScript or
network and must be implementable by the trusted Kaigo runtime.

Locale: {request.locale}
Brief:
{request.brief}
"""


def build_direction_judge_prompt(
    *,
    request: BuilderRequest,
    proposals: tuple[DirectionProposal, ...],
) -> str:
    anonymous = [proposal.to_anonymous_dict() for proposal in proposals]
    return f"""You are the blind direction judge. Candidate authors and roles are hidden.
Choose exactly one candidate using this fixed matrix: site fit, subordination to the
host page, functional truth, responsive integrity, accessibility, and trusted-runtime
feasibility. Do not merge candidates, ask follow-up questions, or start an agent loop.
Return only bounded JSON.

Locale: {request.locale}
Brief:
{request.brief}

Anonymous candidates:
{json.dumps(anonymous, ensure_ascii=False, separators=(',', ':'))}
"""


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
    selected_direction: DirectionProposal | None = None,
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
    direction_payload = (
        json.dumps(
            selected_direction.to_anonymous_dict(),
            ensure_ascii=False,
            separators=(",", ":"),
        )
        if selected_direction
        else "null"
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
- для нативных контролов разрешены безопасные атрибуты `for`, `name`, `checked`, `open`,
  `selected`, `autocomplete`, `inputmode`, `rows`, `cols`, `min`, `max`, `step`;
  inline `style` и event-атрибуты запрещены;
- SVG path использует только простые M/L/H/V/C/S/Q/T/Z-команды без A/a arc;
  для окружностей и дуг используй безопасные элементы circle или ellipse;
- текущая revision строго {revision}, stage строго {stage.value}.
- состояние по умолчанию строго closed; panel не открывается автоматически;
- desktop panel: ширина 372px, высота по содержимому максимум min(536px, 68dvh);
- mobile panel: максимум 70dvh, no fullscreen, no backdrop и не блокирует страницу;
- на первом открытии не более двух suggestions; каждая запускает реальный запрос;
- fake actions, пустые кнопки и действия, которые только очищают поле, запрещены;
- trusted runtime использует классы `.kaigo-widget__message--assistant`,
  `.kaigo-widget__message--user`, `.kaigo-widget__message--status` и
  `.kaigo-widget__message--error`; CSS обязан оформить их как редакционный transcript,
  без bubbles и avatars;
- data-action описывает только реальные open, close, send, suggestion и retry;
  сгенерированный artifact не имитирует ответы.

Этап: {stage.value}
Режим: {mode}
Задача этапа: {guidance}
Локаль: {request.locale}
Viewport: {', '.join(request.viewport_targets)}
Бриф пользователя:
{request.brief}

Выбранное blind-judge направление обязательно и неизменно для всех пяти этапов:
{direction_payload}

Предыдущий полный артефакт:
{previous}

Конкретные ошибки валидатора для repair:
{json.dumps(issue_payload, ensure_ascii=False, separators=(',', ':'))}

Верни полный renderable-кандидат, а не фрагмент и не объяснение.
"""
