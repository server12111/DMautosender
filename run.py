import asyncio
import sys
import time
import traceback

if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

from bot.main import main

RESTART_DELAY = 5

if __name__ == "__main__":
    while True:
        try:
            asyncio.run(main())
            break  # чистый выход (polling завершён нормально)
        except (KeyboardInterrupt, SystemExit):
            print("Остановлено пользователем.")
            break
        except Exception:
            print(f"\n=== КРАШ ===\n{traceback.format_exc()}")
            print(f"Перезапуск через {RESTART_DELAY} сек...")
            time.sleep(RESTART_DELAY)
