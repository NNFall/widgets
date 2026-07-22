import unittest

from playwright.async_api import async_playwright

from builder_lab.preview import build_preview_document
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

    async def mount_runtime(self):
        document = build_preview_document(
            artifact(revision=9), channel_id=CHANNEL
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
