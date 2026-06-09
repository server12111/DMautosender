import asyncio
import sys
import traceback

if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

from bot.main import main

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("Остановлено пользователем.")
    except Exception as e:
        print(f"\n=== КРИТИЧЕСКАЯ ОШИБКА ===\n{traceback.format_exc()}\n")
        sys.exit(1)
