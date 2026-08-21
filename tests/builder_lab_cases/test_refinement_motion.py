from __future__ import annotations

from dataclasses import replace

import pytest

from builder_lab.refinement_motion import RefinementMotionAudit, RefinementMotionAuditError
from tests.builder_lab_cases.test_validation import artifact


MOTION_HTML = """
<section class="kaigo-widget" data-region="root" aria-label="RFN Assistant">
  <button data-region="launcher" type="button">R</button>
  <section data-region="panel" role="dialog" aria-label="RFN Assistant">
    <header data-region="header">
      <strong data-assistant-name>RFN Assistant</strong>
      <button data-action="close" type="button" aria-label="Закрыть">×</button>
    </header>
    <main data-region="messages"><p>Задайте вопрос.</p></main>
    <nav data-region="suggestions">
      <button data-suggestion="Какие услуги?" type="button">Какие услуги?</button>
    </nav>
    <form data-region="composer">
      <textarea aria-label="Сообщение"></textarea>
      <button data-action="send" type="submit">Отправить</button>
    </form>
  </section>
</section>
"""


MOTION_CSS = """
* { box-sizing: border-box; }
.kaigo-widget {
  position: fixed;
  right: 16px;
  bottom: 16px;
  color: #102235;
}
[data-region="launcher"] {
  width: 64px;
  height: 64px;
  border: 0;
  border-radius: 20px;
  background: #ff633f;
  color: white;
  transition: transform 180ms ease, box-shadow 180ms ease, filter 180ms ease;
}
[data-region="launcher"]:hover {
  transform: translateY(-7px) scale(1.055);
  box-shadow: 0 18px 38px rgba(255, 99, 63, .38);
  filter: saturate(1.16);
}
[data-region="panel"] {
  position: absolute;
  right: 0;
  bottom: 0;
  display: grid;
  grid-template-rows: 56px minmax(0, 1fr) auto 64px;
  width: min(372px, calc(100vw - 24px));
  height: min(430px, calc(100vh - 24px));
  border: 1px solid #d9e0e7;
  border-radius: 24px;
  background: white;
  opacity: 0;
  visibility: hidden;
  pointer-events: none;
  transform: translateY(-14px) scale(.94);
  transform-origin: bottom right;
  transition:
    opacity 320ms ease,
    transform 320ms cubic-bezier(.22, .85, .26, 1),
    visibility 0s linear 320ms;
}
.kaigo-widget.kaigo-preview-open [data-region="panel"] {
  opacity: 1;
  visibility: visible;
  pointer-events: auto;
  transform: translateY(0) scale(1);
  transition-delay: 0s;
}
[data-region="header"], [data-region="composer"] {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  padding: 8px 12px;
}
[data-region="messages"] { min-height: 0; overflow: auto; padding: 12px; }
[data-region="suggestions"] { padding: 6px 12px; }
[data-kaigo-runtime-message] { display: flex; flex-direction: column; }
button, textarea { min-height: 44px; }
textarea { flex: 1; min-width: 0; }
@media (prefers-reduced-motion: reduce) {
  [data-region="launcher"], [data-region="panel"] {
    animation: none !important;
    transition: none !important;
  }
}
"""


def motion_artifact(*, css: str = MOTION_CSS, body_html: str = MOTION_HTML):
    return artifact(
        body_html=body_html,
        css=css,
        javascript="throw new Error('generated javascript must stay inert')",
        revision=17,
    )


def motion_audit(*, total_timeout_seconds: float = 30) -> RefinementMotionAudit:
    return RefinementMotionAudit(
        assistant_label="RFN Assistant",
        total_timeout_seconds=total_timeout_seconds,
    )


def test_motion_audit_requires_persona_assistant_label():
    with pytest.raises(TypeError):
        RefinementMotionAudit()


@pytest.mark.asyncio
async def test_motion_audit_samples_hover_close_and_reduced_motion():
    report = await motion_audit(total_timeout_seconds=45).audit(motion_artifact())

    assert report.assistant_label == "RFN Assistant"
    assert [(item.width, item.height) for item in report.viewports] == [
        (1920, 1080),
        (390, 844),
    ]
    assert report.desktop.hover is not None
    assert report.mobile.hover is None
    assert report.desktop.hover.distinct_frame_count >= 3
    assert 80 <= report.desktop.hover.duration_ms <= 600
    assert report.desktop.hover.restored_baseline is True
    for viewport in report.viewports:
        assert viewport.close.distinct_frame_count >= 3
        assert 120 <= viewport.close.duration_ms <= 900
        assert viewport.close.terminal_closed is True
        assert viewport.close.reopens_with_transcript is True
        assert viewport.close.reopens_with_draft is True
        assert viewport.reduced_motion.continuing_animation_count == 0


@pytest.mark.asyncio
async def test_motion_audit_rejects_inert_hover():
    candidate = motion_artifact(
        css=MOTION_CSS
        + "\n[data-region=\"launcher\"]:hover { transform: none; box-shadow: none; filter: none; }"
    )

    with pytest.raises(RefinementMotionAuditError, match="hover.*animation|hover.*frames"):
        await motion_audit().audit(candidate)


@pytest.mark.asyncio
async def test_motion_audit_rejects_infinite_hover():
    candidate = motion_artifact(
        css=MOTION_CSS
        + """
        @keyframes forever { from { opacity: .99; } to { opacity: 1; } }
        [data-region="launcher"]:hover { animation: forever 180ms linear infinite; }
        """
    )

    with pytest.raises(RefinementMotionAuditError, match="hover.*infinite"):
        await motion_audit().audit(candidate)


@pytest.mark.asyncio
async def test_motion_audit_rejects_close_without_finite_transition():
    candidate = motion_artifact(
        css=MOTION_CSS
        + "\n[data-region=\"panel\"] { transition: none !important; }"
    )

    with pytest.raises(RefinementMotionAuditError, match="close.*animation"):
        await motion_audit().audit(candidate)


@pytest.mark.asyncio
async def test_motion_audit_rejects_finite_close_that_never_reaches_closed_state():
    broken = MOTION_HTML.replace(
        'data-action="close" type="button"',
        'data-action="close" type="button" '
        'onclick="event.stopImmediatePropagation();'
        "document.querySelector('[data-region=root]').classList.remove('kaigo-preview-open')\"",
    )
    candidate = motion_artifact(body_html=broken)

    with pytest.raises(
        RefinementMotionAuditError,
        match="close never reached terminal closed state",
    ):
        await motion_audit().audit(candidate)


@pytest.mark.asyncio
async def test_motion_audit_rejects_close_motion_clipping_viewport():
    candidate = motion_artifact(
        css=MOTION_CSS
        + """
        .kaigo-widget:not(.kaigo-preview-open) [data-region="panel"] {
          transform: translateX(520px) scale(.94);
        }
        """
    )

    with pytest.raises(RefinementMotionAuditError, match="close.*viewport|close.*clipped"):
        await motion_audit().audit(candidate)


@pytest.mark.asyncio
async def test_motion_audit_rejects_close_reopen_that_loses_transcript_or_draft():
    broken = MOTION_HTML.replace(
        'data-action="close" type="button"',
        'data-action="close" type="button" '
        'onclick="document.querySelector(\'[data-region=messages]\').replaceChildren();'
        "document.querySelector('[data-kaigo-runtime-input]').value=''\"",
    )
    candidate = motion_artifact(body_html=broken)

    with pytest.raises(
        RefinementMotionAuditError,
        match="close.*(?:transcript|draft)|reopen.*(?:transcript|draft)",
    ):
        await motion_audit().audit(candidate)


@pytest.mark.asyncio
async def test_motion_audit_rejects_reduced_motion_that_keeps_animating():
    candidate = motion_artifact(
        css=MOTION_CSS
        + """
        @media (prefers-reduced-motion: reduce) {
          [data-region="panel"] {
            transition: opacity 320ms linear !important;
          }
        }
        """
    )

    with pytest.raises(RefinementMotionAuditError, match="reduced-motion.*animation"):
        await motion_audit().audit(candidate)


@pytest.mark.asyncio
async def test_motion_audit_rejects_preexisting_infinite_reduced_motion():
    candidate = motion_artifact(
        css=MOTION_CSS
        + """
        @keyframes reduced-loop {
          from { transform: translateY(0); }
          to { transform: translateY(-4px); }
        }
        @media (prefers-reduced-motion: reduce) {
          [data-region="panel"] {
            animation: reduced-loop 180ms linear infinite !important;
          }
        }
        """
    )

    with pytest.raises(RefinementMotionAuditError, match="reduced-motion.*animation"):
        await motion_audit().audit(candidate)


@pytest.mark.asyncio
async def test_motion_audit_rejects_reduced_motion_that_returns_at_endpoints():
    candidate = motion_artifact(
        css=MOTION_CSS
        + """
        @keyframes reduced-pulse-return {
          0%, 100% { transform: translateY(0); }
          50% { transform: translateY(-12px); }
        }
        @media (prefers-reduced-motion: reduce) {
          [data-region="panel"] {
            animation: reduced-pulse-return 60000ms linear 1 !important;
          }
        }
        """
    )

    with pytest.raises(RefinementMotionAuditError, match="reduced-motion.*animation"):
        await motion_audit().audit(candidate)


@pytest.mark.asyncio
async def test_motion_audit_rejects_reduced_motion_on_visual_ancestor():
    candidate = motion_artifact(
        css=MOTION_CSS
        + """
        @keyframes reduced-ancestor-fade {
          from { opacity: 1; }
          to { opacity: .45; }
        }
        @media (prefers-reduced-motion: reduce) {
          [data-region="root"] {
            animation: reduced-ancestor-fade 60000ms linear 1 !important;
          }
        }
        """
    )

    with pytest.raises(RefinementMotionAuditError, match="reduced-motion.*animation"):
        await motion_audit().audit(candidate)


@pytest.mark.asyncio
async def test_motion_audit_rejects_short_visible_close_padded_by_nonvisual_motion():
    candidate = motion_artifact(
        css=MOTION_CSS
        + """
        @property --audit-padding {
          syntax: "<number>";
          inherits: false;
          initial-value: 0;
        }
        @media (prefers-reduced-motion: no-preference) {
          [data-region="panel"] {
            --audit-padding: 0;
            transition:
              opacity 40ms linear,
              transform 40ms linear,
              --audit-padding 500ms linear !important;
          }
          .kaigo-widget.kaigo-preview-open [data-region="panel"] {
            --audit-padding: 1;
          }
        }
        """
    )

    with pytest.raises(RefinementMotionAuditError, match="close animation duration 40"):
        await motion_audit().audit(candidate)


@pytest.mark.asyncio
async def test_motion_audit_accepts_action_motion_that_returns_at_endpoints():
    candidate = motion_artifact(
        css=MOTION_CSS
        + """
        @keyframes hover-pulse-return {
          0%, 100% { transform: translateY(0); }
          30% { transform: translateY(-10px); }
          80% { transform: translateY(-4px); }
        }
        [data-region="launcher"]:hover {
          transform: none;
          box-shadow: none;
          filter: none;
          animation: hover-pulse-return 180ms linear 1;
        }
        """
    )

    report = await motion_audit().audit(candidate)

    assert report.desktop.hover is not None
    assert report.desktop.hover.duration_ms == 180
    assert report.desktop.hover.distinct_frame_count >= 3


@pytest.mark.asyncio
async def test_motion_audit_captures_minimum_fast_finite_transitions():
    fast_css = MOTION_CSS.replace("180ms", "80ms").replace("320ms", "120ms")
    delayed_collection_html = MOTION_HTML.replace(
        'data-region="launcher" type="button"',
        'data-region="launcher" type="button" '
        'onmouseover="queueMicrotask(()=>{const end=performance.now()+180;'
        'while(performance.now()<end){}})"',
    ).replace(
        'data-action="close" type="button"',
        'data-action="close" type="button" '
        'onclick="queueMicrotask(()=>{const end=performance.now()+180;'
        'while(performance.now()<end){}})"',
    )

    report = await motion_audit().audit(
        motion_artifact(css=fast_css, body_html=delayed_collection_html)
    )

    assert report.desktop.hover is not None
    assert report.desktop.hover.duration_ms == 80
    assert report.desktop.close.duration_ms == 120
    assert report.mobile.close.duration_ms == 120


def test_motion_report_is_immutable():
    # The evidence is passed between gates; callers must not be able to rewrite it.
    from builder_lab.refinement_motion import ActionMotionEvidence

    evidence = ActionMotionEvidence(
        action="hover",
        duration_ms=180,
        distinct_frame_count=3,
        frames=(),
        restored_baseline=True,
    )

    with pytest.raises((AttributeError, TypeError)):
        replace(evidence, duration_ms=200).duration_ms = 300
