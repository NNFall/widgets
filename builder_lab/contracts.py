from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class WidgetContract:
    contract_id: str
    version: int
    attention_delay_seconds: int
    prompt_block: str
    required_regions: tuple[str, ...]


CHAT_V1 = WidgetContract(
    contract_id="chat-v1",
    version=1,
    attention_delay_seconds=15,
    required_regions=(
        "root",
        "launcher",
        "panel",
        "header",
        "messages",
        "suggestions",
        "composer",
    ),
    prompt_block="""WIDGET_CONTRACT chat-v1 VERSION 1
Implement one compact, responsive, two-sided AI chat while keeping its art direction
specific to the referenced brand.

Chat anatomy and conversation:
- Provide distinct `data-region="root"`, `data-region="launcher"`,
  `data-region="panel"`, `data-region="header"`, `data-region="messages"`,
  `data-region="suggestions"`, and `data-region="composer"` regions.
- The launcher is fully visible and clearly interactive. The header briefly identifies
  the AI employee and exposes status. The transcript reads immediately as conversation:
  assistant on the left, visitor on the right, with distinct surfaces and author labels.
- Keep composer and send available whenever open. Show at most two short quick replies
  on first open and hide the entire suggestions region after the first user message.
- Pending, retry, and error feedback remain part of the same conversation rather than
  forming a third side. Preserve history across close and reopen.

Runtime and lifecycle:
- Support launcher, opening, open, pending, error, closing, and closed lifecycle states.
- The fixed runtime owns open, close, send, suggestion, retry, transcript, pending, and
  request behavior. Use `.kaigo-widget.kaigo-preview-open [data-region="panel"]` or
  `[data-region="panel"][data-open]` for the runtime-owned open state.
- Style runtime turns through `data-kaigo-runtime-message="assistant"` and
  `data-kaigo-runtime-message="user"`, pending through
  `data-kaigo-runtime-status="pending"`, error through
  `data-kaigo-runtime-status="error"`, and retry through
  `data-kaigo-runtime-retry="true"`.

Motion and feedback:
- Provide opening and closing feedback plus visible hover, focus, active, pending, and
  error feedback.
- After about a 15-second idle delay, the closed launcher may use one subtle,
  non-aggressive attention cue. It must stop permanently after the first interaction,
  must not play sound, must not block the page, and must not repeat aggressively.
- Under `prefers-reduced-motion`, disable ambient and attention motion while keeping
  immediate state feedback.

Responsive and accessibility bounds:
- On desktop, prefer a compact panel in the 320 to 440 CSS px range. On mobile, preserve
  safe outer margins and normally use 64% to 78% of the viewport height. These are
  responsive ranges, not one exact panel size, and no mode becomes fullscreen.
- Every visible interactive control, including close, send, suggestion, and retry, has
  an actual target of at least 44px by 44px.
- The first-open transcript fits without internal scrolling; later conversation may
  scroll without making the system scrollbar the dominant visual element.

Art direction remains free: choose brand-grounded palette, typography, proportions,
shape, illustration or character treatment, launcher form, panel composition, and
motion language. Do not prescribe a color, avatar, or one exact panel size beyond the
chat usability, runtime, responsive, and accessibility invariants above.""",
)


WIDGET_CONTRACTS = {CHAT_V1.contract_id: CHAT_V1}


def resolve_widget_contract(contract_id: str) -> WidgetContract:
    if not isinstance(contract_id, str):
        raise ValueError(f"unsupported widget contract: {contract_id!r}")
    try:
        return WIDGET_CONTRACTS[contract_id]
    except KeyError as exc:
        raise ValueError(f"unsupported widget contract: {contract_id}") from exc
