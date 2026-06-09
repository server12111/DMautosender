import logging
import sys
import io
from pathlib import Path
from datetime import datetime


def setup_logging(logs_path: Path) -> logging.Logger:
    logs_path.mkdir(parents=True, exist_ok=True)
    log_file = logs_path / f"{datetime.now().strftime('%Y%m%d')}.log"

    fmt = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
    datefmt = "%Y-%m-%d %H:%M:%S"
    formatter = logging.Formatter(fmt, datefmt=datefmt)

    file_handler = logging.FileHandler(log_file, encoding="utf-8")
    file_handler.setFormatter(formatter)

    # UTF-8 вывод в консоль на Windows
    utf8_stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    stream_handler = logging.StreamHandler(utf8_stdout)
    stream_handler.setFormatter(formatter)

    root = logging.getLogger()
    root.setLevel(logging.INFO)
    root.handlers.clear()
    root.addHandler(file_handler)
    root.addHandler(stream_handler)

    logging.getLogger("telethon").setLevel(logging.WARNING)
    logging.getLogger("aiogram").setLevel(logging.WARNING)

    return logging.getLogger("dmsender")
