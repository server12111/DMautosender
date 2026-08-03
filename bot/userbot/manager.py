import asyncio
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from telethon import TelegramClient, events
from telethon.errors import (
    SessionPasswordNeededError,
    PhoneCodeInvalidError,
    PhoneCodeExpiredError,
    PasswordHashInvalidError,
    UserDeactivatedBanError,
    UserDeactivatedError,
    AuthKeyUnregisteredError,
    SessionRevokedError,
    AuthKeyDuplicatedError,
    FloodWaitError,
)
from telethon.tl.types import User
from telethon.tl.functions.users import GetFullUserRequest

from ..database.db import Database
from ..database.models import Account
from ..utils.proxy import parse_proxy

logger = logging.getLogger("dmsender.userbot")

_DEVICE_POOL = [
    {"device_model": "Samsung Galaxy A54", "system_version": "Android 13", "app_version": "10.14.5"},
    {"device_model": "Xiaomi Redmi Note 12", "system_version": "Android 12", "app_version": "10.13.2"},
    {"device_model": "OnePlus 11", "system_version": "Android 13", "app_version": "10.12.1"},
    {"device_model": "Google Pixel 7", "system_version": "Android 13", "app_version": "10.11.0"},
    {"device_model": "Samsung Galaxy S23", "system_version": "Android 13", "app_version": "10.14.1"},
]

_BAN_ERRORS = (
    UserDeactivatedBanError,
    UserDeactivatedError,
    AuthKeyUnregisteredError,
    SessionRevokedError,
    AuthKeyDuplicatedError,
)


@dataclass
class _PendingAuthorization:
    client: TelegramClient
    phone: str
    phone_code_hash: str
    user_id: int
    proxy: Optional[str]


class UserbotManager:
    def __init__(self, db: Database, sessions_path: Path) -> None:
        self._db = db
        self._sessions_path = sessions_path
        self._clients: dict[int, TelegramClient] = {}
        self._pending_authorizations: dict[int, _PendingAuthorization] = {}
        self._authorization_versions: dict[int, int] = {}
        self._authorization_finish_tasks: dict[int, asyncio.Task] = {}
        self._parser_jobs: dict[str, set[str]] = {}
        self._parser_job_owners: dict[str, int] = {}
        self._background_tasks: set[asyncio.Task] = set()

    def start_background(self, coroutine, name: str) -> asyncio.Task:
        """Start a managed tools job whose failures are always observed."""
        task = asyncio.create_task(coroutine, name=name)
        self._background_tasks.add(task)
        task.add_done_callback(self._background_task_done)
        return task

    def _background_task_done(self, task: asyncio.Task) -> None:
        self._background_tasks.discard(task)
        if task.cancelled():
            return
        try:
            task.result()
        except Exception:
            logger.exception("Фоновая задача %s завершилась с ошибкой", task.get_name())

    def _make_client(self, account: Account) -> TelegramClient:
        device = _DEVICE_POOL[account.id % len(_DEVICE_POOL)]
        session_name = account.session_file or str(
            self._sessions_path / account.phone.replace("+", "")
        )
        if account.session_file and not Path(account.session_file).exists():
            # Stored path is stale (e.g. the project directory was moved or
            # this DB was copied to a different host) — fall back to the
            # same filename inside the currently configured sessions dir.
            fallback = self._sessions_path / Path(account.session_file).name
            if fallback.exists():
                session_name = str(fallback)
        client = TelegramClient(
            session_name,
            account.api_id,
            account.api_hash,
            device_model=device["device_model"],
            system_version=device["system_version"],
            app_version=device["app_version"],
            proxy=parse_proxy(account.proxy) if getattr(account, "proxy", None) else None,
        )
        client.api_hash = str(account.api_hash)
        return client

    def _make_new_client(
        self, phone: str, api_id: int, api_hash: str, user_id: int,
        proxy: Optional[str],
    ) -> TelegramClient:
        device = _DEVICE_POOL[0]
        session_name = str(
            self._sessions_path / f"{user_id}_{phone.replace('+', '')}"
        )
        client = TelegramClient(
            session_name,
            api_id,
            api_hash,
            device_model=device["device_model"],
            system_version=device["system_version"],
            app_version=device["app_version"],
            proxy=parse_proxy(proxy) if proxy else None,
        )
        client.api_hash = str(api_hash)
        return client

    async def start_all(self) -> None:
        accounts = await self._db.get_all_active_accounts()
        sem = asyncio.Semaphore(5)

        async def _start_one(acc: Account) -> None:
            async with sem:
                await self._start_client(acc)

        await asyncio.gather(*[_start_one(a) for a in accounts], return_exceptions=True)
        logger.info("Запущено аккаунтов: %d из %d", len(self._clients), len(accounts))

    async def _start_client(self, account: Account) -> Optional[TelegramClient]:
        client: Optional[TelegramClient] = None
        try:
            client = self._make_client(account)
            await client.connect()
            if not await client.is_user_authorized():
                logger.warning("Аккаунт %s не авторизован, пропуск", account.phone)
                await client.disconnect()
                await self._db.update_account_status(account.id, "not_authorized", 0)
                return None
            self._clients[account.id] = client
            self._attach_autoresponder(client, account.user_id)
            logger.info("Аккаунт %s подключён", account.phone)
            return client
        except _BAN_ERRORS as e:
            logger.error("Аккаунт %s заблокирован: %s", account.phone, e)
            await self._db.update_account_status(account.id, "banned", 0)
            return None
        except FloodWaitError as e:
            logger.warning("Аккаунт %s: FloodWait %ds при подключении", account.phone, e.seconds)
            await self._db.update_account_status(account.id, "flood_wait", 1)
            return None
        except Exception as e:
            logger.error("Ошибка подключения аккаунта %s: %s", account.phone, e)
            return None
        finally:
            if (
                client
                and account.id not in self._clients
                and client.is_connected()
            ):
                try:
                    await client.disconnect()
                except Exception:
                    logger.exception(
                        "Не удалось закрыть подключение аккаунта %s",
                        account.phone,
                    )

    def _attach_autoresponder(self, client: TelegramClient, user_id: int) -> None:
        @client.on(events.NewMessage(incoming=True))
        async def autoresponder_handler(event: events.NewMessage.Event):
            if not event.is_private:
                return
            
            # 1. Проверяем включён ли автоответчик
            is_on = await self._db.get_setting(user_id, "autoresponder_on", "0")
            if is_on != "1":
                return
            
            # 2. Получаем текст
            text = await self._db.get_setting(user_id, "autoresponder_text", "")
            if not text:
                return
            
            # 2.5 Проверяем подписку
            from ..services.subscription import SubscriptionService
            svc = SubscriptionService(self._db)
            sub = await svc.get_subscription(user_id)
            if not sub or sub.plan == "free":
                return
            
            
            # 3. Проверяем лог
            sender_id = event.sender_id
            if not sender_id:
                return
            
            # 4. Пишем в лог. Если уже отвечали - выходим.
            should_reply = await self._db.check_and_log_autoresponder(user_id, sender_id)
            if should_reply:
                from ..utils.spintax import evaluate_spintax
                try:
                    final_text = evaluate_spintax(text)
                    await event.reply(final_text)
                    logger.info("Сработал автоответчик для %s", sender_id)
                except Exception:
                    await self._db.remove_autoresponder_log(user_id, sender_id)
                    raise

    async def reconnect(self, account_id: int) -> bool:
        account = await self._db.get_account(account_id)
        if not account:
            return False
        await self.disconnect_account(account_id)
        client = await self._start_client(account)
        return client is not None

    async def disconnect_account(self, account_id: int) -> None:
        client = self._clients.pop(account_id, None)
        if client:
            try:
                await client.disconnect()
            except Exception:
                logger.exception(
                    "Не удалось отключить аккаунт %s", account_id
                )

    async def disconnect_all(self) -> None:
        finish_tasks = list(self._authorization_finish_tasks.values())
        for task in finish_tasks:
            task.cancel()
        if finish_tasks:
            await asyncio.gather(*finish_tasks, return_exceptions=True)
        self._authorization_finish_tasks.clear()

        tasks = list(self._background_tasks)
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self._background_tasks.clear()

        for client in self._clients.values():
            try:
                await client.disconnect()
            except Exception:
                pass
        self._clients.clear()
        for pending in self._pending_authorizations.values():
            try:
                await pending.client.disconnect()
            except Exception:
                pass
        self._pending_authorizations.clear()

    def get_client(self, account_id: int) -> Optional[TelegramClient]:
        return self._clients.get(account_id)

    def active_account_ids(self) -> list[int]:
        return list(self._clients.keys())

    def is_connected(self, account_id: int) -> bool:
        return account_id in self._clients

    # ── Authorization flow ────────────────────────────────────────────────────

    async def send_code(
        self, phone: str, api_id: int, api_hash: str, user_id: int = 0,
        proxy: Optional[str] = None, auth_key: int = 0,
    ) -> str:
        """Запросить SMS-код. Возвращает phone_code_hash."""
        if not auth_key or not user_id:
            raise ValueError("Не указан владелец авторизации")
        await self.cancel_authorization(auth_key)
        authorization_version = self._authorization_versions[auth_key]
        client = self._make_new_client(phone, api_id, api_hash, user_id, proxy)
        try:
            await client.connect()
            result = await client.send_code_request(phone)
        except Exception:
            await client.disconnect()
            raise
        if self._authorization_versions.get(auth_key) != authorization_version:
            await client.disconnect()
            raise RuntimeError("Авторизация была отменена")
        self._pending_authorizations[auth_key] = _PendingAuthorization(
            client=client,
            phone=phone,
            phone_code_hash=result.phone_code_hash,
            user_id=user_id,
            proxy=proxy,
        )
        return result.phone_code_hash

    async def sign_in(self, code: str, auth_key: int) -> User:
        """Войти с кодом. Raises SessionPasswordNeededError если нужна 2FA."""
        pending = self._pending_authorizations.get(auth_key)
        if not pending:
            raise RuntimeError("Сначала вызовите send_code()")
        return await pending.client.sign_in(
            pending.phone,
            code,
            phone_code_hash=pending.phone_code_hash,
        )

    async def sign_in_2fa(self, password: str, auth_key: int) -> User:
        """Войти с паролем 2FA."""
        pending = self._pending_authorizations.get(auth_key)
        if not pending:
            raise RuntimeError("Сначала вызовите send_code()")
        return await pending.client.sign_in(password=password)

    async def finish_authorization(
        self, api_id: int, api_hash: str, auth_key: int
    ) -> Account:
        task = self._authorization_finish_tasks.get(auth_key)
        if task is None:
            task = asyncio.create_task(
                self._finish_authorization_once(api_id, api_hash, auth_key),
                name=f"finish_authorization_{auth_key}",
            )
            self._authorization_finish_tasks[auth_key] = task
        try:
            return await task
        finally:
            if task.done() and self._authorization_finish_tasks.get(auth_key) is task:
                self._authorization_finish_tasks.pop(auth_key, None)

    async def _finish_authorization_once(
        self, api_id: int, api_hash: str, auth_key: int
    ) -> Account:
        """Сохранить авторизованный аккаунт в БД и добавить в менеджер."""
        pending = self._pending_authorizations.get(auth_key)
        if not pending:
            raise RuntimeError("Нет незавершённой авторизации аккаунта")
        client = pending.client
        phone = pending.phone
        user_id = pending.user_id

        me: User = await client.get_me()
        name = f"{me.first_name or ''} {me.last_name or ''}".strip() or phone
        phone_clean = phone.replace("+", "")
        session_file = str(
            self._sessions_path / f"{user_id}_{phone_clean}.session"
        )

        existing = await self._db.get_account_by_phone(user_id, phone)
        pending_proxy = pending.proxy
        from ..services.subscription import SubscriptionService
        service = SubscriptionService(self._db)
        max_accounts = await service.get_limit(user_id, "max_accounts")
        if not existing:
            current_count = await self._db.count_user_accounts(user_id)
            if not await service.can_add_account(user_id, current_count):
                raise RuntimeError("Лимит аккаунтов вашего тарифа исчерпан")
        account_id = await self._db.add_account(
            user_id,
            phone,
            api_id,
            api_hash,
            name,
            session_file,
            proxy=pending_proxy,
            max_accounts=max_accounts,
        )
        if account_id is None:
            raise RuntimeError("Лимит аккаунтов вашего тарифа исчерпан")

        previous_client = self._clients.get(account_id)
        if previous_client and previous_client is not client:
            try:
                await previous_client.disconnect()
            except Exception:
                logger.exception("Не удалось закрыть старое подключение аккаунта %s", account_id)
        self._clients[account_id] = client
        self._attach_autoresponder(client, user_id)

        del self._pending_authorizations[auth_key]

        account = await self._db.get_account(account_id)
        logger.info("Аккаунт %s успешно авторизован: %s", phone, name)
        return account

    async def cancel_authorization(self, auth_key: int) -> None:
        self._authorization_versions[auth_key] = (
            self._authorization_versions.get(auth_key, 0) + 1
        )
        pending = self._pending_authorizations.pop(auth_key, None)
        if pending:
            try:
                await pending.client.disconnect()
            except Exception:
                pass

    async def remove_account(self, account_id: int) -> None:
        account = await self._db.get_account(account_id)
        if account_id in self._clients:
            try:
                await self._clients[account_id].disconnect()
            except Exception:
                pass
            del self._clients[account_id]
        await self._db.delete_account(account_id)
        if account and account.session_file:
            sessions_root = self._sessions_path.resolve()
            session_path = Path(account.session_file)
            candidates = [session_path]
            if session_path.suffix != ".session":
                candidates.append(Path(str(session_path) + ".session"))
            for base_path in candidates:
                for suffix in ("", "-journal", "-wal", "-shm"):
                    target = Path(str(base_path) + suffix).resolve()
                    try:
                        target.relative_to(sessions_root)
                    except ValueError:
                        logger.warning(
                            "Пропуск удаления session вне каталога сессий: %s",
                            target,
                        )
                        continue
                    try:
                        target.unlink(missing_ok=True)
                    except OSError:
                        logger.exception(
                            "Не удалось удалить session-файл %s", target
                        )

    # ── Advanced Parser ────────────────────────────────────────────────────

    def get_parser_partial_result(
        self, job_id: str, tg_user_id: int
    ) -> Optional[str]:
        if (
            job_id not in self._parser_jobs
            or self._parser_job_owners.get(job_id) != tg_user_id
        ):
            return None
        import tempfile
        import os
        results = self._parser_jobs[job_id]
        if not results:
            return None
        
        fd, path = tempfile.mkstemp(suffix=".txt")
        with os.fdopen(fd, 'w', encoding='utf-8') as f:
            for r in results:
                f.write(f"{r}\n")
        return path

    async def advanced_parse_groups(self, account_id: int, groups: list[str], mode: str, status_msg, tg_user_id: int, campaign_id: int = None) -> None:
        client = self.get_client(account_id)
        if not client:
            from ..utils.emoji import e
            from ..keyboards.inline import tools_menu_kb
            await status_msg.edit_text(f"{e('❌')} Ошибка: клиент отключен.", reply_markup=tools_menu_kb())
            return
            
        import time
        from ..utils.emoji import e
        from ..keyboards.inline import tools_menu_kb, cancel_kb
        from aiogram.utils.keyboard import InlineKeyboardBuilder
        from ..keyboards.inline import _btn
        import uuid
        import os
        from aiogram.types import FSInputFile
        
        job_id = str(uuid.uuid4())[:8]
        self._parser_jobs[job_id] = set()
        self._parser_job_owners[job_id] = tg_user_id
        
        start_time = time.time()
        total_groups = len(groups)
        processed_groups = 0
        total_found = 0
        premium_count = 0
        nft_count = 0
        gifts_count = 0
        errors = 0
        
        async def update_status():
            elapsed = time.time() - start_time
            if elapsed == 0: elapsed = 1
            rate = total_found / elapsed
            progress_pct = (processed_groups / total_groups) * 100 if total_groups else 0
            
            b = InlineKeyboardBuilder()
            b.row(_btn(text=f"📥 Скачать собранное ({len(self._parser_jobs[job_id])})", callback_data=f"parser:download:{job_id}"))

            txt = (
                f"{e('🛠')} <b>Парсинг в процессе...</b>\n\n"
                f"Обработано групп: <b>{processed_groups}/{total_groups}</b> ({progress_pct:.1f}%)\n"
                f"Проверено пользователей: <b>{processed_users}</b>\n"
                f"Найдено (подходят): <b>{total_found}</b>\n"
                f"Premium: <b>{premium_count}</b> | Подарки: <b>{gifts_count}</b>\n"
                f"Ошибок: <b>{errors}</b>\n\n"
                f"⏳ Прошло: <b>{int(elapsed//60)}м {int(elapsed%60)}с</b>"
            )
            try:
                await status_msg.edit_text(txt, reply_markup=b.as_markup(), parse_mode="HTML")
            except Exception:
                pass
                
        logger.info(f"Старт расширенного парсинга. Групп: {total_groups}, Режим: {mode}")
        
        processed_users = 0
        await update_status()  # Update UI immediately so it doesn't hang on "Подготовка"
        
        for group in groups:
            try:
                logger.info(f"Парсинг группы {group}...")
                participants = await client.get_participants(group, limit=None)
                
                for p in participants:
                    processed_users += 1
                    if processed_users % 10 == 0:
                        await update_status()
                        
                    if getattr(p, 'bot', False) or getattr(p, 'deleted', False):
                        continue
                        
                    is_premium = getattr(p, 'premium', False)
                    is_gifts = False
                    
                    if mode == "premium" and not is_premium:
                        continue
                        
                    if mode == "premium_gifts" and not is_premium:
                        continue
                        
                    if mode == "gifts" or mode == "premium_gifts":
                        try:
                            full = await client(GetFullUserRequest(p.id))
                            # Rate limiting protection for ALL requests
                            await asyncio.sleep(0.3)
                            
                            stargifts_count = getattr(full.full_user, 'stargifts_count', 0)
                            if stargifts_count and stargifts_count > 0:
                                from telethon.tl.functions.payments import GetSavedStarGiftsRequest
                                from telethon.tl.types import StarGiftUnique
                                
                                gifts_req = await client(GetSavedStarGiftsRequest(peer=p.id, offset='', limit=100))
                                await asyncio.sleep(0.3)
                                
                                has_nft = False
                                for sg in gifts_req.gifts:
                                    if isinstance(getattr(sg, 'gift', None), StarGiftUnique):
                                        has_nft = True
                                        break
                                        
                                if has_nft:
                                    is_gifts = True
                                else:
                                    continue
                            else:
                                continue
                        except FloodWaitError as err:
                            logger.warning(f"FloodWait в режиме gifts на {err.seconds} секунд")
                            await asyncio.sleep(err.seconds)
                            continue
                        except Exception as err:
                            continue
                            
                    username_str = getattr(p, 'username', None)
                    if not username_str:
                        un_list = getattr(p, 'usernames', [])
                        for un in un_list:
                            if getattr(un, 'active', False):
                                username_str = un.username
                                break
                                
                    ident = f"@{username_str}" if username_str else str(p.id)
                    if ident not in self._parser_jobs[job_id]:
                        self._parser_jobs[job_id].add(ident)
                        total_found += 1
                        if is_premium: premium_count += 1
                        if is_gifts: gifts_count += 1
                        
            except FloodWaitError as err:
                logger.warning(f"FloodWait на {err.seconds} секунд при парсинге {group}")
                errors += 1
                await asyncio.sleep(err.seconds)
            except Exception as err:
                logger.error(f"Ошибка парсинга {group}: {err}")
                errors += 1
                
            processed_groups += 1
            await update_status()
            
        # Finish
        elapsed = time.time() - start_time
        logger.info(f"Парсинг завершен. Собрано: {total_found}")
        
        import tempfile
        results = self._parser_jobs[job_id]
        if not results:
            from ..keyboards.inline import back_to_tools_kb
            self._parser_jobs.pop(job_id, None)
            self._parser_job_owners.pop(job_id, None)
            await status_msg.edit_text(f"{e('❌')} Никого не найдено или группы недоступны.", reply_markup=back_to_tools_kb())
            return
            
        fd, path = tempfile.mkstemp(suffix=".txt")
        with os.fdopen(fd, 'w', encoding='utf-8') as f:
            for r in results:
                f.write(f"{r}\n")
                
        try:
            doc = FSInputFile(path, filename=f"parsed_users_{job_id}.txt")
            
            final_txt = (
                f"{e('✅')} <b>Парсинг завершен!</b>\n\n"
                f"Обработано групп: <b>{processed_groups}/{total_groups}</b>\n"
                f"Уникальных пользователей: <b>{total_found}</b>\n"
                f"Premium: <b>{premium_count}</b> | NFT: <b>{gifts_count}</b>\n"
                f"Ошибок групп: <b>{errors}</b>\n"
                f"Время работы: <b>{int(elapsed//60)}м {int(elapsed%60)}с</b>\n\n"
                f"Файл прикреплен к сообщению"
            )
            await status_msg.delete()
            from ..keyboards.inline import back_to_tools_kb
            await status_msg.answer_document(doc, caption=final_txt, parse_mode="HTML", reply_markup=back_to_tools_kb())
        except Exception as exc:
            logger.error(f"Failed to send final file: {exc}")
            from ..keyboards.inline import back_to_tools_kb
            await status_msg.edit_text(f"{e('❌')} Ошибка отправки файла.", reply_markup=back_to_tools_kb())
        finally:
            try:
                os.remove(path)
            except OSError:
                pass
            self._parser_jobs.pop(job_id, None)
            self._parser_job_owners.pop(job_id, None)

    async def advanced_parse_users_list(self, account_id: int, users_list: list[str], mode: str, status_msg, tg_user_id: int) -> None:
        client = self.get_client(account_id)
        if not client:
            from ..utils.emoji import e
            from ..keyboards.inline import back_to_tools_kb
            await status_msg.edit_text(f"{e('❌')} Ошибка: клиент отключен.", reply_markup=back_to_tools_kb())
            return
            
        import time
        from ..utils.emoji import e
        from ..keyboards.inline import tools_menu_kb
        import uuid
        import os
        from aiogram.types import FSInputFile
        from telethon.tl.functions.users import GetFullUserRequest
        from telethon.errors import FloodWaitError
        
        job_id = str(uuid.uuid4())[:8]
        self._parser_jobs[job_id] = set()
        self._parser_job_owners[job_id] = tg_user_id
        
        start_time = time.time()
        total_users = len(users_list)
        processed_users = 0
        total_found = 0
        premium_count = 0
        nft_count = 0
        gifts_count = 0
        errors = 0
        
        async def update_status():
            elapsed = time.time() - start_time
            txt = (
                f"{e('🛠')} <b>Парсинг базы в процессе...</b>\n\n"
                f"Проверено пользователей: <b>{processed_users}/{total_users}</b>\n"
                f"Найдено (подходят): <b>{total_found}</b>\n"
                f"Premium: <b>{premium_count}</b> | Подарки: <b>{gifts_count}</b>\n"
                f"Ошибок: <b>{errors}</b>\n\n"
                f"⏳ Прошло: <b>{int(elapsed//60)}м {int(elapsed%60)}с</b>"
            )
            try:
                await status_msg.edit_text(txt, parse_mode="HTML")
            except Exception:
                pass

        logger.info(f"Старт парсинга базы ({total_users} строк). Режим: {mode}")
        await update_status()
        
        for user_ident in users_list:
            if processed_users % 10 == 0 and processed_users > 0:
                await update_status()
                
            is_premium = False
            is_gifts = False
            
            try:
                full = await client(GetFullUserRequest(user_ident))
                await asyncio.sleep(0.3)
                
                u = full.full_user if hasattr(full, 'full_user') else full
                user_obj = full.users[0] if hasattr(full, 'users') and full.users else None
                
                if user_obj:
                    is_premium = getattr(user_obj, 'premium', False)
                
                if mode == "premium" and not is_premium:
                    processed_users += 1
                    continue
                if mode == "premium_gifts" and not is_premium:
                    processed_users += 1
                    continue
                    
                if mode in ["gifts", "premium_gifts"]:
                    stargifts_count = getattr(u, 'stargifts_count', 0)
                    if stargifts_count and stargifts_count > 0:
                        from telethon.tl.functions.payments import GetSavedStarGiftsRequest
                        from telethon.tl.types import StarGiftUnique
                        try:
                            gifts_req = await client(GetSavedStarGiftsRequest(peer=user_obj.id if user_obj else user_ident, offset='', limit=100))
                            await asyncio.sleep(0.3)
                            has_nft = False
                            for sg in gifts_req.gifts:
                                if isinstance(getattr(sg, 'gift', None), StarGiftUnique):
                                    has_nft = True
                                    break
                            if has_nft:
                                is_gifts = True
                            else:
                                processed_users += 1
                                continue
                        except FloodWaitError as err:
                            logger.warning(f"FloodWait в режиме gifts на {err.seconds} секунд")
                            await asyncio.sleep(err.seconds)
                            processed_users += 1
                            continue
                        except Exception:
                            processed_users += 1
                            continue
                    else:
                        processed_users += 1
                        continue
                        
                # Add to results
                ident = user_ident
                if user_obj:
                    username_str = getattr(user_obj, 'username', None)
                    if username_str:
                        ident = f"@{username_str}"
                    else:
                        ident = str(user_obj.id)
                        
                if ident not in self._parser_jobs[job_id]:
                    self._parser_jobs[job_id].add(ident)
                    total_found += 1
                    if is_premium: premium_count += 1
                    if is_gifts: gifts_count += 1
                    
            except FloodWaitError as err:
                logger.warning(f"FloodWait на {err.seconds} секунд при парсинге {user_ident}")
                errors += 1
                await asyncio.sleep(err.seconds)
            except Exception as err:
                errors += 1
                
            processed_users += 1
            
        await update_status()
        
        # Finish
        elapsed = time.time() - start_time
        logger.info(f"Парсинг базы завершен. Собрано: {total_found}")
        
        results = self._parser_jobs[job_id]
        if not results:
            from ..keyboards.inline import back_to_tools_kb
            self._parser_jobs.pop(job_id, None)
            self._parser_job_owners.pop(job_id, None)
            await status_msg.edit_text(f"{e('❌')} Никто не подошел под критерии.", reply_markup=back_to_tools_kb())
            return
            
        import tempfile
        fd, path = tempfile.mkstemp(suffix=".txt")
        with os.fdopen(fd, 'w', encoding='utf-8') as f:
            for r in results:
                f.write(f"{r}\n")
                
        try:
            doc = FSInputFile(path, filename=f"parsed_base_{job_id}.txt")
            final_txt = (
                f"{e('✅')} <b>Парсинг базы завершен!</b>\n\n"
                f"Обработано строк: <b>{processed_users}/{total_users}</b>\n"
                f"Найдено подходящих: <b>{total_found}</b>\n"
                f"Premium: <b>{premium_count}</b> | NFT: <b>{gifts_count}</b>\n"
                f"Ошибок: <b>{errors}</b>\n"
                f"Время работы: <b>{int(elapsed//60)}м {int(elapsed%60)}с</b>\n\n"
                f"Файл прикреплен к сообщению"
            )
            await status_msg.delete()
            from ..keyboards.inline import back_to_tools_kb
            await status_msg.answer_document(doc, caption=final_txt, parse_mode="HTML", reply_markup=back_to_tools_kb())
        except Exception as exc:
            logger.error(f"Failed to send final file: {exc}")
            from ..keyboards.inline import back_to_tools_kb
            await status_msg.edit_text(f"{e('❌')} Ошибка отправки файла.", reply_markup=back_to_tools_kb())
        finally:
            try:
                os.remove(path)
            except OSError:
                pass
            self._parser_jobs.pop(job_id, None)
            self._parser_job_owners.pop(job_id, None)

    async def check_phones(self, account_id: int, phones: list[str], status_msg, tg_user_id: int) -> None:
        client = self.get_client(account_id)
        if not client:
            from ..utils.emoji import e
            from ..keyboards.inline import back_to_tools_kb
            await status_msg.edit_text(f"{e('❌')} Ошибка: клиент отключен.", reply_markup=back_to_tools_kb())
            return
            
        import time
        import re
        from ..utils.emoji import e
        from ..keyboards.inline import tools_menu_kb
        import os
        import tempfile
        from aiogram.types import FSInputFile
        from telethon.tl.functions.contacts import ImportContactsRequest, DeleteContactsRequest
        from telethon.tl.types import InputPhoneContact
        from telethon.errors import FloodWaitError
        
        start_time = time.time()
        
        # Clean phones
        clean_phones = []
        seen_phones = set()
        for p in phones:
            cleaned = re.sub(r'[^\d+]', '', p)
            if not cleaned.startswith('+'):
                if cleaned.startswith('7') or cleaned.startswith('8'):
                    cleaned = '+7' + cleaned[1:]
                else:
                    cleaned = '+' + cleaned
            if cleaned not in seen_phones:
                seen_phones.add(cleaned)
                clean_phones.append(cleaned)
                
        total_phones = len(clean_phones)
        processed = 0
        found_users = []
        errors = 0
        
        async def update_status():
            elapsed = time.time() - start_time
            txt = (
                f"{e('📱')} <b>Чекер номеров в процессе...</b>\n\n"
                f"Проверено номеров: <b>{processed}/{total_phones}</b>\n"
                f"Найдено Telegram: <b>{len(found_users)}</b>\n"
                f"Ошибок: <b>{errors}</b>\n\n"
                f"⏳ Прошло: <b>{int(elapsed//60)}м {int(elapsed%60)}с</b>"
            )
            try:
                await status_msg.edit_text(txt, parse_mode="HTML")
            except Exception:
                pass

        logger.info(f"Старт чекера ({total_phones} номеров)")
        await update_status()
        
        chunk_size = 50
        for i in range(0, total_phones, chunk_size):
            chunk = clean_phones[i:i+chunk_size]
            contacts = []
            for j, phone in enumerate(chunk):
                contacts.append(InputPhoneContact(
                    client_id=j,
                    phone=phone,
                    first_name=f"chk_{i}_{j}",
                    last_name=""
                ))
                
            users_to_delete = []
            try:
                result = await client(ImportContactsRequest(contacts))
                
                # result contains users and imported
                for user in result.users:
                    users_to_delete.append(user.id)
                    username = getattr(user, 'username', None)
                    if username:
                        found_users.append(f"@{username}")
                    else:
                        found_users.append(str(user.id))
                        
            except FloodWaitError as err:
                logger.warning(f"FloodWait на {err.seconds} секунд при проверке номеров")
                errors += len(chunk)
                await asyncio.sleep(err.seconds)
            except Exception as err:
                logger.error(f"Ошибка при проверке пакета номеров: {err}")
                errors += len(chunk)
            finally:
                if users_to_delete:
                    try:
                        await client(
                            DeleteContactsRequest(id=users_to_delete)
                        )
                    except FloodWaitError as err:
                        logger.warning(
                            "FloodWait %sс при очистке временных контактов",
                            err.seconds,
                        )
                        await asyncio.sleep(err.seconds)
                        try:
                            await client(
                                DeleteContactsRequest(id=users_to_delete)
                            )
                        except Exception:
                            logger.exception(
                                "Не удалось удалить временные контакты"
                            )
                    except Exception:
                        logger.exception(
                            "Не удалось удалить временные контакты"
                        )
                
            processed += len(chunk)
            await update_status()
            await asyncio.sleep(1) # Prevent aggressive rate limits
            
        # Finish
        elapsed = time.time() - start_time
        logger.info(f"Чекер номеров завершен. Найдено: {len(found_users)}")
        
        if not found_users:
            from ..keyboards.inline import back_to_tools_kb
            await status_msg.edit_text(f"{e('❌')} Ни одного аккаунта не найдено.", reply_markup=back_to_tools_kb())
            return
            
        fd, path = tempfile.mkstemp(suffix=".txt")
        with os.fdopen(fd, 'w', encoding='utf-8') as f:
            for r in found_users:
                f.write(f"{r}\n")
                
        try:
            doc = FSInputFile(path, filename=f"checked_phones.txt")
            final_txt = (
                f"{e('✅')} <b>Проверка номеров завершена!</b>\n\n"
                f"Обработано номеров: <b>{processed}/{total_phones}</b>\n"
                f"Найдено Telegram аккаунтов: <b>{len(found_users)}</b>\n"
                f"Ошибок: <b>{errors}</b>\n"
                f"Время работы: <b>{int(elapsed//60)}м {int(elapsed%60)}с</b>\n\n"
                f"Файл прикреплен к сообщению"
            )
            await status_msg.delete()
            from ..keyboards.inline import back_to_tools_kb
            await status_msg.answer_document(doc, caption=final_txt, parse_mode="HTML", reply_markup=back_to_tools_kb())
        except Exception as exc:
            logger.error(f"Failed to send checked phones file: {exc}")
            from ..keyboards.inline import back_to_tools_kb
            await status_msg.edit_text(f"{e('❌')} Ошибка отправки файла.", reply_markup=back_to_tools_kb())
        finally:
            try:
                os.remove(path)
            except OSError:
                pass
