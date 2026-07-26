from __future__ import annotations

import logging
import time
from typing import Protocol

from .codex_runner import CodexRunError
from .config import BridgeConfig, ConfigError, validate_thread_id
from .store import BridgeStore, Job
from .telegram_api import TelegramApiError, split_message


class TelegramTransport(Protocol):
    def get_updates(self, *, offset: int | None, timeout_seconds: int) -> list[dict]: ...

    def send_message(self, chat_id: int, text: str) -> None: ...

    def send_chat_action(self, chat_id: int, action: str = "typing") -> None: ...


class CodexTransport(Protocol):
    def run(self, thread_id: str, prompt: str, *, request_id: str) -> str: ...

    def forget(self, request_id: str) -> None: ...


class BridgeService:
    def __init__(
        self,
        config: BridgeConfig,
        telegram: TelegramTransport,
        runner: CodexTransport,
        store: BridgeStore,
        *,
        logger: logging.Logger | None = None,
    ) -> None:
        self.config = config
        self.telegram = telegram
        self.runner = runner
        self.store = store
        self.logger = logger or logging.getLogger(__name__)
        self._next_maintenance_at = 0.0
        for chat_id, thread_id in config.chat_bindings.items():
            self.store.ensure_binding(chat_id, thread_id)

    def _reply(self, chat_id: int, text: str) -> None:
        for chunk in split_message(text):
            self.telegram.send_message(chat_id, chunk)

    def handle_update(self, update: dict) -> None:
        update_id = update.get("update_id")
        message = update.get("message")
        if not isinstance(update_id, int) or not isinstance(message, dict):
            return
        if self.store.has_update(update_id):
            return
        sender = message.get("from")
        chat = message.get("chat")
        if not isinstance(sender, dict) or not isinstance(chat, dict):
            return
        user_id = sender.get("id")
        chat_id = chat.get("id")
        if not isinstance(user_id, int) or not isinstance(chat_id, int):
            return
        text = message.get("text")

        if user_id not in self.config.allowed_user_ids:
            self._reply(
                chat_id,
                f"Доступ не настроен. Ваш Telegram ID: {user_id}. "
                "Добавьте его в allowed_user_ids на Windows-компьютере.",
            )
            return

        if not isinstance(text, str):
            self._reply(chat_id, "Пока мост принимает только текст. Файлы и фото добавим отдельно.")
            self.store.mark_update_processed(update_id)
            return

        stripped = text.strip()
        if stripped.startswith("/"):
            self._handle_command(update_id, chat_id, user_id, stripped)
            return
        if not stripped:
            self._reply(chat_id, "Пустое сообщение не будет отправлено в Codex.")
            self.store.mark_update_processed(update_id)
            return
        thread_id = self.store.get_binding(chat_id)
        if thread_id is None:
            self._reply(chat_id, "Чат не привязан к задаче Codex. Используйте /use <thread-id>.")
            self.store.mark_update_processed(update_id)
            return
        if self.store.enqueue(update_id, chat_id, user_id, thread_id, stripped):
            position = self.store.pending_count()
            self._reply(chat_id, f"Сообщение принято. Позиция в очереди: {position}.")

    def _handle_command(self, update_id: int, chat_id: int, user_id: int, text: str) -> None:
        command, _, argument = text.partition(" ")
        command = command.split("@", 1)[0].lower()
        if command == "/start":
            self._reply(
                chat_id,
                f"Мост Telegram ↔ Codex активен. Ваш Telegram ID: {user_id}. "
                "Команды: /status, /thread, /use <thread-id>.",
            )
        elif command == "/status":
            binding = self.store.get_binding(chat_id)
            self._reply(
                chat_id,
                f"Задача Codex: {binding or 'не привязана'}. "
                f"В очереди: {self.store.pending_count()}.",
            )
        elif command == "/thread":
            binding = self.store.get_binding(chat_id)
            self._reply(chat_id, f"Текущая задача Codex: {binding or 'не привязана'}.")
        elif command == "/use":
            try:
                thread_id = validate_thread_id(argument.strip())
            except ConfigError:
                self._reply(chat_id, "Формат: /use 00000000-0000-0000-0000-000000000000")
            else:
                self.store.set_binding(chat_id, thread_id)
                self._reply(chat_id, f"Чат привязан к задаче Codex {thread_id}.")
        else:
            self._reply(chat_id, "Неизвестная команда. Доступны: /start, /status, /thread, /use <thread-id>.")
        self.store.mark_update_processed(update_id)

    def poll_once(self) -> int:
        last_update_id = self.store.get_last_update_id()
        offset = None if last_update_id is None else last_update_id + 1
        updates = self.telegram.get_updates(
            offset=offset,
            timeout_seconds=self.config.poll_timeout_seconds,
        )
        processed = 0
        for update in sorted(updates, key=lambda item: item.get("update_id", -1)):
            self.handle_update(update)
            update_id = update.get("update_id")
            if isinstance(update_id, int):
                self.store.set_last_update_id(update_id)
            processed += 1
        return processed

    def _deliver(self, job: Job) -> None:
        if not job.response:
            raise RuntimeError("Delivery job has no response")
        request_id = str(job.id)
        self.runner.forget(request_id)
        chunks = split_message(job.response)
        for index in range(job.delivery_cursor, len(chunks)):
            self.telegram.send_message(job.chat_id, chunks[index])
            self.store.mark_chunk_delivered(job.id, index + 1)
        self.store.mark_done(job.id)

    def process_one(self) -> bool:
        delivery = self.store.next_delivery()
        if delivery is not None:
            self._deliver(delivery)
            return True
        job = self.store.claim_next()
        if job is None:
            return False
        try:
            try:
                self.telegram.send_chat_action(job.chat_id, "typing")
            except TelegramApiError:
                self.logger.warning("Could not send Telegram typing action")
            response = self.runner.run(
                job.thread_id,
                job.prompt,
                request_id=str(job.id),
            )
        except (CodexRunError, OSError, RuntimeError) as exc:
            self.store.mark_failed(job.id, str(exc))
            self._reply(
                job.chat_id,
                "Codex не смог завершить ход. Подробности сохранены в локальном логе моста.",
            )
            self.logger.error("Codex turn failed: %s", exc)
            return True
        self.store.mark_for_delivery(job.id, response)
        self.runner.forget(str(job.id))
        refreshed = self.store.next_delivery()
        if refreshed is None:
            raise RuntimeError("Generated response was not persisted")
        self._deliver(refreshed)
        return True

    def run_maintenance(self, *, now: float | None = None) -> tuple[int, int]:
        current = time.monotonic() if now is None else now
        if current < self._next_maintenance_at:
            return 0, 0
        result = self.store.prune_history(self.config.retention_days)
        self._next_maintenance_at = current + 3600.0
        pruned_jobs, pruned_updates = result
        if pruned_jobs or pruned_updates:
            self.logger.info(
                "Pruned %d old job(s) and %d processed update(s)",
                pruned_jobs,
                pruned_updates,
            )
        return result

    def run_forever(self) -> None:
        recovered = self.store.recover_interrupted()
        if recovered:
            self.logger.warning("Recovered %d interrupted Codex turn(s)", recovered)
        self.run_maintenance()
        self.logger.info("Telegram Codex bridge started")
        while True:
            self.run_maintenance()
            try:
                self.poll_once()
            except TelegramApiError as exc:
                self.logger.warning("Telegram polling failed: %s", exc)
                time.sleep(self.config.retry_delay_seconds)
                continue
            try:
                while self.process_one():
                    pass
            except TelegramApiError as exc:
                self.logger.warning("Telegram delivery failed: %s", exc)
                time.sleep(self.config.retry_delay_seconds)
