from typing import Optional
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

from ..database.models import Account, Campaign, Subscription, PLAN_LIMITS
from ..config import config

PREMIUM_EMOJIS = {'✅': '5206607081334906820', '✔️': '5206607081334906820', '❌': '5210952531676504517', '🟢': '5416081784641168838', '🔴': '5411225014148014586', '➕': '5397916757333654639', '◀️': '5213358684024877471', '⬅️': '5213358684024877471', '👤': '5373012449597335010', '👋': '5343984088493599366', '📋': '5435970940670320222', '📄': '5435970940670320222', '📝': '5334882760735598374', '📁': '5433653135799228968', '💳': '5445353829304387411', '💸': '6154462623615159832', '💰': '6154462623615159832', '🤝': '5395732581780040886', 'ℹ️': '5334544901428229844', '⚠️': '5447644880824181073', '❗️': '5274099962655816924', '❗': '5274099962655816924', '⛔️': '5260293700088511294', '⛔': '5260293700088511294', '🆘': '5388805667114988189', '⚡️': '5456140674028019486', '⚡': '5456140674028019486', '🚀': '5445284980978621387', '🔥': '5389038097860144794', '⭐️': '5438496463044752972', '⭐': '5438496463044752972', '⏰': '5215394081911351762', '⏱️': '5373236586760651455', '⌛': '5386367538735104399', '📱': '5377501751278576553', '🔑': '5307843983102204243', '🗑️': '5445267414562389170', '🗑': '5445267414562389170', '📢': '5424818078833715060', '📣': '5424818078833715060', '📊': '5231200819986047254', '🎟️': '5431653802753159903', '🎟': '5431653802753159903', '✏️': '5395444784611480792', '🌐': '5447410659077661506', '🤖': '5172522439917175584', '💬': '5443038326535759644', '🔔': '5458603043203327669', '🎯': '5310278924616356636', '⚙️': '5341715473882955310', '🔧': '5462921117423384478', '🛠': '5462921117423384478', '📤': '5433614747381538714', '📥': '5433811242135331842', '🆔': '5334890573281114250', '🔗': '5271604874419647061', '📅': '5413879192267805083', '🗓': '5413879192267805083', '📞': '5467539229468793355', '🏠': '5416041192905265756', '⚠': '5447644880824181073', '✏': '5395444784611480792', '⏱': '5373236586760651455', '◀': '5213358684024877471', '⚙': '5341715473882955310', '✉': '5253742260054409879', '🔃': '5346269127059196142', '🔄': '5346269127059196142', '➡️': '5416117059207572332', '➡': '5416117059207572332', '💾': '5373342633798167891', '⏭️': '5240148091562125943', '⏭': '5240148091562125943', '⏳': '5175181110572745347', '👥': '5372926953978341366', '👇': '5305522282695768654', '🇷🇺': '5449408995691341691', '🇺🇦': '5427265669026564448', '💎': '5235630047959727475', '💠': '5388581564311417657', '💵': '5409048419211682843', '📌': '5397782960512444700', '✉️': '5253742260054409879', '📨': '5253742260054409879', '📬': '5350421256627838238', '📲': '5406809207947142040', '📸': '5235837920081887219', '🔇': '5462990730253319917', '🔐': '5821453562680448557', '0️⃣': '5226929552319594190', '🔢': '6323436631428695574', '🔤': '5335044092592142723', '🚫': '5240241223632954241', '❓': '5436113877181941026', '⚫': '5298839734489986848', '↩️': '5436118790624518351', '↩': '5436118790624518351', '📦': '5854908544712707500', '🔜': '5440621591387980068', '▶️': '5359782292468287371', '▶': '5359782292468287371', '🎲': '5384474763827620477', '📡': '5413337163100083587', '🔁': '5346269127059196142', '🔒': '5429405838345265327', '🛒': '5312361253610475399', '🧲': '5395732581780040886'}

def _btn(text: str, callback_data: str = None, url: str = None, style: str = None) -> InlineKeyboardButton:
    icon_id = None
    clean_text = text.strip()
    
    # Check for emojis at the start of the string
    for emoji, eid in PREMIUM_EMOJIS.items():
        if clean_text.startswith(emoji):
            icon_id = eid
            clean_text = clean_text[len(emoji):].strip()
            # Set default styles based on emoji if no style provided
            if not style:
                if emoji in ["✅", "✔️", "🟢", "➕", "🚀", "💸", "💰", "💵", "💳", "💾", "🤝"]:
                    style = "success"
                elif emoji in ["❌", "🔴", "🗑️", "🗑", "⛔️", "⛔", "🚫", "🔇"]:
                    style = "danger"
                elif emoji in ["👤", "👥", "💎", "⭐", "⭐️", "⚙️", "⚙", "🤖", "📊", "🔗", "🔑", "🔐", "🔒"]:
                    style = "primary"
            break
            
    # Default fallback
    if not style:
        style = "primary"
        if "Отмена" in clean_text or "Удалить" in clean_text or "Назад" in clean_text:
            style = "danger"
        elif "Подтвердить" in clean_text or "Да," in clean_text:
            style = "success"
            
    kwargs = {"text": clean_text, "style": style}
    if icon_id:
        kwargs["icon_custom_emoji_id"] = icon_id
        
    if url:
        kwargs["url"] = url
    else:
        kwargs["callback_data"] = callback_data
        
    return InlineKeyboardButton(**kwargs)

def _back_btn() -> InlineKeyboardButton:
    return _btn(text="Назад", callback_data="menu:main")

def _main_btn() -> InlineKeyboardButton:
    return _btn(text="Главное меню", callback_data="menu:main")

def main_menu_kb(is_admin: bool = False, plan: str = "free") -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(_btn(text="⭐ Подписка", callback_data="sub:plans"))
    builder.row(_btn(text="🚀 Рассылки", callback_data="campaigns:list"))
    builder.row(
        _btn(text="👤 Мои аккаунты", callback_data="accounts:list"),
        _btn(text="🧲 Инструменты", callback_data="tools:menu")
    )
    builder.row(_btn(text="💎 Профиль", callback_data="profile:show"))
    if is_admin:
        builder.row(_btn(text="⚙️ Админ-панель", callback_data="admin:panel"))
    return builder.as_markup()

def cancel_kb(back_data: str = "menu:main") -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(_btn(text="Назад", callback_data=back_data))
    return builder.as_markup()

def back_to_menu_kb() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(_main_btn())
    return builder.as_markup()

# --- Campaigns ---
def campaigns_list_kb(campaigns: list[Campaign]) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for c in campaigns:
        status_emoji = "⏳" if c.status == "draft" else "🚀" if c.status == "running" else "✅" if c.status == "completed" else "⏸"
        builder.row(_btn(text=f"{status_emoji} {c.name}", callback_data=f"campaign:view:{c.id}"))
    
    builder.row(_btn(text="Создать рассылку", callback_data="campaigns:create"))
    builder.row(_main_btn())
    return builder.as_markup()

def cancel_to_camp_kb() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(_btn(text="Отмена", callback_data="campaigns:list"))
    return builder.as_markup()

def confirm_delete_camp_kb(camp_id: int) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(
        _btn(text="Да, удалить", callback_data=f"camp:del:yes:{camp_id}"),
        _btn(text="Нет", callback_data=f"campaign:view:{camp_id}")
    )
    return builder.as_markup()

def campaign_view_kb(camp: Campaign) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    if camp.status in ("draft", "stopped"):
        builder.row(_btn(text="Добавить получателей", callback_data=f"database:menu:{camp.id}"))
        builder.row(_btn(text="📱 Аккаунты", callback_data=f"campaign:accounts:{camp.id}"))
        builder.row(_btn(text="📝 Изменить сообщение", callback_data=f"compose:menu:{camp.id}"))
        builder.row(
            _btn(text="⏱ Отложить", callback_data=f"delays:menu:{camp.id}"),
            _btn(text="Запустить", callback_data=f"mailing:start:{camp.id}")
        )
    elif camp.status == "running":
        builder.row(_btn(text="Остановить", callback_data=f"mailing:stop:{camp.id}"))
    elif camp.status == "paused":
        builder.row(_btn(text="Продолжить", callback_data=f"mailing:start:{camp.id}"))
    
    if camp.status != "running":
        builder.row(_btn(text="Удалить рассылку", callback_data=f"campaign:delete:{camp.id}"))
    builder.row(_btn(text="Назад к списку", callback_data="campaigns:list"))
    return builder.as_markup()

def campaign_delay_kb(camp_id: int) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    times = [
        ("1 мин", 1), ("5 мин", 5), ("15 мин", 15),
        ("30 мин", 30), ("1 час", 60), ("3 часа", 180)
    ]
    for i in range(0, len(times), 2):
        t1, v1 = times[i]
        if i + 1 < len(times):
            t2, v2 = times[i+1]
            builder.row(
                _btn(text=t1, callback_data=f"camp:do_delay:{camp_id}:{v1}"),
                _btn(text=t2, callback_data=f"camp:do_delay:{camp_id}:{v2}")
            )
        else:
            builder.row(_btn(text=t1, callback_data=f"camp:do_delay:{camp_id}:{v1}"))
            
    builder.row(_btn(text="Отмена", callback_data=f"campaign:view:{camp_id}"))
    return builder.as_markup()

# --- Accounts ---
def accounts_list_kb(accounts: list[Account]) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for acc in accounts:
        status_icon = "🟢" if acc.is_active and acc.status == "connected" else "🔴"
        label = f"{status_icon} {acc.phone}"
        if acc.name:
            label += f" ({acc.name})"
        builder.row(_btn(text=label, callback_data=f"accounts:view:{acc.id}"))
        
    builder.row(_btn(text="Добавить аккаунт", callback_data="accounts:add"))
    builder.row(_btn(text="📦 Загрузить архивом", callback_data="accounts:mass_add"))
    builder.row(_main_btn())
    return builder.as_markup()

def cancel_to_acc_kb() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(_btn(text="Отмена", callback_data="accounts:list"))
    return builder.as_markup()

def account_view_kb(acc: Account) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(_btn(text="Переподключить", callback_data=f"accounts:reconnect:{acc.id}"))
    if acc.is_active:
        builder.row(_btn(text="Отключить", callback_data=f"accounts:toggle:{acc.id}"))
    else:
        builder.row(_btn(text="Включить", callback_data=f"accounts:toggle:{acc.id}"))
        
    builder.row(_btn(text="Удалить", callback_data=f"accounts:delete:{acc.id}"))
    builder.row(_btn(text="К списку", callback_data="accounts:list"))
    return builder.as_markup()

def confirm_delete_acc_kb(acc_id: int) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(
        _btn(text="Да, удалить", callback_data=f"accounts:delete_confirm:{acc_id}"),
        _btn(text="Нет", callback_data=f"accounts:view:{acc_id}")
    )
    return builder.as_markup()

# --- Tools ---
def tools_menu_kb() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(_btn(text="🛠 Парсер аудитории", callback_data="tools:parser"))
    builder.row(_btn(text="💬 Автоответчик", callback_data="tools:autoresponder"))
    builder.row(_main_btn())
    return builder.as_markup()

def back_to_tools_kb() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(_btn(text="◀️ Назад", callback_data="tools:menu"))
    return builder.as_markup()

def system_status_kb() -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.row(_btn(text="🔄 Обновить", callback_data="admin:system_status"))
    b.row(_back_btn())
    return b.as_markup()

def code_pad_kb(code: str) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    
    # Numbers 1-3
    b.row(
        _btn(text="1", callback_data="code_pad:1", style="secondary"),
        _btn(text="2", callback_data="code_pad:2", style="secondary"),
        _btn(text="3", callback_data="code_pad:3", style="secondary")
    )
    # Numbers 4-6
    b.row(
        _btn(text="4", callback_data="code_pad:4", style="secondary"),
        _btn(text="5", callback_data="code_pad:5", style="secondary"),
        _btn(text="6", callback_data="code_pad:6", style="secondary")
    )
    # Numbers 7-9
    b.row(
        _btn(text="7", callback_data="code_pad:7", style="secondary"),
        _btn(text="8", callback_data="code_pad:8", style="secondary"),
        _btn(text="9", callback_data="code_pad:9", style="secondary")
    )
    # Numbers *, 0, Delete
    b.row(
        _btn(text="*", callback_data="code_pad:ignore", style="secondary"),
        _btn(text="0", callback_data="code_pad:0", style="secondary"),
        _btn(text="◀️ Стереть", callback_data="code_pad:del", style="danger")
    )
    
    if len(code) >= 5:
        b.row(_btn(text="✅ Отправить код", callback_data="code_pad:submit", style="success"))
        
    b.row(_btn(text="Назад", callback_data="menu:cancel"))
    return b.as_markup()


# --- Profile ---
def profile_kb(support_username: str = "") -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    
    # Financial block
    builder.row(
        _btn(text="Подписка", callback_data="sub:plans"),
        _btn(text="💸 Вывод средств", callback_data="profile:withdraw")
    )
    builder.row(_btn(text="🎟 Активировать промокод", callback_data="promo:activate"))
    
    # Legal & Support block
    builder.row(
        _btn(text="⚠ Политика", url=config.PRIVACY_URL),
        _btn(text="📄 Соглашение", url=config.TERMS_URL)
    )
    if support_username:
        builder.row(_btn(text="💬 Служба поддержки", url=f"https://t.me/{support_username.lstrip('@')}"))
        
    builder.row(_main_btn())
    return builder.as_markup()

def cancel_to_profile_kb() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(_btn(text="Отмена", callback_data="profile:show"))
    return builder.as_markup()

# --- Admin ---
def admin_menu_kb() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(_btn(text="Пользователи", callback_data="admin:users:1"))
    builder.row(_btn(text="Настройки бота", callback_data="admin:settings"))
    builder.row(_btn(text="Рассылка", callback_data="admin:broadcast:prepare"))
    builder.row(_btn(text="Создать промокод", callback_data="admin:promo:create"))
    builder.row(_main_btn())
    return builder.as_markup()

# --- Payment ---







def welcome_agree_kb() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(_btn(text="📜 Политика конфиденциальности", url=config.PRIVACY_URL))
    builder.row(_btn(text="📄 Пользовательское соглашение", url=config.TERMS_URL))
    builder.row(_btn(text="✅ Я согласен", callback_data="legal:agree"))
    return builder.as_markup()

def after_legal_kb() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(_btn(text="Назад к соглашению", callback_data="menu:welcome"))
    return builder.as_markup()

def confirm_kb(yes_data: str, no_data: str) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(
        _btn(text="✅ Да", callback_data=yes_data),
        _btn(text="Нет", callback_data=no_data)
    )
    return builder.as_markup()

def tools_parser_kb() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(_btn(text="Парсер аудитории", callback_data="tools:parser"))
    builder.row(_main_btn())
    return builder.as_markup()

# Missing from earlier
def account_actions_kb(account_id: int) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(_btn(text="Переподключить", callback_data=f"accounts:reconnect:{account_id}"))
    builder.row(
        _btn(text="Отключить", callback_data=f"accounts:toggle:{account_id}"),
        _btn(text="Включить", callback_data=f"accounts:toggle:{account_id}")
    )
    builder.row(_btn(text="Удалить", callback_data=f"accounts:delete:{account_id}"))
    builder.row(_btn(text="К списку", callback_data="accounts:list"))
    return builder.as_markup()

def account_delete_confirm_kb(account_id: int) -> InlineKeyboardMarkup:
    return confirm_delete_acc_kb(account_id)

def skip_kb(callback_data: str) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(_btn(text="Пропустить", callback_data=callback_data))
    builder.row(_btn(text="Отмена", callback_data="menu:main"))
    return builder.as_markup()

def database_menu_kb(campaign_id: int, stats: dict = None) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(
        _btn(text="Добавить вручную", callback_data=f"database:upload:{campaign_id}"),
        _btn(text="Спарсить из чата", callback_data=f"tools:parser:camp:{campaign_id}")
    )
    builder.row(_btn(text="Очистить базу", callback_data=f"database:clear_all:{campaign_id}"))
    builder.row(_btn(text="Назад", callback_data=f"campaign:view:{campaign_id}"))
    return builder.as_markup()

def compose_menu_kb(campaign_id: int, has_image: bool = False, has_file: bool = False) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(_btn(text="Изменить текст", callback_data=f"compose:edit_text:{campaign_id}"))
    builder.row(_btn(text="🖼 Изменить фото/видео", callback_data=f"compose:set_file:{campaign_id}"))
    if has_image or has_file:
        builder.row(_btn(text="Удалить медиа", callback_data=f"compose:clear_attach:{campaign_id}"))
    builder.row(_btn(text="Назад", callback_data=f"campaign:view:{campaign_id}"))
    return builder.as_markup()

def delays_menu_kb(campaign_id: int, delay_mode: str) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    modes = [("Фиксированная", "fixed"), ("Случайная", "random")]
    for name, code in modes:
        marker = "✅ " if code == delay_mode else ""
        builder.row(_btn(text=f"{marker}{name}", callback_data=f"delays:edit:{campaign_id}:{code}"))
    builder.row(_btn(text="Изменить время (сек)", callback_data=f"delays:set_fixed:{campaign_id}"))
    builder.row(_btn(text="Назад", callback_data=f"campaign:view:{campaign_id}"))
    return builder.as_markup()

def mailing_status_kb(campaign_id: int, is_running: bool) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    if is_running:
        builder.row(_btn(text="Остановить", callback_data=f"mailing:stop:{campaign_id}"))
    else:
        builder.row(_btn(text="Запустить", callback_data=f"mailing:start:{campaign_id}"))
    builder.row(_btn(text="Назад", callback_data=f"campaign:view:{campaign_id}"))
    return builder.as_markup()

def stats_kb(campaign_id: int) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(_btn(text="Обновить", callback_data=f"stats:show:{campaign_id}"))
    builder.row(_btn(text="Назад", callback_data=f"campaign:view:{campaign_id}"))
    return builder.as_markup()

def logs_kb(campaign_id: int, page: int, total_pages: int) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    row = []
    if page > 1:
        row.append(_btn(text="⬅️", callback_data=f"logs:show:{campaign_id}:{page-1}"))
    if page < total_pages:
        row.append(_btn(text="➡️", callback_data=f"logs:show:{campaign_id}:{page+1}"))
    if row:
        builder.row(*row)
    builder.row(_btn(text="Обновить", callback_data=f"logs:show:{campaign_id}:{page}"))
    builder.row(_btn(text="Назад", callback_data=f"campaign:view:{campaign_id}"))
    return builder.as_markup()

campaign_menu_kb = campaign_view_kb

def campaign_accounts_kb(campaign_id: int, user_accounts: list[Account], assigned_account_ids: list[int]) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for acc in user_accounts:
        marker = "✅" if acc.id in assigned_account_ids else "❌"
        builder.row(_btn(text=f"{marker} {acc.phone} ({acc.name or 'Без имени'})", callback_data=f"campaign:toggle_acc:{campaign_id}:{acc.id}"))
    builder.row(_btn(text="Назад", callback_data=f"campaign:view:{campaign_id}"))
    return builder.as_markup()

def skip_proxy_kb() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(_btn(text="Без прокси", callback_data="accounts:skip_proxy"))
    builder.row(_btn(text="Отмена", callback_data="menu:main"))
    return builder.as_markup()

def plans_kb(pro_price: str, biz_price: str, support_username: str = "") -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    from ..database.models import PLAN_LIMITS
    for plan_id, info in PLAN_LIMITS.items():
        if plan_id == "free":
            continue
        price = pro_price if plan_id == "pro" else biz_price
        builder.row(_btn(text=f"{info['emoji']} {info['label']} — {price}/мес", callback_data=f"sub:select:{plan_id}"))
    builder.row(_btn(text="Назад", callback_data="menu:main"))
    return builder.as_markup()

def payment_provider_kb(plan_id: str, support_username: str = "") -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(_btn(text="Картой (RUB/USD)", callback_data=f"pay:platega:{plan_id}"))
    builder.row(_btn(text="CryptoBot", callback_data=f"pay:cryptobot:{plan_id}"))
    builder.row(_btn(text="TON (прямой)", callback_data=f"pay:ton:{plan_id}"))
    builder.row(_btn(text="Отмена", callback_data="sub:plans"))
    return builder.as_markup()

def payment_waiting_kb(url: str, payment_id: int, provider: str) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(_btn(text="Оплатить", url=url))
    builder.row(_btn(text="Проверить оплату", callback_data=f"pay:check:{payment_id}"))
    builder.row(_btn(text="Отмена", callback_data="sub:plans"))
    return builder.as_markup()

def ton_payment_kb(deeplink: str, tonkeeper_url: str, payment_id: int) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(_btn(text="Оплатить в TON (URI)", url=deeplink))
    builder.row(_btn(text="📲 Tonkeeper", url=tonkeeper_url))
    builder.row(_btn(text="Проверить оплату", callback_data=f"pay:check:{payment_id}"))
    builder.row(_btn(text="Отмена", callback_data="sub:plans"))
    return builder.as_markup()

def payment_success_kb() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(_btn(text="Назад", callback_data="menu:main"))
    return builder.as_markup()

def admin_back_kb() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(_btn(text="Назад", callback_data="admin:panel"))
    return builder.as_markup()

def admin_panel_kb() -> InlineKeyboardMarkup:
    return admin_menu_kb()

def admin_broadcast_kb() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(_btn(text="🚀 Начать рассылку", callback_data="admin:broadcast:start"))
    builder.row(_btn(text="Назад", callback_data="admin:panel"))
    return builder.as_markup()

def admin_promos_kb(promos: list) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for promo in promos:
        status = "✅" if promo.is_active else "❌"
        builder.row(_btn(text=f"{status} {promo.code}", callback_data=f"admin:promo:view:{promo.id}"))
    builder.row(_btn(text="Создать промокод", callback_data="admin:promo:create"))
    builder.row(_btn(text="Назад", callback_data="admin:panel"))
    return builder.as_markup()

def admin_promo_actions_kb(promo_id: int, is_active: bool) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    if is_active:
        builder.row(_btn(text="Отключить", callback_data=f"admin:promo:toggle:{promo_id}"))
    else:
        builder.row(_btn(text="Включить", callback_data=f"admin:promo:toggle:{promo_id}"))
    builder.row(_btn(text="Удалить", callback_data=f"admin:promo:delete:{promo_id}"))
    builder.row(_btn(text="Назад", callback_data="admin:promo:list"))
    return builder.as_markup()

def admin_settings_kb() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(_btn(text="Цена PRO", callback_data="admin:set:pro_price"))
    builder.row(_btn(text="Цена BIZ", callback_data="admin:set:business_price"))
    builder.row(_btn(text="Ссылка поддержки", callback_data="admin:set:support_username"))
    builder.row(_btn(text="Назад", callback_data="admin:panel"))
    return builder.as_markup()

def admin_users_kb(users: list, page: int, total: int, per_page: int) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for user in users:
        builder.row(_btn(text=f"{user.tg_id} - ${user.balance:.2f}", callback_data=f"admin:user:view:{user.id}"))
    
    # Pagination
    import math
    total_pages = math.ceil(total / per_page)
    row = []
    if page > 1:
        row.append(_btn(text="⬅️", callback_data=f"admin:users:{page-1}"))
    if page < total_pages:
        row.append(_btn(text="➡️", callback_data=f"admin:users:{page+1}"))
    if row:
        builder.row(*row)
        
    builder.row(_btn(text="Назад", callback_data="admin:panel"))
    return builder.as_markup()

def admin_user_actions_kb(user_id: int, is_banned: bool) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    if is_banned:
        builder.row(_btn(text="🟢 Разблокировать", callback_data=f"admin:user:toggle_ban:{user_id}"))
    else:
        builder.row(_btn(text="⛔ Заблокировать", callback_data=f"admin:user:toggle_ban:{user_id}"))
    builder.row(
        _btn(text="⭐ Выдать PRO", callback_data=f"admin:grant:pro:30:{user_id}"),
        _btn(text="💎 Выдать BIZ", callback_data=f"admin:grant:business:30:{user_id}")
    )
    builder.row(_btn(text="🔙 Назад", callback_data="admin:users:1"))
    return builder.as_markup()

def api_id_kb(back_data: str = "menu:cancel") -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(_btn(text="Использовать стандартный (0)", callback_data="add_acc:use_default_api"))
    builder.row(_btn(text="Назад", callback_data=back_data))
    return builder.as_markup()
