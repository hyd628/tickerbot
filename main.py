import logging

from tickerbot.bot import build_application


def main() -> None:
    logging.basicConfig(
        format="%(asctime)s %(levelname)s %(name)s: %(message)s", level=logging.INFO
    )
    application = build_application()
    application.run_polling()


if __name__ == "__main__":
    main()
