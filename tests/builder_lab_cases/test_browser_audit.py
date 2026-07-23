import asyncio
import hashlib
import io
import unittest
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import patch

from PIL import Image
from playwright.async_api import async_playwright

from builder_lab.browser_audit import (
    BrowserAudit,
    BrowserAuditError,
    BrowserAuditReport,
    CapturedScreenshot,
)
from builder_lab.preview import build_preview_document
from builder_lab.visual_models import LayoutState, ScreenshotEvidence, ScreenshotState
from tests.builder_lab_cases.test_validation import artifact


AUDIT_HTML = """
<section class="kaigo" data-region="root" aria-label="RAW AI">
  <button data-region="launcher" aria-label="Открыть RAW AI">RAW AI</button>
  <section data-region="panel" role="dialog" aria-label="RAW AI">
    <header data-region="header"><strong>RAW AI</strong><button data-action="close" aria-label="Закрыть">×</button></header>
    <main data-region="messages"><p>Задайте короткий вопрос.</p></main>
    <nav data-region="suggestions"><button data-suggestion="Какие услуги?">Какие услуги?</button></nav>
    <form data-region="composer"><textarea aria-label="Сообщение"></textarea><button data-action="send">Отправить</button></form>
  </section>
</section>
"""


AUDIT_CSS = """
* { box-sizing: border-box; }
.kaigo { position: fixed; right: 20px; bottom: 20px; color: #111; }
[data-region="launcher"] { width: 216px; height: 46px; border: 1px solid #111; background: #f5f5f5; }
[data-region="panel"] { display: none; width: 372px; height: 304px; background: #f5f5f5; border: 1px solid #111; }
.kaigo-preview-open [data-region="launcher"] { display: none; }
.kaigo-preview-open [data-region="panel"] { display: grid; grid-template-rows: 52px 1fr 48px 60px; }
[data-region="header"], [data-region="composer"] { display: flex; align-items: center; justify-content: space-between; }
[data-region="messages"] { overflow: auto; min-height: 0; }
[data-region="root"]:has([data-kaigo-runtime-status="error"]) [data-region="suggestions"] { display: none; }
button, textarea { min-height: 44px; }
button { min-width: 44px; }
textarea { flex: 1 1 auto; min-width: 0; width: auto; }
@media (max-width: 600px) {
  .kaigo { right: 12px; bottom: 12px; left: 12px; }
  [data-region="launcher"] { display: block; margin-left: auto; }
  [data-region="panel"] { width: 366px; height: 320px; }
}
"""


def audit_artifact(**changes):
    payload = {"body_html": AUDIT_HTML, "css": AUDIT_CSS, "revision": 12}
    payload.update(changes)
    return artifact(**payload)


def jpeg(width=1440, height=900, color=(245, 245, 245)):
    output = io.BytesIO()
    Image.new("RGB", (width, height), color).save(output, "JPEG", quality=80)
    return output.getvalue()


class ScreenshotBundleContractTests(unittest.TestCase):
    def test_captured_screenshot_validates_real_jpeg_hash_and_dimensions(self):
        data = jpeg()
        evidence = ScreenshotEvidence(
            screenshot_id="desktop.closed",
            state=ScreenshotState.DESKTOP_CLOSED,
            sha256=hashlib.sha256(data).hexdigest(),
            mime_type="image/jpeg",
            byte_count=len(data),
            width=1440,
            height=900,
        )
        shot = CapturedScreenshot(evidence=evidence, data=data)
        self.assertEqual(shot.data, data)
        for broken in (b"not-a-jpeg", data + b"tampered"):
            with self.subTest(broken=broken[:10]), self.assertRaises(ValueError):
                CapturedScreenshot(evidence=evidence, data=broken)

    def test_report_requires_exact_six_screenshots_and_eight_layouts(self):
        with self.assertRaises(ValueError):
            BrowserAuditReport(screenshots=(), layouts=())


class BrowserAuditChromiumTests(unittest.IsolatedAsyncioTestCase):
    async def test_real_chromium_captures_six_jpegs_and_eight_layout_states(self):
        report = await BrowserAudit().audit(audit_artifact())

        self.assertEqual(
            [shot.evidence.state for shot in report.screenshots],
            list(ScreenshotState),
        )
        self.assertEqual([layout.state for layout in report.layouts], list(LayoutState))
        self.assertEqual(sum(len(shot.data) for shot in report.screenshots), report.total_bytes)
        self.assertLessEqual(report.total_bytes, 8_000_000)
        self.assertTrue(all(shot.data.startswith(b"\xff\xd8\xff") for shot in report.screenshots))
        self.assertEqual(
            [(shot.evidence.width, shot.evidence.height) for shot in report.screenshots],
            [(1920, 1080)] * 3 + [(390, 844)] * 3,
        )
        turn_two = [
            item for item in report.layouts if item.state.value.endswith("after_turn_2")
        ]
        self.assertTrue(all(item.transcript_roles == ("user", "assistant", "user", "assistant") for item in turn_two))
        self.assertTrue(all(not item.console_errors for item in report.layouts))
        self.assertTrue(all(not item.page_errors for item in report.layouts))
        self.assertTrue(all(not item.request_failures for item in report.layouts))
        open_layouts = [item for item in report.layouts if not item.state.value.endswith("closed")]
        self.assertTrue(all("launcher.aria-expanded=true" in item.aria_states for item in open_layouts))
        self.assertTrue(all("panel.aria-hidden=false" in item.aria_states for item in open_layouts))
        self.assertTrue(all("messages.role=log" in item.aria_states for item in open_layouts))
        self.assertTrue(all("messages.aria-live=polite" in item.aria_states for item in open_layouts))
        self.assertTrue(
            all(
                "composer.aria-busy=false" in item.aria_states
                for item in open_layouts
                if "after_turn" in item.state.value
            )
        )
        self.assertTrue(
            all(
                {
                    "composer.aria-busy=false",
                    "composer.aria-busy=missing",
                }.intersection(item.aria_states)
                for item in open_layouts
                if item.state.value.endswith("open_initial")
            )
        )
        desktop_open = [item for item in open_layouts if item.state.value.startswith("desktop")]
        self.assertTrue(all("textarea" in item.active_element for item in desktop_open))
        mobile_initial = next(
            item for item in report.layouts if item.state is LayoutState.MOBILE_OPEN_INITIAL
        )
        self.assertNotIn("textarea", mobile_initial.active_element)
        mobile_after_turn = [
            item for item in open_layouts if item.state.value.startswith("mobile.after_turn")
        ]
        self.assertTrue(all("textarea" in item.active_element for item in mobile_after_turn))

    async def test_runtime_supports_generated_panel_data_open_state(self):
        data_open_css = AUDIT_CSS.replace(
            '.kaigo-preview-open [data-region="panel"]',
            '[data-region="panel"][data-open]',
        )

        report = await BrowserAudit().audit(audit_artifact(css=data_open_css))

        self.assertEqual(len(report.screenshots), 6)
        self.assertEqual(len(report.layouts), 8)

    async def test_valid_desktop_panel_width_is_not_hardcoded(self):
        for width in (336, 432):
            with self.subTest(width=width):
                candidate = audit_artifact(
                    css=AUDIT_CSS.replace(
                        "width: 372px; height: 304px",
                        f"width: {width}px; height: 304px",
                    )
                )

                report = await BrowserAudit().audit(candidate)

                self.assertEqual(len(report.layouts), 8)

    async def test_audit_runs_the_artifact_javascript_in_every_state(self):
        class InspectingAudit(BrowserAudit):
            def __init__(self):
                super().__init__()
                self.generated_markers = []

            async def _capture(self, page, state):
                captured = await super()._capture(page, state)
                self.generated_markers.append(
                    await page.frames[1].evaluate(
                        "document.querySelector('[data-region=root]')"
                        ".dataset.generated"
                    )
                )
                return captured

        audit = InspectingAudit()
        report = await audit.audit(
            audit_artifact(
                javascript=(
                    "document.querySelector('[data-region=root]')"
                    ".dataset.generated='yes'"
                )
            )
        )

        self.assertEqual(len(report.screenshots), 6)
        self.assertEqual(audit.generated_markers, ["yes"] * 6)

    async def test_mobile_panel_geometry_cannot_leak_into_narrow_desktop(self):
        overbroad_mobile_css = AUDIT_CSS + """
        @media (min-width: 601px) and (max-width: 899px) {
          .kaigo { right: 12px; bottom: 12px; left: 12px; }
          [data-region="panel"] {
            width: calc(100vw - 24px);
          }
        }
        """

        with self.assertRaises(BrowserAuditError) as caught:
            await BrowserAudit().audit(audit_artifact(css=overbroad_mobile_css))

        self.assertIn("compact desktop bound", str(caught.exception))

    async def test_hidden_panel_cannot_intercept_the_closed_launcher(self):
        intercepting_css = AUDIT_CSS + """
        [data-region="launcher"] { position: fixed; right: 20px; bottom: 20px; z-index: 1; }
        [data-region="panel"] {
          position: fixed;
          right: 20px;
          bottom: 20px;
          display: grid;
          grid-template-rows: 52px 1fr 48px 60px;
          opacity: 0;
          pointer-events: auto;
          z-index: 2;
        }
        .kaigo-preview-open [data-region="panel"] { opacity: 1; }
        @media (max-width: 600px) {
          [data-region="launcher"] { right: 12px; bottom: 12px; }
          [data-region="panel"] {
            left: 12px;
            right: 12px;
            bottom: 12px;
            width: calc(100vw - 24px);
          }
        }
        """

        with self.assertRaises(BrowserAuditError) as caught:
            await BrowserAudit().audit(audit_artifact(css=intercepting_css))

        self.assertTrue(caught.exception.failures)
        self.assertIn("pointer-events:none", caught.exception.failures[0])
        self.assertIn("intercepts pointer events", caught.exception.diagnostic)

    async def test_content_box_panel_is_allowed_when_computed_geometry_fits(self):
        content_box_css = AUDIT_CSS.replace(
            "* { box-sizing: border-box; }",
            ".kaigo { box-sizing: border-box; }\n"
            "button, textarea { box-sizing: border-box; }",
            1,
        )

        report = await BrowserAudit().audit(audit_artifact(css=content_box_css))

        self.assertEqual(len(report.screenshots), 6)
        self.assertEqual(len(report.layouts), 8)

    async def test_motion_is_disabled_before_every_evidence_capture(self):
        class InspectingAudit(BrowserAudit):
            def __init__(self):
                super().__init__()
                self.animation_counts = []

            async def _capture(self, page, state):
                captured = await super()._capture(page, state)
                self.animation_counts.append(
                    await page.frames[1].evaluate("document.getAnimations().length")
                )
                return captured

        animated = audit_artifact(
            css=AUDIT_CSS
            + "\n[data-region=panel] { transition: opacity 10s linear; opacity: 0; }"
            + "\n.kaigo-preview-open [data-region=panel] { opacity: 1; }"
        )
        audit = InspectingAudit()
        await audit.audit(animated)
        self.assertEqual(audit.animation_counts, [0] * 6)

    async def test_keyframe_driven_open_state_is_frozen_at_its_final_frame(self):
        keyframe_css = AUDIT_CSS + """
        @keyframes panel-in {
          from { opacity: 0; transform: translateY(10px); }
          to { opacity: 1; transform: translateY(0); }
        }
        .kaigo { position: static; }
        [data-region="launcher"] { position: fixed; right: 20px; bottom: 20px; }
        [data-region="panel"] {
          position: fixed;
          right: 20px;
          bottom: 20px;
          display: grid;
          grid-template-rows: 52px 1fr 48px 60px;
          visibility: hidden;
          opacity: 0;
          transform: translateY(10px);
        }
        .kaigo-preview-open [data-region="panel"] {
          visibility: visible;
          animation: panel-in 250ms ease-out forwards;
        }
        @media (max-width: 600px) {
          [data-region="launcher"] { right: 12px; bottom: 12px; }
          [data-region="panel"] {
            left: 12px;
            right: 12px;
            bottom: 12px;
            width: calc(100vw - 24px);
          }
        }
        """

        report = await BrowserAudit().audit(audit_artifact(css=keyframe_css))

        open_layouts = [
            item for item in report.layouts if not item.state.value.endswith("closed")
        ]
        self.assertTrue(all(item.panel_inside_viewport for item in open_layouts))

    async def test_late_waapi_animation_is_settled_for_every_capture(self):
        class InspectingAudit(BrowserAudit):
            def __init__(self):
                super().__init__()
                self.animation_counts = []

            async def _capture(self, page, state):
                captured = await super()._capture(page, state)
                self.animation_counts.append(
                    await page.frames[1].evaluate("document.getAnimations().length")
                )
                return captured

        script = (
            "setTimeout(() => document.querySelector('[data-region=panel]').animate("
            "[{opacity:.2},{opacity:1}],{duration:10000,iterations:Infinity}), 100);"
        )
        late_animation = audit_artifact(
            body_html=AUDIT_HTML.replace(
                "</section>\n", f"<script>{script}</script></section>\n", 1
            )
        )
        audit = InspectingAudit()
        await audit.audit(late_animation)
        self.assertEqual(audit.animation_counts, [0] * 6)

    async def test_saved_original_animate_with_recurring_motion_fails_gate(self):
        script = """
          const savedAnimate = Element.prototype.animate;
          setInterval(() => savedAnimate.call(
            document.querySelector('[data-region=panel]'),
            [{opacity:.2},{opacity:1}],
            {duration:10000,iterations:Infinity}
          ), 5);
        """
        hostile = audit_artifact(
            body_html=AUDIT_HTML.replace(
                "</section>\n", f"<script>{script}</script></section>\n", 1
            )
        )
        with self.assertRaises(BrowserAuditError) as caught:
            await BrowserAudit().audit(hostile)
        self.assertEqual(caught.exception.error_code, "browser_gate_failed")

    async def test_recurring_direct_style_mutation_fails_visual_stability_gate(self):
        script = """
          const panel = document.querySelector('[data-region=panel]');
          let flip = false;
          setInterval(() => {
            flip = !flip;
            panel.style.background = flip ? 'rgb(245, 245, 245)' : 'rgb(210, 40, 40)';
          }, 5);
        """
        unstable = audit_artifact(
            body_html=AUDIT_HTML.replace(
                "</section>\n", f"<script>{script}</script></section>\n", 1
            )
        )
        with self.assertRaises(BrowserAuditError) as caught:
            await BrowserAudit().audit(unstable)
        self.assertEqual(caught.exception.error_code, "browser_gate_failed")

    async def test_transient_visual_mutation_between_equal_frames_fails_capture(self):
        image = jpeg()

        class Frame:
            def __init__(self):
                self.calls = 0

            async def evaluate(self, _script, *_args):
                self.calls += 1
                if self.calls == 3:
                    return {"mutations": 2, "changed": False, "active": 0}
                return None

        class Page:
            def __init__(self):
                self.frames = [object(), Frame()]

            async def screenshot(self, **_kwargs):
                return image

            async def wait_for_timeout(self, _milliseconds):
                return None

            async def evaluate(self, _script):
                return [1440, 900]

        with self.assertRaisesRegex(ValueError, "visual evidence changed"):
            await BrowserAudit()._capture(
                Page(), ScreenshotState.DESKTOP_CLOSED
            )

    async def test_send_and_close_controls_must_be_really_clickable(self):
        blocked = audit_artifact(
            css=(
                AUDIT_CSS
                + "\n[data-action=send], [data-action=close] { pointer-events: none !important; }"
            )
        )
        with self.assertRaises(BrowserAuditError) as caught:
            await BrowserAudit(timeout_ms=500, total_timeout_seconds=60).audit(blocked)
        self.assertEqual(caught.exception.error_code, "browser_gate_failed")
        self.assertIn("pointer events", caught.exception.diagnostic or "")

    async def test_visible_suggestions_must_be_pointer_interactable(self):
        blocked = audit_artifact(
            css=AUDIT_CSS + "\n[data-suggestion] { pointer-events: none !important; }"
        )
        with self.assertRaises(BrowserAuditError) as caught:
            await BrowserAudit().audit(blocked)
        self.assertEqual(caught.exception.error_code, "browser_gate_failed")

    async def test_each_send_requires_real_pending_busy_and_disabled_state(self):
        def without_pending_busy(candidate, *, channel_id):
            return build_preview_document(candidate, channel_id=channel_id).replace(
                "setBusy(true);", "setBusy(false);"
            )

        with patch(
            "builder_lab.browser_audit.build_preview_document",
            side_effect=without_pending_busy,
        ):
            with self.assertRaises(BrowserAuditError) as caught:
                await BrowserAudit().audit(audit_artifact())
        self.assertEqual(caught.exception.error_code, "browser_gate_failed")
        self.assertIn("pending", caught.exception.diagnostic or "")

    async def test_retry_must_not_duplicate_the_original_user_turn(self):
        def duplicate_retry(candidate, *, channel_id):
            return build_preview_document(candidate, channel_id=channel_id).replace(
                "retry.addEventListener('click', () => postPending());",
                "retry.addEventListener('click', () => { appendMessage('user', pending.text); postPending(); });",
            )

        with patch(
            "builder_lab.browser_audit.build_preview_document",
            side_effect=duplicate_retry,
        ):
            with self.assertRaises(BrowserAuditError) as caught:
                await BrowserAudit().audit(audit_artifact())
        self.assertEqual(caught.exception.error_code, "browser_gate_failed")
        self.assertIn("retry", caught.exception.diagnostic or "")

    async def test_each_user_action_emits_exactly_one_chat_request(self):
        def duplicate_second_send(candidate, *, channel_id):
            return build_preview_document(candidate, channel_id=channel_id).replace(
                "appendMessage('user', normalized);\n    postPending();",
                "appendMessage('user', normalized);\n    postPending();\n"
                "    if (normalized === 'Как начать проект?') postPending();",
            )

        def duplicate_retry_request(candidate, *, channel_id):
            return build_preview_document(candidate, channel_id=channel_id).replace(
                "retry.addEventListener('click', () => postPending());",
                "retry.addEventListener('click', () => { postPending(); postPending(); });",
            )

        def delayed_duplicate_second_send(candidate, *, channel_id):
            return build_preview_document(candidate, channel_id=channel_id).replace(
                "appendMessage('user', normalized);\n    postPending();",
                "appendMessage('user', normalized);\n    postPending();\n"
                "    if (normalized === 'Как начать проект?') {\n"
                "      const duplicateRequestId = pending.requestId;\n"
                "      setTimeout(() => window.parent.postMessage({source:'kaigo-builder-preview',"
                "version:2,channel_id:channelId,type:'chat.request',request_id:duplicateRequestId,"
                "revision,text:normalized}, '*'), 2000);\n"
                "    }",
            )

        def delayed_duplicate_retry(candidate, *, channel_id):
            return build_preview_document(candidate, channel_id=channel_id).replace(
                "retry.addEventListener('click', () => postPending());",
                "retry.addEventListener('click', () => { const duplicateRequestId=pending.requestId; "
                "const duplicateText=pending.text; postPending(); "
                "setTimeout(() => window.parent.postMessage({source:'kaigo-builder-preview',"
                "version:2,channel_id:channelId,type:'chat.request',request_id:duplicateRequestId,"
                "revision,text:duplicateText}, '*'), 2000); });",
            )

        def abort_signal_duplicate_retry(candidate, *, channel_id):
            return build_preview_document(candidate, channel_id=channel_id).replace(
                "retry.addEventListener('click', () => postPending());",
                "retry.addEventListener('click', () => { const duplicateRequestId=pending.requestId; "
                "const duplicateText=pending.text; postPending(); "
                "AbortSignal.timeout(2000).addEventListener('abort', () => "
                "window.parent.postMessage({source:'kaigo-builder-preview',version:2,"
                "channel_id:channelId,type:'chat.request',request_id:duplicateRequestId,"
                "revision,text:duplicateText}, '*')); });",
            )

        def scheduler_duplicate_retry(candidate, *, channel_id):
            return build_preview_document(candidate, channel_id=channel_id).replace(
                "retry.addEventListener('click', () => postPending());",
                "retry.addEventListener('click', () => { const duplicateRequestId=pending.requestId; "
                "const duplicateText=pending.text; postPending(); "
                "scheduler.postTask(() => window.parent.postMessage({source:'kaigo-builder-preview',"
                "version:2,channel_id:channelId,type:'chat.request',request_id:duplicateRequestId,"
                "revision,text:duplicateText}, '*'), {delay:2000}); });",
            )

        def message_channel_duplicate_retry(candidate, *, channel_id):
            return build_preview_document(candidate, channel_id=channel_id).replace(
                "retry.addEventListener('click', () => postPending());",
                "retry.addEventListener('click', () => { const duplicateRequestId=pending.requestId; "
                "const duplicateText=pending.text; postPending(); const channel=new MessageChannel(); "
                "const deadline=performance.now()+15000; channel.port1.onmessage=() => { "
                "if (performance.now() < deadline) { channel.port2.postMessage(0); return; } "
                "window.parent.postMessage({source:'kaigo-builder-preview',version:2,"
                "channel_id:channelId,type:'chat.request',request_id:duplicateRequestId,"
                "revision,text:duplicateText}, '*'); }; channel.port2.postMessage(0); });",
            )

        for tamper in (
            duplicate_second_send,
            duplicate_retry_request,
            delayed_duplicate_second_send,
            delayed_duplicate_retry,
            abort_signal_duplicate_retry,
            scheduler_duplicate_retry,
            message_channel_duplicate_retry,
        ):
            with self.subTest(tamper=tamper.__name__), patch(
                "builder_lab.browser_audit.build_preview_document", side_effect=tamper
            ):
                with self.assertRaises(BrowserAuditError) as caught:
                    await BrowserAudit().audit(audit_artifact())
                self.assertEqual(
                    caught.exception.error_code,
                    (
                        "browser_runtime_failed"
                        if tamper is message_channel_duplicate_retry
                        else "browser_gate_failed"
                    ),
                )
                diagnostic = caught.exception.diagnostic or ""
                self.assertTrue(
                    "request" in diagnostic
                    or "MessageChannel" in diagnostic,
                    diagnostic,
                )

    async def test_additional_window_message_task_source_is_rejected(self):
        def same_window_post_message_duplicate_retry(candidate, *, channel_id):
            return build_preview_document(candidate, channel_id=channel_id).replace(
                "retry.addEventListener('click', () => postPending());",
                "retry.addEventListener('click', () => { const duplicateRequestId=pending.requestId; "
                "const duplicateText=pending.text; postPending(); const deadline=performance.now()+10000; "
                "const tick=event => { if (event.source !== window || event.data !== 'audit-tick') return; "
                "if (performance.now() < deadline) { window.postMessage('audit-tick', '*'); return; } "
                "window.parent.postMessage({source:'kaigo-builder-preview',version:2,"
                "channel_id:channelId,type:'chat.request',request_id:duplicateRequestId,"
                "revision,text:duplicateText}, '*'); }; window.addEventListener('message', tick); "
                "window.postMessage('audit-tick', '*'); });",
            )

        with patch(
            "builder_lab.browser_audit.build_preview_document",
            side_effect=same_window_post_message_duplicate_retry,
        ):
            with self.assertRaises(BrowserAuditError) as caught:
                await BrowserAudit().audit(audit_artifact())
        self.assertIn(
            caught.exception.error_code,
            {"browser_runtime_failed", "browser_gate_failed"},
        )
        self.assertIn("pending async tasks", caught.exception.diagnostic or "")

    async def test_final_ledger_rechecks_parent_after_pending_task_gate(self):
        class BoundaryFrame:
            def __init__(self, page):
                self.page = page

            async def evaluate(self, script):
                if "__kaigoAuditPendingAsync" not in script:
                    raise AssertionError(script)
                self.page.delivery_queued = True
                return {
                    "timeouts": 0,
                    "intervals": 0,
                    "animationFrames": 0,
                    "idleCallbacks": 0,
                    "abortSignals": 0,
                    "scheduledTasks": 0,
                    "violations": 0,
                }

        class BoundaryPage:
            def __init__(self):
                self.request_count = 2
                self.violation = False
                self.delivery_queued = False
                self.frames = [object(), BoundaryFrame(self)]

            async def wait_for_timeout(self, _milliseconds):
                return None

            async def evaluate(self, script):
                if self.delivery_queued:
                    self.delivery_queued = False
                    self.request_count = 3
                    self.violation = True
                if "__auditLedgerSealed = true" in script or "new Promise" in script:
                    return None
                if "requestCount:" in script:
                    return {
                        "requestCount": self.request_count,
                        "pendingReplies": 0,
                        "violation": self.violation,
                    }
                raise AssertionError(script)

        with self.assertRaisesRegex(ValueError, "final chat.request ledger"):
            await BrowserAudit._assert_final_chat_ledger(
                BoundaryPage(), expected_count=2
            )

    async def test_timer_cross_cancellation_does_not_leave_false_pending_work(self):
        script = """
          <script>
            const intervalHandle = setInterval(() => {}, 10000);
            clearTimeout(intervalHandle);
            const timeoutHandle = setTimeout(() => {}, 10000);
            clearInterval(timeoutHandle);
          </script>
        """
        healthy = audit_artifact(body_html=AUDIT_HTML + script)
        report = await BrowserAudit().audit(healthy)
        self.assertEqual(len(report.screenshots), 6)
        self.assertEqual(len(report.layouts), 8)

    async def test_retry_must_preserve_exact_assistant_response_text(self):
        def corrupt_retry_answer(candidate, *, channel_id):
            return build_preview_document(candidate, channel_id=channel_id).replace(
                "appendMessage('assistant', data.text.trim());",
                "appendMessage('assistant', pending.text === 'Проверка повтора' "
                "? 'CORRUPTED RETRY ANSWER' : data.text.trim());",
            )

        with patch(
            "builder_lab.browser_audit.build_preview_document",
            side_effect=corrupt_retry_answer,
        ):
            with self.assertRaises(BrowserAuditError) as caught:
                await BrowserAudit().audit(audit_artifact())
        self.assertEqual(caught.exception.error_code, "browser_gate_failed")
        self.assertIn("retry", caught.exception.diagnostic or "")

    async def test_retry_touch_target_uses_ancestor_clipped_visible_rect(self):
        cases = (
            audit_artifact(
                css=(
                    AUDIT_CSS
                    + "\n[data-kaigo-runtime-status]{height:8px!important;overflow:hidden!important;"
                    "position:relative!important}"
                    + "\n[data-kaigo-runtime-retry]{width:44px!important;height:44px!important;"
                    "transform:translateY(-18px)!important}"
                )
            ),
            audit_artifact(
                css=(
                    AUDIT_CSS
                    + "\n[data-region=messages]:has([data-kaigo-runtime-retry=true]){"
                    "overflow:visible!important}"
                    + "\n[data-kaigo-runtime-retry]{position:fixed!important;left:0!important;"
                    "top:0!important;width:44px!important;height:44px!important}"
                )
            ),
            audit_artifact(
                body_html=AUDIT_HTML.replace(
                    "</header>",
                    '<input class="retry-only" type="number" style="width:44px;height:44px">'
                    "</header>",
                ),
                css=(
                    AUDIT_CSS
                    + "\n.retry-only{display:none}"
                    + "\n[data-region=root]:has([data-kaigo-runtime-status=error]) "
                    ".retry-only{display:block}"
                ),
            ),
        )
        for broken in cases:
            with self.subTest(css=broken.css[-220:]):
                with self.assertRaises(BrowserAuditError) as caught:
                    await BrowserAudit().audit(broken)
                self.assertEqual(caught.exception.error_code, "browser_gate_failed")
                self.assertIn("retry", caught.exception.diagnostic or "")
                self.assertTrue(caught.exception.failures)

    async def test_retry_size_failure_explains_the_actual_runtime_ancestry(self):
        broken = audit_artifact(
            css=(
                AUDIT_CSS
                + '\n[data-kaigo-runtime-retry="true"]{width:20px;height:20px;'
                "min-width:0;min-height:0}"
                + "\n.kaigo-widget__message--error "
                '[data-kaigo-runtime-retry="true"]{min-width:44px;min-height:44px}'
            )
        )

        with self.assertRaises(BrowserAuditError) as caught:
            await BrowserAudit().audit(broken)

        diagnostic = caught.exception.diagnostic or ""
        self.assertIn('.kaigo-widget [data-kaigo-runtime-retry="true"]', diagnostic)
        self.assertIn("do not require .kaigo-widget__message--error", diagnostic)

    async def test_partial_context_is_closed_when_route_setup_fails(self):
        class BrokenContext:
            def __init__(self):
                self.closed = False

            async def add_init_script(self, *_args):
                pass

            async def route(self, *_args):
                raise RuntimeError("route setup failed")

            async def close(self):
                self.closed = True

        class BrokenBrowser:
            def __init__(self):
                self.context = BrokenContext()

            async def new_context(self, **_kwargs):
                return self.context

        browser = BrokenBrowser()
        with self.assertRaises(RuntimeError):
            await BrowserAudit(browser=browser)._new_context(browser, 390, 844)
        self.assertTrue(browser.context.closed)

    async def test_bounded_cleanup_never_swallows_caller_cancellation(self):
        started = asyncio.Event()

        async def never_finishes():
            started.set()
            await asyncio.Event().wait()

        task = asyncio.create_task(
            BrowserAudit._bounded_cleanup(never_finishes(), timeout_seconds=10)
        )
        await started.wait()
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task

    async def test_bounded_cleanup_returns_even_if_child_suppresses_cancellation(self):
        finished = asyncio.Event()

        async def cancellation_resistant():
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                # A first cancellation is swallowed by a buggy transport.  The
                # bounded cleanup must deliver another cancellation rather than
                # detaching this coroutine as a live orphan.
                try:
                    await asyncio.Event().wait()
                finally:
                    finished.set()

        started = asyncio.get_running_loop().time()
        await asyncio.wait_for(
            BrowserAudit._bounded_cleanup(
                cancellation_resistant(), timeout_seconds=0.01
            ),
            timeout=0.12,
        )
        self.assertLess(asyncio.get_running_loop().time() - started, 0.12)
        self.assertTrue(finished.is_set())

    async def test_owned_cleanup_preserves_caller_cancellation_when_close_resists(self):
        close_started = asyncio.Event()
        stop_close = asyncio.Event()
        asyncio.get_running_loop().call_later(0.5, stop_close.set)

        class Browser:
            async def close(self):
                close_started.set()
                while not stop_close.is_set():
                    try:
                        await stop_close.wait()
                    except asyncio.CancelledError:
                        pass

        class Chromium:
            async def launch(self, **_kwargs):
                return Browser()

        class Playwright:
            chromium = Chromium()

            async def stop(self):
                return None

        class Starter:
            async def start(self):
                return Playwright()

        class EmptyAudit(BrowserAudit):
            async def _audit_viewport(self, **_kwargs):
                return [], []

        BrowserAudit._cleanup_orphans.clear()
        with patch("builder_lab.browser_audit.async_playwright", return_value=Starter()):
            task = asyncio.create_task(EmptyAudit().audit(audit_artifact()))
            await close_started.wait()
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await asyncio.wait_for(task, timeout=0.3)
        BrowserAudit._cleanup_orphans.clear()

    async def test_bounded_cleanup_propagates_completed_close_error(self):
        async def close_fails():
            raise RuntimeError("close failed")

        with self.assertRaisesRegex(RuntimeError, "close failed"):
            await BrowserAudit._bounded_cleanup(close_fails(), timeout_seconds=1)

    async def test_viewport_and_retry_contexts_use_bounded_cleanup(self):
        class Context:
            async def close(self):
                return None

        class CleanupAudit(BrowserAudit):
            def __init__(self):
                super().__init__(browser=object())
                self.cleanup_calls = 0

            async def _new_context(self, *_args, **_kwargs):
                return Context(), object(), object()

            async def _mount(self, *_args, **_kwargs):
                raise ValueError("stop after context creation")

            async def _bounded_cleanup(self, awaitable, timeout_seconds=5):
                self.cleanup_calls += 1
                await awaitable

        audit = CleanupAudit()
        with self.assertRaises(ValueError):
            await audit._audit_viewport(
                browser=object(), artifact=audit_artifact(), prefix="mobile", width=390, height=844
            )
        with self.assertRaises(ValueError):
            await audit._retry_probe(browser=object(), artifact=audit_artifact())
        self.assertEqual(audit.cleanup_calls, 2)

    async def test_total_timeout_includes_startup_and_runs_nested_cleanup(self):
        close_started = asyncio.Event()
        close_cancelled = False
        playwright_stopped = False

        class FakeBrowser:
            async def close(self):
                nonlocal close_cancelled
                close_started.set()
                try:
                    await asyncio.Event().wait()
                except asyncio.CancelledError:
                    close_cancelled = True
                    raise

        class FakePlaywright:
            def __init__(self):
                self.chromium = SimpleNamespace(launch=self.launch)

            async def launch(self, **_kwargs):
                return FakeBrowser()

            async def stop(self):
                nonlocal playwright_stopped
                playwright_stopped = True

        class FailingAudit(BrowserAudit):
            async def _audit_viewport(self, **_kwargs):
                raise ValueError("stop before viewport work")

        fake_playwright = FakePlaywright()
        starter = SimpleNamespace(start=self._async_value(fake_playwright))
        with patch("builder_lab.browser_audit.async_playwright", return_value=starter):
            with self.assertRaises(BrowserAuditError) as caught:
                await FailingAudit(total_timeout_seconds=0.05).audit(audit_artifact())
        self.assertEqual(caught.exception.error_code, "browser_gate_failed")
        self.assertTrue(close_started.is_set())
        self.assertTrue(close_cancelled)
        self.assertTrue(playwright_stopped)

    async def test_expired_deadline_does_not_grant_context_cleanup_five_more_seconds(self):
        close_cancelled = False

        class HangingContext:
            async def close(self):
                nonlocal close_cancelled
                try:
                    await asyncio.Event().wait()
                except asyncio.CancelledError:
                    close_cancelled = True
                    raise

        class DeadlineAudit(BrowserAudit):
            def __init__(self):
                super().__init__(browser=object(), total_timeout_seconds=0.05)

            async def _new_context(self, *_args, **_kwargs):
                return HangingContext(), object(), object()

            async def _mount(self, *_args, **_kwargs):
                await asyncio.Event().wait()

        started = asyncio.get_running_loop().time()
        with self.assertRaises(BrowserAuditError) as caught:
            await asyncio.wait_for(DeadlineAudit().audit(audit_artifact()), timeout=0.30)
        elapsed = asyncio.get_running_loop().time() - started
        self.assertEqual(caught.exception.error_code, "browser_gate_failed")
        self.assertTrue(close_cancelled)
        self.assertLess(elapsed, 0.20)

    async def test_total_deadline_reserves_time_for_context_teardown(self):
        close_completed = False

        class SlowContext:
            async def close(self):
                nonlocal close_completed
                await asyncio.sleep(0.01)
                close_completed = True

        class ReservedCleanupAudit(BrowserAudit):
            def __init__(self):
                super().__init__(browser=object(), total_timeout_seconds=0.08)

            async def _new_context(self, *_args, **_kwargs):
                return SlowContext(), object(), object()

            async def _mount(self, *_args, **_kwargs):
                await asyncio.Event().wait()

        started = asyncio.get_running_loop().time()
        with self.assertRaises(BrowserAuditError) as caught:
            await asyncio.wait_for(ReservedCleanupAudit().audit(audit_artifact()), timeout=0.20)

        self.assertEqual(caught.exception.error_code, "browser_gate_failed")
        self.assertTrue(close_completed)
        self.assertLess(asyncio.get_running_loop().time() - started, 0.15)

    @staticmethod
    def _async_value(value):
        async def get_value():
            return value

        return get_value

    async def test_unexpected_network_is_fail_closed(self):
        hostile = audit_artifact(
            body_html=AUDIT_HTML.replace(
                "<p>Задайте короткий вопрос.</p>",
                '<img src="https://example.com/tracker.png" alt="">',
            )
        )
        with self.assertRaises(BrowserAuditError) as caught:
            await BrowserAudit().audit(hostile)
        self.assertEqual(caught.exception.error_code, "unexpected_network")

    async def test_bad_target_geometry_fails_deterministic_gate(self):
        broken = audit_artifact(css=AUDIT_CSS + "\n[data-action=send] { min-height: 0; height: 12px; }")
        with self.assertRaises(BrowserAuditError) as caught:
            await BrowserAudit().audit(broken)
        self.assertEqual(caught.exception.error_code, "browser_gate_failed")
        self.assertIn("44", str(caught.exception))
        self.assertTrue(caught.exception.failures)
        self.assertTrue(any("44" in failure for failure in caught.exception.failures))

    async def test_ancestor_overflow_clipping_reduces_effective_touch_target(self):
        cases = (
            AUDIT_CSS
            + "\n[data-region=suggestions]{height:10px!important;overflow:hidden!important}"
            + "\n[data-region=suggestions] button{height:44px!important}",
            AUDIT_CSS
            + "\n[data-region=suggestions]{height:58px!important;overflow:hidden!important;"
            + "border-bottom:20px solid #111!important;align-self:start!important}"
            + "\n[data-region=suggestions] button{height:44px!important}",
        )
        for css in cases:
            with self.subTest(css=css[-180:]):
                clipped = audit_artifact(css=css)
                with self.assertRaises(BrowserAuditError) as caught:
                    await BrowserAudit().audit(clipped)
                self.assertEqual(caught.exception.error_code, "browser_gate_failed")

    async def test_launcher_touch_target_and_required_close_send_are_gated(self):
        cases = (
            audit_artifact(
                css=(
                    AUDIT_CSS
                    + "\n[data-region=launcher] { min-width: 0 !important; "
                    "min-height: 0 !important; width: 14px; height: 10px; }"
                )
            ),
            audit_artifact(body_html=AUDIT_HTML.replace('<button data-action="close" aria-label="Закрыть">×</button>', "")),
            audit_artifact(body_html=AUDIT_HTML.replace('<button data-action="send">Отправить</button>', "")),
        )
        for broken in cases:
            with self.subTest(body=broken.body_html[-80:], css=broken.css[-80:]):
                with self.assertRaises(BrowserAuditError) as caught:
                    await BrowserAudit().audit(broken)
                self.assertEqual(caught.exception.error_code, "browser_gate_failed")

    async def test_close_outside_viewport_is_a_structured_browser_gate_failure(self):
        broken = audit_artifact(
            css=(
                AUDIT_CSS
                + "\n[data-action=close]{position:fixed!important;top:-100px!important;"
                "left:0!important;width:80px!important;height:44px!important}"
            )
        )

        with self.assertRaises(BrowserAuditError) as caught:
            await BrowserAudit().audit(broken)

        self.assertEqual(caught.exception.error_code, "browser_gate_failed")
        self.assertTrue(caught.exception.failures)
        self.assertIn("close control is outside the viewport", caught.exception.failures[0])

    async def test_panel_outside_viewport_failure_contains_repair_geometry(self):
        healthy = await BrowserAudit().audit(audit_artifact())
        layouts = list(healthy.layouts)
        index = next(
            i
            for i, layout in enumerate(layouts)
            if layout.state is LayoutState.DESKTOP_OPEN_INITIAL
        )
        layout = layouts[index]
        layouts[index] = replace(
            layout,
            panel_inside_viewport=False,
            regions=tuple(
                replace(region, x=-380.0, width=1800.0)
                if region.region == "panel"
                else region
                for region in layout.regions
            ),
        )
        broken = replace(healthy, layouts=tuple(layouts))

        with self.assertRaises(BrowserAuditError) as caught:
            BrowserAudit()._assert_release_gate(broken)

        diagnostic = caught.exception.diagnostic or ""
        self.assertIn("panel rect=(x=", diagnostic)
        self.assertIn("viewport=1440x900", diagnostic)
        self.assertIn("position: fixed", diagnostic)
        self.assertIn("right/bottom offsets", diagnostic)

    async def test_launcher_must_have_semantics_and_real_keyboard_activation(self):
        keyboard_dead_launcher = audit_artifact(
            body_html=AUDIT_HTML.replace(
                '<button data-region="launcher" aria-label="Открыть RAW AI">RAW AI</button>',
                '<div data-region="launcher" tabindex="0" aria-label="Открыть RAW AI">RAW AI</div>',
            )
        )
        with self.assertRaises(BrowserAuditError) as caught:
            await BrowserAudit().audit(keyboard_dead_launcher)
        self.assertEqual(caught.exception.error_code, "browser_gate_failed")
        self.assertIn("keyboard", caught.exception.diagnostic or "")

    async def test_mobile_open_rejects_widget_driven_composer_autofocus(self):
        autofocus_script = """
        <script>
          const autofocusRoot = document.querySelector('[data-region="root"]');
          const autofocusInput = document.querySelector('[data-region="composer"] textarea');
          new MutationObserver(() => {
            if (autofocusRoot.dataset.state === 'open') autofocusInput.focus();
          }).observe(autofocusRoot, {attributes: true, attributeFilter: ['data-state']});
        </script>
        """
        broken = audit_artifact(body_html=AUDIT_HTML + autofocus_script)

        with self.assertRaises(BrowserAuditError) as caught:
            await BrowserAudit().audit(broken)

        self.assertEqual(caught.exception.error_code, "browser_gate_failed")
        self.assertIn("auto-focus", caught.exception.diagnostic or "")

    async def test_two_suggestions_are_allowed_when_they_fit(self):
        two_suggestions = audit_artifact(
            body_html=AUDIT_HTML.replace(
                '</nav>',
                '<button data-suggestion="Стоимость">Стоимость</button></nav>',
            )
        )

        report = await BrowserAudit().audit(two_suggestions)

        self.assertEqual(len(report.layouts), 8)

    async def test_subpixel_touch_target_rounding_within_half_pixel_is_tolerated(self):
        subpixel = audit_artifact(
            css=AUDIT_CSS
            + "\nbutton, textarea { min-height:43.75px!important; }"
            + "\n[data-region=launcher] { height:46px!important; }"
        )

        report = await BrowserAudit().audit(subpixel)

        self.assertEqual(len(report.layouts), 8)

    async def test_root_with_fixed_sized_children_need_not_have_its_own_box(self):
        zero_box_root = audit_artifact(
            css=AUDIT_CSS
            + """
.kaigo { position: static; width: 0; height: 0; }
[data-region="launcher"], [data-region="panel"] {
  position: fixed;
  right: 20px;
  bottom: 20px;
}
@media (max-width: 600px) {
  [data-region="launcher"], [data-region="panel"] {
    right: 12px;
    bottom: 12px;
  }
}
"""
        )

        report = await BrowserAudit().audit(zero_box_root)

        self.assertEqual(len(report.screenshots), 6)

    async def test_content_driven_panel_heights_within_caps_pass(self):
        dynamic_height = audit_artifact(
            css=AUDIT_CSS
            + """
[data-region="panel"] { height: 400px; }
@media (max-width: 600px) {
  [data-region="panel"] { height: 420px; }
}
"""
        )

        report = await BrowserAudit().audit(dynamic_height)

        self.assertEqual(len(report.screenshots), 6)

    async def test_native_label_actions_are_counted_and_touch_target_gated(self):
        extra_actions = (
            '<span role="link" tabindex="0" '
            'onclick="this.dataset.pointerActivated=\'true\'" '
            'onkeydown="if(event.key===\'Enter\')this.dataset.keyboardActivated=\'true\'" '
            'style="display:flex;width:44px;height:44px">A</span>'
            '<span role="link" tabindex="0" '
            'onclick="this.dataset.pointerActivated=\'true\'" '
            'onkeydown="if(event.key===\'Enter\')this.dataset.keyboardActivated=\'true\'" '
            'style="display:flex;width:44px;height:44px">B</span>'
            '<span role="link" tabindex="0" '
            'onclick="this.dataset.pointerActivated=\'true\'" '
            'onkeydown="if(event.key===\'Enter\')this.dataset.keyboardActivated=\'true\'" '
            'style="display:flex;width:44px;height:44px">C</span>'
            '<span role="link" tabindex="0" '
            'onclick="this.dataset.pointerActivated=\'true\'" '
            'onkeydown="if(event.key===\'Enter\')this.dataset.keyboardActivated=\'true\'" '
            'style="display:flex;width:44px;height:44px">D</span>'
        )
        too_many = audit_artifact(
            body_html=AUDIT_HTML.replace(
                "</header>", extra_actions + "</header>"
            ),
            css=AUDIT_CSS + "\n[data-region=suggestions] button{display:none!important}",
        )
        tiny_label = audit_artifact(
            body_html=AUDIT_HTML.replace(
                "</header>",
                '<input id="tiny-choice" type="checkbox" hidden>'
                '<label for="tiny-choice" style="display:block;width:12px;height:12px">T</label>'
                "</header>",
            ),
            css=AUDIT_CSS + "\n[data-region=suggestions] button{display:none!important}",
        )
        tiny_wrapping_label = audit_artifact(
            body_html=AUDIT_HTML.replace(
                "</header>",
                '<label style="display:flex;width:12px;height:12px">'
                '<input type="checkbox" hidden>T</label>'
                "</header>",
            ),
            css=AUDIT_CSS + "\n[data-region=suggestions] button{display:none!important}",
        )

        report = await BrowserAudit().audit(too_many)
        self.assertEqual(len(report.layouts), 8)

        for broken, expected in (
            (tiny_label, "44"),
            (tiny_wrapping_label, "44"),
        ):
            with self.subTest(expected=expected):
                with self.assertRaises(BrowserAuditError) as caught:
                    await BrowserAudit().audit(broken)
                self.assertEqual(caught.exception.error_code, "browser_gate_failed")
                self.assertIn(expected, caught.exception.diagnostic or "")

    async def test_visually_hidden_textarea_label_uses_visible_control_target(self):
        labelled_textarea = audit_artifact(
            body_html=AUDIT_HTML.replace(
                '<textarea aria-label="Сообщение"></textarea>',
                '<label for="message" style="position:absolute;width:1px;height:1px;overflow:hidden">'
                'Сообщение</label><textarea id="message"></textarea>',
            )
        )

        report = await BrowserAudit().audit(labelled_textarea)

        self.assertEqual(len(report.screenshots), 6)

    async def test_aria_action_requires_focus_pointer_and_keyboard_activation(self):
        broken = audit_artifact(
            body_html=AUDIT_HTML.replace(
                "</header>",
                '<div class="dead-aria-action" role="button" '
                'style="display:flex;width:44px;height:44px">Help</div>'
                "</header>",
            ),
            css=(
                AUDIT_CSS
                + "\n[data-region=suggestions] button{display:none!important}"
                + "\n[data-region=root]:has([data-kaigo-runtime-status=error]) "
                ".dead-aria-action{display:none!important}"
            ),
        )

        with self.assertRaises(BrowserAuditError) as caught:
            await BrowserAudit().audit(broken)

        self.assertEqual(caught.exception.error_code, "browser_gate_failed")
        self.assertIn("ARIA action", caught.exception.diagnostic or "")

    async def test_generic_native_button_requires_activation_behavior(self):
        broken = audit_artifact(
            body_html=AUDIT_HTML.replace(
                "</header>",
                '<button class="dead-native-action" type="button" '
                'style="width:44px;height:44px">Help</button>'
                "</header>",
            ),
            css=(
                AUDIT_CSS
                + "\n[data-region=suggestions] button{display:none!important}"
                + "\n[data-region=root]:has([data-kaigo-runtime-status=error]) "
                ".dead-native-action{display:none!important}"
            ),
        )

        with self.assertRaises(BrowserAuditError) as caught:
            await BrowserAudit().audit(broken)

        self.assertEqual(caught.exception.error_code, "browser_gate_failed")
        self.assertIn("activation behavior", caught.exception.diagnostic or "")

    async def test_noop_native_button_handler_is_not_activation_behavior(self):
        broken = audit_artifact(
            body_html=AUDIT_HTML.replace(
                "</header>",
                '<button class="noop-native-action" type="button" onclick="void 0" '
                'style="width:44px;height:44px">Help</button>'
                "</header>",
            ),
            css=(
                AUDIT_CSS
                + "\n[data-region=suggestions] button{display:none!important}"
                + "\n[data-region=root]:has([data-kaigo-runtime-status=error]) "
                ".noop-native-action{display:none!important}"
            ),
        )

        with self.assertRaises(BrowserAuditError) as caught:
            await BrowserAudit().audit(broken)

        self.assertEqual(caught.exception.error_code, "browser_gate_failed")
        self.assertIn("activation behavior", caught.exception.diagnostic or "")

    async def test_delayed_focus_telemetry_is_not_activation_behavior(self):
        class ControlTrackingAudit(BrowserAudit):
            def __init__(self):
                super().__init__()
                self.control_calls = 0

            async def _probe_action_control(self, **kwargs):
                self.control_calls += 1
                return await super()._probe_action_control(**kwargs)

        broken = audit_artifact(
            body_html=AUDIT_HTML.replace(
                "</header>",
                '<button class="focus-telemetry-action" type="button" onclick="void 0" '
                'style="width:44px;height:44px">Help</button>'
                "</header>",
            )
            + """
            <script>
              document.addEventListener('focusin', event => {
                if (!event.target.closest('.focus-telemetry-action')) return;
                const telemetry = setInterval(() => parent.postMessage({
                  source: 'kaigo-builder-preview',
                  version: 2,
                  type: 'focus.telemetry'
                }, '*'), 10);
                setTimeout(() => clearInterval(telemetry), 200);
              });
            </script>
            """,
            css=(
                AUDIT_CSS
                + "\n[data-region=suggestions] button{display:none!important}"
                + "\n[data-region=root]:has([data-kaigo-runtime-status=error]) "
                ".focus-telemetry-action{display:none!important}"
            ),
        )

        audit = ControlTrackingAudit()
        with self.assertRaises(BrowserAuditError) as caught:
            await audit.audit(broken)

        self.assertEqual(caught.exception.error_code, "browser_gate_failed")
        self.assertIn("activation behavior", caught.exception.diagnostic or "")
        self.assertGreaterEqual(audit.control_calls, 1)

    async def test_delegated_native_button_with_observable_effect_is_accepted(self):
        delegated = audit_artifact(
            body_html=AUDIT_HTML.replace(
                "</header>",
                '<button class="delegated-native-action" type="button" '
                'aria-pressed="false" style="width:44px;height:44px">Help</button>'
                "</header>",
            )
            + """
            <script>
              document.querySelector('[data-region="header"]').addEventListener('click', event => {
                const button = event.target.closest('.delegated-native-action');
                if (!button) return;
                const count = Number(button.dataset.activationCount || '0') + 1;
                button.dataset.activationCount = String(count);
                button.setAttribute('aria-pressed', count % 2 ? 'true' : 'false');
              });
            </script>
            """,
            css=(
                AUDIT_CSS
                + "\n[data-region=suggestions] button{display:none!important}"
                + "\n[data-region=root]:has([data-kaigo-runtime-status=error]) "
                ".delegated-native-action{display:none!important}"
            ),
        )

        report = await BrowserAudit().audit(delegated)

        self.assertEqual(len(report.screenshots), 6)
        self.assertEqual(len(report.layouts), 8)

    async def test_behavior_probe_uses_one_context_and_closes_each_modality_page(self):
        class LifecycleAudit(BrowserAudit):
            def __init__(self):
                super().__init__()
                self.inside_probe = False
                self.probe_contexts = []
                self.probe_pages = []

            async def _probe_generic_actions(self, **kwargs):
                self.inside_probe = True
                try:
                    return await super()._probe_generic_actions(**kwargs)
                finally:
                    self.inside_probe = False

            async def _new_context(self, *args, **kwargs):
                context, page, failures = await super()._new_context(*args, **kwargs)
                if self.inside_probe:
                    self.probe_contexts.append(context)
                    self.probe_pages.append(page)
                return context, page, failures

            async def _new_probe_page(self, *args, **kwargs):
                page = await super()._new_probe_page(*args, **kwargs)
                self.probe_pages.append(page)
                return page

        delegated = audit_artifact(
            body_html=AUDIT_HTML.replace(
                "</header>",
                '<button class="delegated-native-action desktop-action" type="button" '
                'aria-pressed="false" style="width:44px;height:44px">D</button>'
                '<button class="delegated-native-action mobile-action" type="button" '
                'aria-pressed="false" style="width:44px;height:44px">M</button>'
                "</header>",
            )
            + """
            <script>
              document.querySelector('[data-region="header"]').addEventListener('click', event => {
                const button = event.target.closest('.delegated-native-action');
                if (!button) return;
                button.dataset.activationCount = String(
                  Number(button.dataset.activationCount || '0') + 1
                );
              });
            </script>
            """,
            css=(
                AUDIT_CSS
                + "\n[data-region=suggestions] button{display:none!important}"
                + "\n.mobile-action{display:none}"
                + "\n@media(max-width:600px){.desktop-action{display:none}.mobile-action{display:block}}"
                + "\n[data-region=root]:has([data-kaigo-runtime-status=error]) "
                ".delegated-native-action{display:none!important}"
            ),
        )
        audit = LifecycleAudit()

        await audit.audit(delegated)

        self.assertEqual(len(audit.probe_contexts), 1)
        self.assertEqual(len(audit.probe_pages), 8)
        self.assertTrue(all(page.is_closed() for page in audit.probe_pages))

    async def test_generic_action_behavior_is_rechecked_for_each_viewport(self):
        desktop_only_behavior = audit_artifact(
            body_html=AUDIT_HTML.replace(
                "</header>",
                '<button class="responsive-native-action" type="button" '
                'aria-pressed="false" style="width:44px;height:44px">Help</button>'
                "</header>",
            )
            + """
            <script>
              document.querySelector('[data-region="header"]').addEventListener('click', event => {
                const button = event.target.closest('.responsive-native-action');
                if (!button || matchMedia('(max-width:600px)').matches) return;
                button.dataset.activationCount = String(
                  Number(button.dataset.activationCount || '0') + 1
                );
              });
            </script>
            """,
            css=(
                AUDIT_CSS
                + "\n[data-region=suggestions] button{display:none!important}"
                + "\n[data-region=root]:has([data-kaigo-runtime-status=error]) "
                ".responsive-native-action{display:none!important}"
            ),
        )

        with self.assertRaises(BrowserAuditError) as caught:
            await BrowserAudit().audit(desktop_only_behavior)

        self.assertEqual(caught.exception.error_code, "browser_gate_failed")
        self.assertIn("activation behavior", caught.exception.diagnostic or "")

    async def test_fully_covered_generic_control_is_not_treated_as_usable(self):
        covered = (
            '<div class="covered-wrapper" style="position:relative;width:44px;height:44px">'
            '<button class="covered-action" style="width:44px;height:44px">Help</button>'
            '<div style="position:absolute;inset:0;z-index:1;background:white"></div>'
            "</div>"
        )
        broken = audit_artifact(
            body_html=AUDIT_HTML.replace("</header>", covered + "</header>"),
            css=(
                AUDIT_CSS
                + "\n[data-region=suggestions] button{display:none!important}"
                + "\n[data-region=root]:has([data-kaigo-runtime-status=error]) "
                ".covered-wrapper{display:none!important}"
            ),
        )

        with self.assertRaises(BrowserAuditError) as caught:
            await BrowserAudit().audit(broken)

        self.assertEqual(caught.exception.error_code, "browser_gate_failed")
        self.assertIn("clipped", caught.exception.diagnostic or "")

    async def test_closed_state_also_gates_every_visible_interactive_target(self):
        broken = audit_artifact(
            body_html=(
                AUDIT_HTML
                + '<button class="rogue-closed-action" aria-label="rogue">x</button>'
            ),
            css=(
                AUDIT_CSS
                + "\n.rogue-closed-action{position:fixed;left:5px;top:5px;"
                "width:16px;height:10px;min-height:0}"
                + "\n.kaigo[data-state=open]~.rogue-closed-action{display:none}"
            ),
        )
        with self.assertRaises(BrowserAuditError) as caught:
            await BrowserAudit().audit(broken)
        self.assertEqual(caught.exception.error_code, "browser_gate_failed")

    async def test_closed_state_rejects_large_non_action_control_beside_launcher(self):
        cases = (
            ('<textarea class="rogue-closed-field">visible</textarea>', "100px", "44px"),
            ('<div class="rogue-closed-field" contenteditable="true">x</div>', "8px", "8px"),
        )
        for html, width, height in cases:
            broken = audit_artifact(
                body_html=(AUDIT_HTML + html),
                css=(
                    AUDIT_CSS
                    + f"\n.rogue-closed-field{{position:fixed;left:5px;top:5px;width:{width};height:{height}}}"
                    + "\n.kaigo[data-state=open]~.rogue-closed-field{display:none}"
                ),
            )
            with self.subTest(html=html):
                with self.assertRaises(BrowserAuditError) as caught:
                    await BrowserAudit().audit(broken)
                self.assertEqual(caught.exception.error_code, "browser_gate_failed")

    async def test_two_turn_history_must_move_with_a_real_wheel(self):
        broken = audit_artifact(css=AUDIT_CSS + "\n[data-region=messages]{overflow:hidden}")
        with self.assertRaises(BrowserAuditError) as caught:
            await BrowserAudit().audit(broken)
        self.assertEqual(caught.exception.error_code, "browser_gate_failed")

    async def test_two_turn_history_may_fit_without_forced_scroll_range(self):
        compressed = audit_artifact(
            css=(
                AUDIT_CSS
                + "\n[data-region=messages], [data-region=messages] * {"
                "font-size:1px!important;line-height:1px!important;margin:0!important;padding:0!important}"
            )
        )
        report = await BrowserAudit().audit(compressed)
        self.assertEqual(len(report.screenshots), 6)
        self.assertEqual(len(report.layouts), 8)

    async def test_close_reopen_must_preserve_exact_transcript_text(self):
        corrupt_history = """
          document.querySelector('[data-region="launcher"]').addEventListener('click', () => {
            requestAnimationFrame(() => {
              document.querySelectorAll('[data-kaigo-runtime-content]').forEach(
                (node, index) => { node.textContent = `CORRUPTED-${index}`; }
              );
            });
          });
        """
        hostile = audit_artifact(
            body_html=AUDIT_HTML.replace(
                "</section>\n", f"<script>{corrupt_history}</script></section>\n", 1
            )
        )
        with self.assertRaises(BrowserAuditError) as caught:
            await BrowserAudit().audit(hostile)
        self.assertEqual(caught.exception.error_code, "browser_gate_failed")

    async def test_required_region_clipping_and_internal_horizontal_overflow_are_gated(self):
        broken = audit_artifact(
            css=AUDIT_CSS
            + "\n[data-region=header] { width: 500px; overflow: visible; }"
        )
        with self.assertRaises(BrowserAuditError) as caught:
            await BrowserAudit().audit(broken)
        self.assertEqual(caught.exception.error_code, "browser_gate_failed")

    async def test_open_actions_must_remain_inside_panel(self):
        broken = audit_artifact(
            css=AUDIT_CSS
            + "\n[data-action=send] { position: fixed; left: 0; top: 0; width: 80px; height: 44px; }"
        )
        with self.assertRaises(BrowserAuditError) as caught:
            await BrowserAudit().audit(broken)
        self.assertEqual(caught.exception.error_code, "browser_gate_failed")
        self.assertIn("inside the panel", str(caught.exception))

    async def test_clip_path_cannot_hide_most_of_a_touch_target(self):
        broken = audit_artifact(
            css=(
                AUDIT_CSS
                + "\n[data-action=close], [data-action=send], [data-suggestion] {"
                "clip-path: inset(21px); }"
            )
        )
        with self.assertRaises(BrowserAuditError) as caught:
            await BrowserAudit().audit(broken)
        self.assertEqual(caught.exception.error_code, "browser_gate_failed")
        self.assertIn("clipped", caught.exception.diagnostic or "")

    async def test_noop_clip_path_does_not_reject_a_healthy_touch_target(self):
        report = await BrowserAudit().audit(
            audit_artifact(css=AUDIT_CSS + "\n[data-suggestion] { clip-path: inset(0); }")
        )
        self.assertEqual(len(report.screenshots), 6)
        self.assertEqual(len(report.layouts), 8)

    async def test_all_interactive_targets_are_gated_without_an_action_count_cap(self):
        tiny_link = audit_artifact(
            body_html=AUDIT_HTML.replace(
                "</main>", '<a href="#details" style="width:10px;height:10px">i</a></main>'
            )
        )
        four_suggestions = audit_artifact(
            body_html=AUDIT_HTML.replace(
                "</nav>",
                """<button data-suggestion="two">two</button>
                <button data-suggestion="three">three</button>
                <button data-suggestion="four">four</button></nav>""",
            )
        )
        fourth_summary_action = audit_artifact(
            body_html=AUDIT_HTML.replace(
                "</nav>",
                '<details><summary style="width:44px;height:44px">more</summary></details></nav>',
            )
        )
        tiny_native_control = audit_artifact(
            body_html=AUDIT_HTML.replace(
                "</main>",
                '<audio controls style="width:8px;height:8px"></audio></main>',
            )
        )
        native_activation_controls = tuple(
            audit_artifact(
                body_html=AUDIT_HTML.replace(
                    "</header>", control + "</header>",
                )
            )
            for control in (
                '<select style="width:44px;height:44px"><option>x</option></select>',
                '<input type="checkbox" style="width:44px;height:44px">',
                '<input type="radio" style="width:44px;height:44px">',
                '<input type="file" style="width:44px;height:44px">',
                '<input type="range" style="width:44px;height:44px">',
                '<input type="image" alt="action" style="width:44px;height:44px">',
                '<input type="number" style="width:44px;height:44px">',
            )
        )
        allowed = await BrowserAudit().audit(four_suggestions)
        self.assertEqual(len(allowed.screenshots), 6)

        for broken in (
            tiny_link,
            fourth_summary_action,
            tiny_native_control,
            *native_activation_controls,
        ):
            with self.subTest(body=broken.body_html[-300:]):
                with self.assertRaises(BrowserAuditError) as caught:
                    await BrowserAudit().audit(broken)
                self.assertEqual(caught.exception.error_code, "browser_gate_failed")

    async def test_custom_click_targets_are_measured_and_counted_as_actions(self):
        candidates = (
            audit_artifact(
                body_html=AUDIT_HTML.replace(
                    "</main>",
                    '<div onclick="void 0" style="width:8px;height:8px">custom</div></main>',
                )
            ),
            audit_artifact(
                body_html=AUDIT_HTML.replace(
                    "</main>",
                    '<div id="custom-action" style="width:44px;height:44px">custom</div>'
                    '<script>document.getElementById("custom-action").addEventListener("click",()=>{});</script>'
                    "</main>",
                )
            ),
        )
        for broken in candidates:
            with self.subTest(body=broken.body_html[-300:]):
                with self.assertRaises(BrowserAuditError) as caught:
                    await BrowserAudit().audit(broken)
                self.assertEqual(caught.exception.error_code, "browser_gate_failed")

    async def test_image_map_area_uses_hotspot_geometry_not_zero_dom_rect(self):
        mapped = audit_artifact(
            body_html=AUDIT_HTML.replace(
                "</header>",
                '<img usemap="#audit-map" alt="map" style="width:44px;height:44px">'
                '<map name="audit-map"><area href="#x" shape="rect" coords="0,0,8,8" '
                'aria-label="tiny hotspot"></map></header>',
            )
        )
        with self.assertRaises(BrowserAuditError) as caught:
            await BrowserAudit().audit(mapped)
        self.assertEqual(caught.exception.error_code, "browser_gate_failed")
        self.assertIn("44", caught.exception.diagnostic or "")

    async def test_shadow_dom_creation_is_fail_closed(self):
        shadowed = audit_artifact(
            body_html=(
                AUDIT_HTML
                + '<div id="shadow-host" style="width:8px;height:8px;overflow:hidden"></div>'
                + "<script>const root=document.querySelector('#shadow-host').attachShadow({mode:'open'});"
                + "root.innerHTML='<button style=\"width:8px;height:8px\">x</button>';</script>"
            )
        )
        with self.assertRaises(BrowserAuditError) as caught:
            await BrowserAudit().audit(shadowed)
        self.assertIn(
            caught.exception.error_code,
            {"browser_runtime_failed", "browser_gate_failed"},
        )

    async def test_hostile_popup_websocket_worker_download_console_and_pageerror_fail_closed(self):
        attempts = {
            "popup": "setTimeout(()=>window.open('about:blank'),120)",
            "websocket": "setTimeout(()=>new WebSocket('wss://example.com/socket'),120)",
            "webrtc": (
                "setTimeout(()=>{try{const peer=new RTCPeerConnection();"
                "peer.createDataChannel('audit');peer.close()}catch(_){}},120)"
            ),
            "worker": "setTimeout(()=>new Worker('data:text/javascript,postMessage(1)'),120)",
            "console": "setTimeout(()=>console.error('hostile-console'),120)",
            "pageerror": "setTimeout(()=>{throw new Error('hostile-pageerror')},120)",
        }
        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch(headless=True)
            try:
                audit = BrowserAudit(browser=browser)
                for name, script in attempts.items():
                    hostile = audit_artifact(
                        body_html=AUDIT_HTML.replace("</section>\n", f"<script>{script}</script></section>\n", 1)
                    )
                    with self.subTest(channel=name), self.assertRaises(BrowserAuditError):
                        await audit.audit(hostile)
            finally:
                await browser.close()

    async def test_download_guard_records_real_chromium_download_attempt(self):
        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch(headless=True)
            audit = BrowserAudit(browser=browser)
            context, page, failures = await audit._new_context(browser, 390, 844)
            try:
                await page.set_content('<a download="proof.txt" href="data:text/plain,proof">download</a>')
                await page.locator("a").click()
                await page.wait_for_timeout(50)
                with self.assertRaises(BrowserAuditError) as caught:
                    audit._raise_policy_failures(failures)
                self.assertEqual(caught.exception.error_code, "unexpected_network")
                self.assertTrue(any(item.startswith("download:") for item in failures.policy))
            finally:
                await context.close()
                await browser.close()

    async def test_hostile_iframe_navigation_fails_closed(self):
        hostile = audit_artifact(
            body_html=AUDIT_HTML.replace(
                "</section>\n",
                "<script>setTimeout(()=>{location.href='data:text/html,escaped'},120)</script></section>\n",
                1,
            )
        )
        with self.assertRaises(BrowserAuditError):
            await BrowserAudit().audit(hostile)


if __name__ == "__main__":
    unittest.main()
