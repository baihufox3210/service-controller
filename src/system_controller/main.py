import logging
import sys

from system_controller.core.bot import Bot
from system_controller.core.config import ConfigError, load_settings


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    try:
        settings = load_settings()

    except ConfigError as e:
        print(f"設定錯誤：{e}", file=sys.stderr)
        sys.exit(1)

    if not (settings.admin_user_ids or settings.admin_role_ids):
        logging.warning("未設定任何管理員（ADMIN_USER_IDS / ADMIN_ROLE_IDS），將無人能新增或移除服務。")

    Bot(settings).run(settings.token, log_handler=None)


if __name__ == "__main__":
    main()
