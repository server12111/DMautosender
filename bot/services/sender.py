import asyncio
import logging
import random
import time
from dataclasses import dataclass, field
from io import BytesIO
from typing import Optional, Callable, Awaitable

from aiogram import Bot
from telethon import TelegramClient
from telethon.errors import (
    FloodWaitError,
    UserPrivacyRestrictedError,
    UserNotMutualContactError,
    InputUserDeactivatedError,
    PeerFloodError,
    UserBannedInChannelError,
    ChatWriteForbiddenError,
    UsernameNotOccupiedError,
    UsernameInvalidError,
    PhoneNumberBannedError,
)

from ..database.db import Database
from ..utils.spintax import evaluate_spintax
from ..userbot.manager import UserbotManager

logger = logging.getLogger("dmsender.sender")


@dataclass
class SendConfig:
    message_text: str
    parse_mode: str = "html"
    image_file_id: Optional[str] = None
    attach_file_id: Optional[str] = None
    attach_file_name: Optional[str] = None
    delay_mode: str = "fixed"
    delay_fixed: float = 10.0
    delay_min: float = 5.0
    delay_max: float = 30.0
    pause_between_cycles: float = 0.0

    def compute_delay(self) -> float:
        if self.delay_mode == "random":
            return random.uniform(self.delay_min, self.delay_max)
        return self.delay_fixed


@dataclass
class MailingStats:
    sent: int = 0
    errors: int = 0
    blocked: int = 0
    skipped: int = 0
    start_time: float = field(default_factory=time.time)
    _lock: asyncio.Lock = field(default_factory=asyncio.Lock)

    def speed_per_min(self) -> float:
        elapsed = time.time() - self.start_time
        if elapsed < 1:
            return 0.0
        return round(self.sent / elapsed * 60, 1)

    def reset(self) -> None:
        self.sent = 0
        self.errors = 0
        self.blocked = 0
        self.skipped = 0
        self.start_time = time.time()


# Callback types
OnProgress = Callable[[MailingStats, int], Awaitable[None]]
OnLog = Callable[[str], Awaitable[None]]
OnFinished = Callable[[], Awaitable[None]]
OnSpamBlock = Callable[[str, str], Awaitable[None]]  # (acc_label, reason)


class MailingSender:
    def __init__(
        self,
        db: Database,
        manager: UserbotManager,
        bot: Bot,
        campaign_id: int,
        active_account_ids: list[int],
        config: SendConfig,
    ) -> None:
        self._db = db
        self._manager = manager
        self._bot = bot
        self._campaign_id = campaign_id
        self._active_account_ids = active_account_ids
        self._config = config
        self._stop_event = asyncio.Event()
        self._queue: asyncio.Queue[str] = asyncio.Queue()
        self._stats = MailingStats()
        self._is_running = False
        self._on_progress: Optional[OnProgress] = None
        self._on_log: Optional[OnLog] = None
        self._on_finished: Optional[OnFinished] = None
        self._on_spamblock: Optional[OnSpamBlock] = None

    @property
    def is_running(self) -> bool:
        return self._is_running

    @property
    def stats(self) -> MailingStats:
        return self._stats

    def set_callbacks(
        self,
        on_progress: Optional[OnProgress] = None,
        on_log: Optional[OnLog] = None,
        on_finished: Optional[OnFinished] = None,
        on_spamblock: Optional[OnSpamBlock] = None,
    ) -> None:
        self._on_progress = on_progress
        self._on_log = on_log
        self._on_finished = on_finished
        self._on_spamblock = on_spamblock

    async def _log(self, msg: str) -> None:
        logger.info(msg)
        if self._on_log:
            try:
                await self._on_log(msg)
            except Exception:
                pass

    async def _notify_progress(self, remaining: int) -> None:
        if self._on_progress:
            try:
                await self._on_progress(self._stats, remaining)
            except Exception:
                pass

    async def _notify_spamblock(self, acc_label: str, reason: str) -> None:
        if self._on_spamblock:
            try:
                await self._on_spamblock(acc_label, reason)
            except Exception:
                pass

    def stop(self) -> None:
        self._stop_event.set()

    async def start(self) -> None:
        if self._is_running:
            return

        self._is_running = True
        self._stop_event.clear()
        self._stats.reset()

        try:
            cycle = 0
            while not self._stop_event.is_set():
                cycle += 1

                unprocessed = await self._db.get_unprocessed_identifiers(self._campaign_id)
                if not unprocessed:
                    await self._log("⚠️ Необработанных пользователей не осталось.")
                    break

                for ident in unprocessed:
                    await self._queue.put(ident)

                account_ids = [acc_id for acc_id in self._active_account_ids if self._manager.is_connected(acc_id)]
                if not account_ids:
                    await self._log("❌ Нет подключённых аккаунтов для рассылки.")
                    break

                if cycle == 1:
                    await self._log(
                        f"▶️ Начинаем рассылку. Пользователей: {len(unprocessed)}, аккаунтов: {len(account_ids)}"
                    )
                else:
                    await self._log(f"🔄 Цикл {cycle}. Осталось необработанных: {len(unprocessed)}")

                tasks = [
                    asyncio.create_task(self._worker(acc_id), name=f"worker_{acc_id}_c{cycle}")
                    for acc_id in account_ids
                ]
                await asyncio.gather(*tasks, return_exceptions=True)

                if self._stop_event.is_set():
                    break

                # Проверяем есть ли ещё необработанные и настроена ли пауза
                remaining = await self._db.get_unprocessed_identifiers(self._campaign_id)
                if not remaining or self._config.pause_between_cycles <= 0:
                    break

                await self._log(
                    f"⏸ Пауза между циклами: {self._config.pause_between_cycles}с "
                    f"(осталось {len(remaining)} пользователей)..."
                )
                # Спим кусками по 1с чтобы stop() срабатывал быстро
                for _ in range(int(self._config.pause_between_cycles)):
                    if self._stop_event.is_set():
                        break
                    await asyncio.sleep(1)

        finally:
            self._is_running = False
            while not self._queue.empty():
                try:
                    self._queue.get_nowait()
                except asyncio.QueueEmpty:
                    break

            await self._log(
                f"⏹ Рассылка завершена. Отправлено: {self._stats.sent}, "
                f"Ошибок: {self._stats.errors}, Заблокировано: {self._stats.blocked}"
            )
            if self._on_finished:
                try:
                    await self._on_finished()
                except Exception:
                    pass

    async def _worker(self, account_id: int) -> None:
        client = self._manager.get_client(account_id)
        if not client:
            await self._log(f"⚠️ Клиент аккаунта #{account_id} не найден, пропуск.")
            return

        account = await self._db.get_account(account_id)
        acc_label = account.phone if account else f"#{account_id}"

        while not self._stop_event.is_set():
            try:
                identifier = self._queue.get_nowait()
            except asyncio.QueueEmpty:
                break

            claimed = await self._db.try_claim(self._campaign_id, identifier, account_id)
            if not claimed:
                self._stats.skipped += 1
                continue

            try:
                await self._send_to_user(client, identifier, self._config)
                await self._db.mark_sent(self._campaign_id, identifier, account_id, "sent")
                self._stats.sent += 1
                await self._log(f"✅ [{acc_label}] → {identifier}")

            except FloodWaitError as e:
                await self._db.release_claim(self._campaign_id, identifier)
                await self._queue.put(identifier)
                await self._log(
                    f"⏳ [{acc_label}] FloodWait {e.seconds}с — ожидаем..."
                )
                await asyncio.sleep(e.seconds)
                continue

            except PeerFloodError:
                # Спам-блок: аккаунт заблокирован на отправку сообщений
                await self._db.release_claim(self._campaign_id, identifier)
                await self._queue.put(identifier)
                await self._log(
                    f"⛔ [{acc_label}] Спам-блок (PeerFlood) — рассылка остановлена!"
                )
                await self._db.update_account_status(account_id, "spamblock", 0)
                await self._notify_spamblock(acc_label, "PeerFlood — аккаунт получил спам-ограничение")
                self.stop()
                break

            except (UserPrivacyRestrictedError, UserNotMutualContactError):
                await self._db.mark_sent(self._campaign_id, identifier, account_id, "blocked", "privacy")
                self._stats.blocked += 1
                await self._log(f"🔒 [{acc_label}] → {identifier}: приватность")

            except InputUserDeactivatedError:
                await self._db.mark_sent(self._campaign_id, identifier, account_id, "blocked", "deactivated")
                self._stats.blocked += 1
                await self._log(f"👻 [{acc_label}] → {identifier}: аккаунт удалён")

            except (UsernameNotOccupiedError, UsernameInvalidError):
                await self._db.mark_sent(self._campaign_id, identifier, account_id, "error", "invalid_username")
                self._stats.errors += 1
                await self._log(f"❓ [{acc_label}] → {identifier}: username не найден")

            except PhoneNumberBannedError:
                # Аккаунт заблокирован Telegram — останавливаем всю рассылку
                await self._db.release_claim(self._campaign_id, identifier)
                await self._queue.put(identifier)
                await self._log(f"🚫 [{acc_label}] аккаунт заблокирован Telegram — рассылка остановлена!")
                await self._db.update_account_status(account_id, "banned", 0)
                await self._notify_spamblock(acc_label, "Аккаунт заблокирован Telegram (PhoneNumberBanned)")
                self.stop()
                break

            except Exception as e:
                err_msg = str(e)[:200]
                await self._db.mark_sent(self._campaign_id, identifier, account_id, "error", err_msg)
                self._stats.errors += 1
                await self._log(f"❌ [{acc_label}] → {identifier}: {err_msg}")

            remaining = self._queue.qsize()
            await self._notify_progress(remaining)
            if not self._stop_event.is_set():
                await asyncio.sleep(self._config.compute_delay())

    async def _resolve_numeric_id(self, client: TelegramClient, user_id: int):
        """Три попытки получить entity по числовому ID."""
        from telethon.tl.functions.users import GetUsersRequest
        from telethon.tl.types import InputUser, InputPeerUser, UserEmpty

        # 1. Кэш сессии — работает если аккаунт уже взаимодействовал с юзером
        try:
            return await client.get_entity(user_id)
        except Exception:
            pass

        # 2. GetUsersRequest с access_hash=0 — работает для публичных аккаунтов
        try:
            result = await client(GetUsersRequest([InputUser(user_id, 0)]))
            if result and not isinstance(result[0], UserEmpty) and getattr(result[0], "id", None):
                return result[0]
        except Exception:
            pass

        # 3. Прямой InputPeerUser(id, 0) — обходит Telethon-резолюцию,
        #    запрос уходит на сервер напрямую, сервер решает по настройкам приватности
        return InputPeerUser(user_id, 0)

    async def _send_to_user(
        self, client: TelegramClient, identifier: str, config: SendConfig
    ) -> None:
        if identifier.lstrip("-").isdigit():
            entity = await self._resolve_numeric_id(client, int(identifier))
        else:
            entity = await client.get_entity(identifier)

        final_text = evaluate_spintax(self._config.message_text or "")

        if self._config.attach_file_id:
            file_bytes = await self._download_file(self._config.attach_file_id, self._config.attach_file_name or "file")
            await client.send_file(
                entity,
                file_bytes,
                caption=final_text or None,
                parse_mode="html",
                force_document=True,
            )
        elif self._config.image_file_id:
            # Указываем имя с расширением .jpg — Telethon отправит как фото, не как файл
            file_bytes = await self._download_file(self._config.image_file_id, "photo.jpg")
            await client.send_file(
                entity,
                file_bytes,
                caption=final_text or None,
                parse_mode="html",
                force_document=False,
            )
        else:
            await client.send_message(
                entity,
                final_text,
                parse_mode="html",
            )

    async def _download_file(self, file_id: str, filename: str = "file") -> BytesIO:
        result = await self._bot.download(file_id)
        result.seek(0)
        # Telethon определяет тип по атрибуту name у BytesIO
        result.name = filename
        return result
