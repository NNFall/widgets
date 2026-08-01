import unittest

from playwright.async_api import async_playwright

from builder_lab.preview import build_preview_document, build_trusted_runtime_document
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

    async def mount_runtime(self, candidate=None, *, trusted=False):
        builder = build_trusted_runtime_document if trusted else build_preview_document
        document = builder(
            candidate or artifact(revision=9), channel_id=CHANNEL
        )
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
            "\u042d\u0442\u043e \u0432\u0438\u0437\u0443\u0430\u043b\u044c\u043d\u044b\u0439 "
            "\u043f\u0440\u0435\u0434\u043f\u0440\u043e\u0441\u043c\u043e\u0442\u0440. \u0414\u0438\u0430\u043b\u043e\u0433 "
            "\u0441 AI \u0434\u043e\u0441\u0442\u0443\u043f\u0435\u043d \u0432 \u0441\u0442\u0443\u0434\u0438\u0438.",
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

    async def test_runtime_hidden_suggestions_cannot_be_reshown_by_generated_css(self):
        base = artifact(revision=9)
        body_html = base.body_html.replace(
            "<button type=\"button\">Подобрать решение</button>",
            (
                "<button type=\"button\">Первый вопрос</button>"
                "<button type=\"button\">Второй вопрос</button>"
                "<button type=\"button\">Третий вопрос</button>"
            ),
        )
        frame = await self.mount_runtime(
            artifact(
                revision=9,
                body_html=body_html,
                css=base.css
                + "\n[data-region=\"suggestions\"] button { display:flex; }",
                suggested_actions=(
                    "Первый вопрос",
                    "Второй вопрос",
                    "Третий вопрос",
                ),
            )
        )
        suggestions = frame.locator('[data-region="suggestions"] button')

        self.assertEqual(await suggestions.count(), 3)
        self.assertEqual(
            [await suggestions.nth(index).is_visible() for index in range(3)],
            [True, True, False],
        )
        self.assertTrue(await suggestions.nth(2).is_disabled())

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
        self.assertEqual(await input_box.input_value(), "Первый вопрос")
        self.assertEqual(
            await frame.locator('[data-region="composer"]').get_attribute("aria-busy"),
            "true",
        )
        await self.page.evaluate("window.releaseBridgeResponse()")
        await frame.locator('[data-kaigo-runtime-message="assistant"]').wait_for()
        self.assertEqual(await input_box.input_value(), "")
        self.assertNotIn(
            "Нельзя принять",
            await frame.locator('[data-region="messages"]').inner_text(),
        )

        await input_box.fill("Ошибка")
        await input_box.press("Enter")
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
        await self.page.evaluate("window.releaseBridgeResponse()")
        await frame.locator('[data-kaigo-runtime-message="assistant"]').nth(1).wait_for()
        self.assertEqual(await input_box.input_value(), "")
        self.assertEqual(
            await frame.locator('[data-kaigo-runtime-message="assistant"]').count(), 2
        )


if __name__ == "__main__":
    unittest.main()
