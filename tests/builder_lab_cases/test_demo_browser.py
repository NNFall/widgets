import tempfile
import unittest
from pathlib import Path

from aiohttp.test_utils import TestServer
from playwright.async_api import async_playwright

from builder_lab.chat import ChatReply
from builder_lab.demo import save_demo
from builder_lab.models import EngineName
from builder_lab.store import RunStore
from builder_lab.web import create_builder_lab_app
from tests.builder_lab_cases.test_demo import completed_snapshot


class IdleOrchestrator:
    async def close(self):
        return None


class BrowserChatService:
    def __init__(self):
        self.calls = []

    async def reply(self, **kwargs):
        self.calls.append(kwargs)
        return ChatReply(
            request_id=kwargs["request_id"],
            text=f"Ход {len(self.calls)}: {kwargs['text']}",
            provider_request_id=f"provider-{len(self.calls)}",
        )

    async def close(self):
        return None


class ResumableOrchestrator:
    def __init__(self, store):
        self.store = store
        self.run_id = None

    async def start(self, request):
        run = await self.store.create(request)
        self.run_id = run.run_id
        await self.store.set_running(run.run_id)
        await self.store.append_event(
            run.run_id,
            event_type="reference.started",
            stage=None,
            status="running",
            message="Сайт анализируется; активный запуск сохранён.",
        )
        return await self.store.snapshot(run.run_id)

    async def cancel(self, run_id):
        return await self.store.request_cancel(run_id)

    async def close(self):
        return None


class DemoBridgeBrowserTests(unittest.IsolatedAsyncioTestCase):
    async def test_builder_restores_active_run_and_history_after_reload(self):
        store = RunStore()
        orchestrator = ResumableOrchestrator(store)
        app = create_builder_lab_app(
            store=store,
            orchestrator=orchestrator,
            enabled_engines=(EngineName.DIRECT,),
            chat_secure_cookie=False,
        )
        server = TestServer(app)
        await server.start_server()
        playwright = await async_playwright().start()
        browser = await playwright.chromium.launch(headless=True)
        page = await browser.new_page(viewport={"width": 1280, "height": 800})
        try:
            await page.goto(str(server.make_url("/")))
            await page.locator("#source-url").fill("https://example.com/")
            await page.locator("#brief").fill("Спокойный консультант")
            await page.locator("#generate").click()
            await page.get_by_text(
                "Сайт анализируется; активный запуск сохранён."
            ).wait_for()

            await page.reload()

            await page.get_by_text(
                "Сайт анализируется; активный запуск сохранён."
            ).wait_for()
            self.assertEqual(
                await page.locator("#source-url").input_value(),
                "https://example.com/",
            )
            self.assertEqual(
                await page.locator("#brief").input_value(),
                "Спокойный консультант",
            )
            self.assertIn(
                "running",
                await page.locator("#status").inner_text(),
            )
        finally:
            await browser.close()
            await playwright.stop()
            await server.close()

    async def test_demo_parent_bridge_completes_two_turns_in_one_cookie_session(self):
        with tempfile.TemporaryDirectory() as directory:
            demo_path = Path(directory) / "latest.json"
            save_demo(
                demo_path,
                completed_snapshot(),
                model="gemini-3.5-flash",
                source_url="https://rawbureau.ru/",
                chat_system_prompt="Отвечай только по проверенным фактам.",
            )
            chat = BrowserChatService()
            app = create_builder_lab_app(
                store=RunStore(),
                orchestrator=IdleOrchestrator(),
                enabled_engines=(EngineName.DIRECT,),
                demo_path=demo_path,
                chat_service=chat,
                chat_secure_cookie=False,
            )
            server = TestServer(app)
            await server.start_server()
            playwright = await async_playwright().start()
            browser = await playwright.chromium.launch(headless=True)
            page = await browser.new_page(viewport={"width": 1280, "height": 800})
            errors = []
            page.on("pageerror", lambda error: errors.append(str(error)))
            try:
                await page.goto(str(server.make_url("/demo")))
                frame = page.frame_locator("#demo-preview")
                launcher = frame.locator('[data-region="launcher"]')
                await launcher.wait_for()
                self.assertEqual(
                    await frame.locator('[data-region="root"]').get_attribute(
                        "data-state"
                    ),
                    "closed",
                )
                await launcher.click()
                composer = frame.locator('[data-kaigo-runtime-input="true"]')
                await composer.fill("Первый вопрос")
                await composer.press("Enter")
                await frame.locator('[data-kaigo-runtime-message="assistant"]').wait_for()
                await composer.fill("Уточнение")
                await composer.press("Enter")
                await frame.locator('[data-kaigo-runtime-message="assistant"]').nth(1).wait_for()
                await page.keyboard.press("Escape")
                await launcher.click()

                self.assertEqual(
                    await frame.locator('[data-kaigo-runtime-message="user"]').count(),
                    2,
                )
                self.assertEqual(
                    await frame.locator('[data-kaigo-runtime-message="assistant"]').count(),
                    2,
                )
                self.assertIn(
                    "Ход 2: Уточнение",
                    await frame.locator('[data-region="messages"]').inner_text(),
                )
                self.assertEqual(len(chat.calls), 2)
                self.assertEqual(chat.calls[0]["session_id"], chat.calls[1]["session_id"])
                self.assertEqual(chat.calls[0]["scope"], chat.calls[1]["scope"])
                self.assertEqual(errors, [])
            finally:
                await browser.close()
                await playwright.stop()
                await server.close()


if __name__ == "__main__":
    unittest.main()
