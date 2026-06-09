import re


_TG_LINK_RE = re.compile(
    r"(?:https?://)?(?:t(?:elegram)?\.me/|@)([A-Za-z0-9_]{3,32})", re.IGNORECASE
)
_NUMERIC_RE = re.compile(r"^-?\d{5,15}$")


def parse_txt(content: bytes) -> list[str]:
    """Парсит TXT-файл с базой пользователей. Принимает @username, числовые ID и t.me ссылки."""
    try:
        text = content.decode("utf-8")
    except UnicodeDecodeError:
        text = content.decode("cp1251", errors="replace")

    identifiers: list[str] = []
    seen: set[str] = set()

    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue

        # Попытка найти t.me ссылку или @username
        match = _TG_LINK_RE.search(line)
        if match:
            ident = "@" + match.group(1).lower()
        elif _NUMERIC_RE.match(line):
            ident = line
        elif line.startswith("@"):
            # Уже @username — проверяем формат
            username = line[1:]
            if re.match(r"^[A-Za-z0-9_]{3,32}$", username):
                ident = "@" + username.lower()
            else:
                continue
        else:
            continue

        if ident not in seen:
            seen.add(ident)
            identifiers.append(ident)

    return identifiers
