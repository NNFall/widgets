import unittest

from builder_lab.preview import PREVIEW_CSP, build_preview_document, preview_iframe_attributes
from tests.builder_lab_cases.test_validation import artifact


MOJIBAKE_MARKERS = ("РЎ", "Рµ", "Р°", "СЃ", "С‚", "вЂ", "\ufffd")


class PreviewDocumentTests(unittest.TestCase):
    def test_document_contains_restrictive_csp_and_validated_artifact(self):
        candidate = artifact(revision=7)
        document = build_preview_document(candidate)
        self.assertIn("default-src &#x27;none&#x27;", document)
        self.assertIn("connect-src &#x27;none&#x27;", document)
        self.assertIn("form-action &#x27;none&#x27;", document)
        self.assertIn(candidate.body_html, document)
        self.assertIn(candidate.css, document)
        self.assertNotIn("allow-same-origin", document)
        self.assertNotIn("fetch(", document)
        self.assertNotIn("XMLHttpRequest", document)
        self.assertEqual(PREVIEW_CSP.count("connect-src"), 1)

    def test_fixed_runtime_has_versioned_render_acknowledgement(self):
        document = build_preview_document(
            artifact(revision=9), channel_id="channel-1234567890abcdef"
        )
        self.assertIn("kaigo-builder-preview", document)
        self.assertIn("version: 2", document)
        self.assertIn("type: 'rendered'", document)
        self.assertIn("const revision = 9", document)
        self.assertIn("channel_id: channelId", document)
        self.assertIn('const channelId = "channel-1234567890abcdef"', document)
        self.assertIn("event.source !== window.parent", document)
        self.assertIn("data.channel_id !== channelId", document)
        self.assertIn("data.revision !== revision", document)
        self.assertNotIn("data-kaigo-generated", document)

    def test_generated_javascript_is_appended_after_the_trusted_runtime(self):
        candidate = artifact(
            revision=9,
            javascript=(
                "document.querySelector('[data-region=root]')"
                ".dataset.generated = 'yes';"
            ),
        )

        document = build_preview_document(
            candidate,
            channel_id="channel-1234567890abcdef",
        )

        self.assertIn("data-kaigo-generated", document)
        self.assertIn("dataset.generated = 'yes'", document)
        self.assertGreater(
            document.index("data-kaigo-generated"),
            document.index("type: 'rendered'"),
        )

    def test_generated_script_terminator_is_escaped_case_insensitively(self):
        document = build_preview_document(
            artifact(
                javascript=(
                    "document.body.dataset.safe = "
                    "'</ScRiPt><script id=escape>bad()</script>';"
                )
            )
        )

        self.assertNotIn("</ScRiPt>", document)
        self.assertNotIn("</ScRiPt><script", document)
        self.assertIn("<\\/script><script id=escape>", document)

    def test_chat_runtime_is_closed_by_default_and_uses_safe_dom_messages(self):
        document = build_preview_document(
            artifact(revision=9), channel_id="channel-1234567890abcdef"
        )
        self.assertIn("setOpen(false)", document)
        self.assertNotIn("setOpen(true)", document)
        self.assertIn("content.textContent = text", document)
        self.assertNotIn("message.textContent = text", document)
        self.assertIn("const assistantLabel =", document)
        self.assertIn(
            "label.textContent = role === 'user' ? 'ВЫ' : assistantLabel",
            document,
        )
        self.assertIn("root.getAttribute('aria-label')", document)
        self.assertNotIn("RAW AI", document)
        self.assertIn("data-kaigo-runtime-message", document)
        self.assertIn("message.className = `kaigo-widget__message kaigo-widget__message--${role}`", document)
        self.assertIn("label.className = 'kaigo-widget__message-label'", document)
        self.assertIn("content.className = 'kaigo-widget__message-content'", document)
        self.assertIn("messages.setAttribute('role', 'log')", document)
        self.assertIn("messages.setAttribute('aria-live', 'polite')", document)
        self.assertNotIn("innerHTML", document)
        self.assertNotIn("insertAdjacentHTML", document)

    def test_chat_runtime_user_facing_copy_is_readable_utf8(self):
        document = build_preview_document(
            artifact(revision=9), channel_id="channel-1234567890abcdef"
        )
        self.assertIn("Связь прервалась. Текст сохранён.", document)
        self.assertIn("ПОВТОРИТЬ", document)
        self.assertFalse(any(marker in document for marker in MOJIBAKE_MARKERS))

    def test_chat_runtime_separates_pending_and_failed_request_lifecycle(self):
        document = build_preview_document(
            artifact(revision=9), channel_id="channel-1234567890abcdef"
        )
        self.assertIn("let pendingRequest = null", document)
        self.assertIn("let failedRequest = null", document)
        self.assertIn("type: 'chat.request'", document)
        self.assertIn("request_id: pendingRequest.requestId", document)
        self.assertIn("text: pendingRequest.text", document)
        self.assertIn("composer.setAttribute('aria-busy', value ? 'true' : 'false')", document)
        self.assertIn("setBusy(true)", document)
        self.assertIn("setBusy(false)", document)
        self.assertIn("data-kaigo-runtime-status", document)
        self.assertIn("data-kaigo-runtime-retry", document)
        self.assertIn("retryFailedRequest", document)
        self.assertIn("pendingRequest = failedRequest", document)
        self.assertIn("failedRequest = null", document)
        self.assertIn(
            "if (input && input.value === pendingRequest.text) input.value = ''",
            document,
        )
        self.assertIn("failedRequest = pendingRequest", document)
        self.assertIn("pendingRequest = null", document)
        error_index = document.index("type === 'chat.error'")
        clear_index = document.index(
            "if (input && input.value === pendingRequest.text) input.value = ''"
        )
        self.assertLess(clear_index, error_index)

    def test_attention_state_is_runtime_owned_one_shot_and_reduced_motion_safe(self):
        document = build_preview_document(
            artifact(revision=9), channel_id="channel-1234567890abcdef"
        )
        self.assertIn("const ATTENTION_DELAY_MS = 15000", document)
        self.assertIn("kaigo-preview-attention", document)
        self.assertIn("prefers-reduced-motion: reduce", document)
        self.assertIn("document.visibilityState !== 'visible'", document)
        self.assertIn("pointerdown", document)
        self.assertIn("visibilitychange", document)
        self.assertIn("attentionCancelled", document)
        self.assertNotIn("new Audio(", document)
        self.assertNotIn(".play()", document)

    def test_chat_runtime_handles_textarea_keyboard_focus_and_session_preserving_toggle(self):
        document = build_preview_document(
            artifact(revision=9), channel_id="channel-1234567890abcdef"
        )
        self.assertIn("document.createElement('textarea')", document)
        self.assertIn("input.rows = 1", document)
        self.assertIn("[data-kaigo-runtime-input]::-webkit-scrollbar", document)
        self.assertIn("scrollbar-width: none", document)
        self.assertIn("resize: none", document)
        self.assertIn("const minHeight = Math.max(44, lineHeight + 8)", document)
        self.assertIn(
            "Math.max(minHeight, Math.min(input.scrollHeight, maxHeight))",
            document,
        )
        self.assertIn("event.isComposing", document)
        self.assertIn("event.key === 'Enter' && !event.shiftKey", document)
        self.assertIn("event.key === 'Escape'", document)
        self.assertIn("launcher.focus()", document)
        self.assertIn("launcher.hidden = Boolean(open)", document)
        self.assertIn(
            "launcher.setAttribute('aria-hidden', open ? 'true' : 'false')",
            document,
        )
        self.assertIn("matchMedia('(hover: hover) and (pointer: fine)').matches", document)
        self.assertIn("document.querySelector('[data-action=\"close\"]')", document)
        self.assertNotIn("[data-region=\"header\"] button", document)
        self.assertNotIn("replaceChildren()", document)
        self.assertNotIn("sessionStorage.clear", document)

    def test_suggestions_send_real_questions_instead_of_only_copying_text(self):
        document = build_preview_document(
            artifact(revision=9), channel_id="channel-1234567890abcdef"
        )
        self.assertIn("sendText(text)", document)
        self.assertIn("suggestions.slice(2).forEach", document)
        self.assertIn("root.dataset.chatStarted = 'true'", document)
        self.assertIn("suggestionsRegion.hidden = true", document)
        self.assertIn("suggestionsRegion.setAttribute('aria-hidden', 'true')", document)
        self.assertNotIn("input.focus();\n  }});", document)

    def test_rejects_invalid_channel_identifier(self):
        for channel in ("", "short", "bad channel", "x" * 97):
            with self.subTest(channel=channel):
                with self.assertRaises(ValueError):
                    build_preview_document(artifact(), channel_id=channel)

    def test_fixed_runtime_synchronizes_css_checkbox_toggles(self):
        document = build_preview_document(artifact(revision=10))
        self.assertIn("input[type=\"checkbox\"]", document)
        self.assertIn("toggle.checked = Boolean(open)", document)
        self.assertIn("toggle.addEventListener('change'", document)
        self.assertIn("event.preventDefault()", document)

    def test_metadata_is_escaped(self):
        document = build_preview_document(
            artifact(art_direction='</script><script id="escape">bad()</script>')
        )
        self.assertNotIn('<script id="escape">', document)

    def test_style_terminator_is_escaped_case_insensitively(self):
        document = build_preview_document(
            artifact(css='.kaigo-widget { content: "</StYlE><script id=escape>bad()</script>"; }')
        )
        self.assertNotIn("</StYlE>", document)
        self.assertNotIn("</StYlE><script id=escape>", document)
        self.assertIn("<\\/style><script id=escape>", document)

    def test_parent_iframe_contract_is_opaque(self):
        attributes = preview_iframe_attributes()
        self.assertEqual(attributes["sandbox"], "allow-scripts")
        self.assertNotIn("allow-same-origin", attributes["sandbox"])
        self.assertEqual(attributes["referrerpolicy"], "no-referrer")
        self.assertIn("AI", attributes["title"])


if __name__ == "__main__":
    unittest.main()
