import unittest

from playwright.async_api import async_playwright

from builder_lab.preview import (
    TOP_LEVEL_VISUAL_ONLY_MESSAGE,
    build_preview_document,
    build_trusted_runtime_document,
)
from tests.builder_lab_cases.test_validation import artifact


CHANNEL = "channel-1234567890abcdef"


class PreviewRuntimeBrowserTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.playwright = await async_playwright().start()
        self.browser = await self.playwright.chromium.launch(headless=True)
        self.page = await self.browser.new_page(viewport={"width": 1280, "height": 800})

    async def asyncTearDown(self):
        await self.browser.close()
        await self.playwright.stop()

    async def mount_runtime(
        self,
        candidate=None,
        *,
        trusted=False,
        assistant_label=None,
    ):
        builder = build_trusted_runtime_document if trusted else build_preview_document
        kwargs = {"channel_id": CHANNEL}
        if trusted:
            kwargs["assistant_label"] = assistant_label
        document = builder(candidate or artifact(revision=9), **kwargs)
        await self.page.set_content("<main><iframe id='preview' sandbox='allow-scripts'></iframe></main>")
        await self.page.evaluate(
            """previewDocument => {
                window.bridgeEvents = [];
                window.bridgeAttempts = {};
                window.bridgeReplies = [];
                window.releaseBridgeResponse = () => {
                  const reply = window.bridgeReplies.shift();
                  if (!reply) throw new Error('no pending bridge response');
                  reply();
                };
                const frame = document.getElementById('preview');
                window.addEventListener('message', event => {
                  const data = event.data;
                  if (event.source !== frame.contentWindow || !data || data.source !== 'kaigo-builder-preview') return;
                  window.bridgeEvents.push(data);
                  if (data.type !== 'chat.request') return;
                  const count = (window.bridgeAttempts[data.request_id] || 0) + 1;
                  window.bridgeAttempts[data.request_id] = count;
                  const base = {source:'kaigo-builder-parent', version:2, channel_id:data.channel_id, revision:data.revision, request_id:data.request_id};
                  if (data.text === 'Ошибка' && count === 1) {
                    setTimeout(() => event.source.postMessage({...base, type:'chat.error', message:'Временная ошибка', retryable:true}, '*'), 40);
                    return;
                  }
                  event.source.postMessage({...base, channel_id:'wrong-channel-123456789', type:'chat.response', text:'Нельзя принять'}, '*');
                  window.bridgeReplies.push(() => event.source.postMessage({...base, type:'chat.response', text:'Ответ на: ' + data.text}, '*'));
                });
                frame.srcdoc = previewDocument;
            }""",
            document,
        )
        await self.page.wait_for_function(
            "window.bridgeEvents.some(event => event.type === 'rendered')"
        )
        return self.page.frame_locator("#preview")

    async def test_generated_javascript_runs_inside_the_preview(self):
        frame = await self.mount_runtime(
            artifact(
                revision=9,
                javascript=(
                    "document.querySelector('[data-region=root]')"
                    ".dataset.generated='yes'"
                ),
            )
        )

        self.assertEqual(
            await frame.locator("[data-region=root]").get_attribute(
                "data-generated"
            ),
            "yes",
        )

    async def test_trusted_runtime_survives_generated_dom_takeover_payload(self):
        marker = "trusted-runtime-dom-takeover"
        base = artifact(revision=9)
        candidate = artifact(
            revision=9,
            body_html=base.body_html.replace(
                '<header class="kaigo-widget__header" data-region="header">',
                '<header class="kaigo-widget__header" data-region="header">'
                '<button type="button" data-action="close">Close</button>',
            ),
            javascript=(
                f"window.__payload='{marker}';"
                "document.body.replaceChildren(document.createTextNode('owned'));"
            ),
        )
        document = build_trusted_runtime_document(candidate, channel_id=CHANNEL)

        self.assertNotIn(marker, document)
        self.assertNotIn("data-kaigo-generated", document)
        frame = await self.mount_runtime(candidate, trusted=True)
        root = frame.locator('[data-region="root"]')
        launcher = frame.locator('[data-region="launcher"]')

        self.assertEqual(await root.count(), 1)
        await launcher.click()
        self.assertEqual(await root.get_attribute("data-state"), "open")
        input_box = frame.locator('[data-kaigo-runtime-input="true"]')
        await input_box.fill("Trusted question")
        await input_box.press("Enter")
        await self.page.wait_for_function(
            "window.bridgeEvents.some(event => "
            "event.type === 'chat.request' && event.text === 'Trusted question')"
        )
        await self.page.evaluate("window.releaseBridgeResponse()")
        await frame.locator('[data-kaigo-runtime-message="assistant"]').wait_for()
        await frame.locator('[data-action="close"]').click()
        self.assertEqual(await root.get_attribute("data-state"), "closed")
        self.assertTrue(await launcher.is_visible())

    async def test_trusted_runtime_enforces_persona_across_visible_identity_labels(self):
        base = artifact(revision=9)
        candidate = artifact(
            revision=9,
            body_html=base.body_html.replace(
                '<main class="kaigo-widget__messages" data-region="messages" aria-live="polite">',
                '<main class="kaigo-widget__messages" data-region="messages" aria-live="polite">'
                '<article class="kaigo-widget__message--assistant">'
                '<span class="kaigo-widget__message-label">AI-консультант</span>'
                '</article>',
            ).replace(
                '</header>',
                '<strong>AI-консультант</strong>'
                '<button data-action="close" aria-label="Закрыть AI-консультанта">×</button>'
                '</header>',
            ),
        )

        frame = await self.mount_runtime(
            candidate,
            trusted=True,
            assistant_label="Анна",
        )
        root = frame.locator('[data-region="root"]')
        launcher = frame.locator('[data-region="launcher"]')

        self.assertEqual(await root.get_attribute("aria-label"), "Анна")
        self.assertEqual((await launcher.inner_text()).strip(), "А")
        self.assertEqual(
            await frame.locator('[data-region="header"] h2').inner_text(),
            "Анна",
        )
        self.assertEqual(
            await frame.locator('.kaigo-widget__message-label').inner_text(),
            "Анна",
        )
        self.assertNotIn(
            "AI",
            " ".join(
                await frame.locator('[aria-label]').evaluate_all(
                    "nodes => nodes.map(node => node.getAttribute('aria-label') || '')"
                )
            ),
        )

    async def test_trusted_runtime_neutralizes_legacy_ai_labels_with_separators(self):
        base = artifact(revision=9)
        candidate = artifact(
            revision=9,
            body_html=(
                base.body_html
                .replace("AI-консультант", "AI_consultant")
                .replace(">AI<", ">AI2<")
            ),
        )

        frame = await self.mount_runtime(candidate, trusted=True)
        root = frame.locator('[data-region="root"]')
        launcher = frame.locator('[data-region="launcher"]')

        self.assertEqual(await root.get_attribute("aria-label"), "КОНСУЛЬТАНТ")
        self.assertEqual((await launcher.inner_text()).strip(), "К")
        self.assertEqual(
            await frame.locator('[data-region="header"] h2').inner_text(),
            "КОНСУЛЬТАНТ",
        )
        visible_labels = " ".join(
            await frame.locator('[aria-label]').evaluate_all(
                "nodes => nodes.map(node => node.getAttribute('aria-label') || '')"
            )
        )
        self.assertNotIn("AI_", visible_labels)
        self.assertNotIn("AI2", visible_labels)

    async def test_top_level_trusted_runtime_fails_chat_as_visual_only_without_staying_busy(
        self,
    ):
        document = build_trusted_runtime_document(
            artifact(revision=9), channel_id=CHANNEL
        )
        await self.page.set_content(document)

        await self.page.locator('[data-region="launcher"]').click()
        input_box = self.page.locator('[data-kaigo-runtime-input="true"]')
        question = "\u041a\u0430\u043a \u043d\u0430\u0447\u0430\u0442\u044c?"
        await input_box.fill(question)
        await input_box.press("Enter")

        status = self.page.locator('[data-kaigo-runtime-status="error"]')
        await status.wait_for()
        self.assertEqual(
            await status.inner_text(),
            TOP_LEVEL_VISUAL_ONLY_MESSAGE,
        )
        self.assertEqual(await input_box.input_value(), question)
        self.assertEqual(
            await self.page.locator('[data-region="composer"]').get_attribute(
                "aria-busy"
            ),
            "false",
        )
        self.assertFalse(
            await self.page.locator('[data-region="composer"] button').is_disabled()
        )
        self.assertEqual(
            await self.page.locator('[data-kaigo-runtime-retry="true"]').count(),
            0,
        )
        self.assertEqual(
            await self.page.locator('[data-kaigo-runtime-content="user"]').inner_text(),
            question,
        )

        second_question = (
            "\u0410 \u0447\u0442\u043e \u0434\u0430\u043b\u044c\u0448\u0435?"
        )
        await input_box.fill(second_question)
        await input_box.press("Enter")
        self.assertEqual(
            await self.page.locator('[data-kaigo-runtime-message="user"]').count(),
            2,
        )
        self.assertEqual(await input_box.input_value(), second_question)

    async def test_empty_suggestions_region_is_hidden_without_a_layout_gap(self):
        base = artifact(revision=9)
        body_html = base.body_html.replace(
            "<button type=\"button\">Подобрать решение</button>",
            "",
        )
        empty_region = (
            '<div class="kaigo-widget__suggestions" '
            'data-region="suggestions"></div>'
        )
        body_html = body_html.replace(
            empty_region,
            (
                '<button type="button" data-suggestion="Внешнее действие">'
                "Внешнее действие</button>"
                + empty_region
            ),
        )
        frame = await self.mount_runtime(
            artifact(
                revision=9,
                body_html=body_html,
                css=base.css
                + ("\n[data-region=\"suggestions\"] { display:flex !important; "
                   "min-height:80px !important; padding:40px !important; }"),
                suggested_actions=(),
            )
        )
        region = frame.locator('[data-region="suggestions"]')

        self.assertEqual(await region.count(), 1)
        self.assertFalse(await region.is_visible())
        self.assertEqual(await region.get_attribute("aria-hidden"), "true")
        self.assertEqual(
            await region.evaluate("node => node.getBoundingClientRect().height"),
            0,
        )
        outside_suggestion = frame.locator(
            '[data-suggestion="Внешнее действие"]'
        )
        await outside_suggestion.evaluate("node => node.click()")
        await self.page.wait_for_timeout(50)
        self.assertEqual(
            await self.page.evaluate(
                "window.bridgeEvents.filter(event => event.type === 'chat.request').length"
            ),
            0,
        )

    async def test_two_suggestions_remain_visible_and_actionable(self):
        base = artifact(revision=9)
        body_html = base.body_html.replace(
            "<button type=\"button\">Подобрать решение</button>",
            (
                "<button type=\"button\" data-suggestion=\"Первый вопрос\">Первый вопрос</button>"
                "<button type=\"button\" data-suggestion=\"Второй вопрос\">Второй вопрос</button>"
            ),
        )
        frame = await self.mount_runtime(
            artifact(
                revision=9,
                body_html=body_html,
                suggested_actions=("Первый вопрос", "Второй вопрос"),
            )
        )
        suggestions = frame.locator('[data-region="suggestions"] button')

        self.assertEqual(await suggestions.count(), 2)
        self.assertTrue(await suggestions.nth(0).is_visible())
        self.assertTrue(await suggestions.nth(1).is_visible())

    async def test_generated_javascript_cannot_reach_parent_document(self):
        frame = await self.mount_runtime(
            artifact(
                revision=9,
                javascript=(
                    "try{parent.document.body.dataset.pwned='1'}"
                    "catch(error){document.body.dataset.isolated='1'}"
                ),
            )
        )

        self.assertIsNone(
            await self.page.locator("body").get_attribute("data-pwned")
        )
        self.assertEqual(
            await frame.locator("body").get_attribute("data-isolated"),
            "1",
        )

    async def test_real_protocol_keyboard_error_retry_and_close_reopen_history(self):
        frame = await self.mount_runtime()
        root = frame.locator('[data-region="root"]')
        launcher = frame.locator('[data-region="launcher"]')
        input_box = frame.locator('[data-kaigo-runtime-input="true"]')

        self.assertEqual(await root.get_attribute("data-state"), "closed")
        self.assertFalse(
            await launcher.evaluate("node => document.activeElement === node")
        )
        await launcher.click()
        self.assertEqual(await root.get_attribute("data-state"), "open")
        self.assertEqual(await input_box.evaluate("node => node.tagName"), "TEXTAREA")

        await input_box.fill("Первый вопрос")
        await input_box.press("Shift+Enter")
        self.assertIn("\n", await input_box.input_value())
        await input_box.fill("Первый вопрос")
        await input_box.press("Enter")
        self.assertEqual(await input_box.input_value(), "")
        self.assertEqual(
            await frame.locator('[data-region="composer"]').get_attribute("aria-busy"),
            "true",
        )
        draft = "Черновик следующего вопроса"
        await input_box.fill(draft)
        await self.page.evaluate("window.releaseBridgeResponse()")
        await frame.locator('[data-kaigo-runtime-message="assistant"]').wait_for()
        self.assertEqual(await input_box.input_value(), draft)
        self.assertNotIn(
            "Нельзя принять",
            await frame.locator('[data-region="messages"]').inner_text(),
        )

        await input_box.fill("")
        await input_box.fill("Ошибка")
        await input_box.press("Enter")
        self.assertEqual(await input_box.input_value(), "")
        retry = frame.locator('[data-kaigo-runtime-retry="true"]')
        await retry.wait_for()
        self.assertEqual(await input_box.input_value(), "Ошибка")
        await self.page.keyboard.press("Escape")
        self.assertEqual(await root.get_attribute("data-state"), "closed")
        await launcher.click()
        self.assertEqual(await input_box.input_value(), "Ошибка")
        self.assertEqual(
            await frame.locator('[data-kaigo-runtime-message="user"]').count(), 2
        )
        await retry.click()
        self.assertEqual(await input_box.input_value(), "")
        await self.page.evaluate("window.releaseBridgeResponse()")
        await frame.locator('[data-kaigo-runtime-message="assistant"]').nth(1).wait_for()
        self.assertEqual(await input_box.input_value(), "")
        self.assertEqual(
            await frame.locator('[data-kaigo-runtime-message="assistant"]').count(), 2
        )

    async def test_error_and_retry_do_not_overwrite_a_new_pending_draft(self):
        frame = await self.mount_runtime()
        await frame.locator('[data-region="launcher"]').click()
        input_box = frame.locator('[data-kaigo-runtime-input="true"]')

        await input_box.fill("Ошибка")
        await input_box.press("Enter")
        self.assertEqual(await input_box.input_value(), "")
        draft = "Не стирай этот черновик"
        await input_box.fill(draft)

        retry = frame.locator('[data-kaigo-runtime-retry="true"]')
        await retry.wait_for()
        self.assertEqual(await input_box.input_value(), draft)

        await retry.click()
        self.assertEqual(await input_box.input_value(), draft)
        await self.page.evaluate("window.releaseBridgeResponse()")
        await frame.locator('[data-kaigo-runtime-message="assistant"]').wait_for()
        self.assertEqual(await input_box.input_value(), draft)

    async def test_retry_preserves_whitespace_only_edit_of_restored_raw_text(self):
        frame = await self.mount_runtime()
        await frame.locator('[data-region="launcher"]').click()
        input_box = frame.locator('[data-kaigo-runtime-input="true"]')
        original = "  Ошибка  "

        await input_box.fill(original)
        await input_box.press("Enter")
        self.assertEqual(await input_box.input_value(), "")
        await self.page.wait_for_function(
            "window.bridgeEvents.some(event => "
            "event.type === 'chat.request' && event.text === 'Ошибка')"
        )

        retry = frame.locator('[data-kaigo-runtime-retry="true"]')
        await retry.wait_for()
        self.assertEqual(await input_box.input_value(), original)
        edited = original + " "
        await input_box.fill(edited)

        await retry.click()
        self.assertEqual(await input_box.input_value(), edited)
        await self.page.evaluate("window.releaseBridgeResponse()")
        await frame.locator('[data-kaigo-runtime-message="assistant"]').wait_for()
        self.assertEqual(await input_box.input_value(), edited)


if __name__ == "__main__":
    unittest.main()
