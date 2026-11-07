"""``python -m studio_server``: run the server on ``STUDIO_HOST``:``STUDIO_PORT``."""

import uvicorn

from studio_server.config import ServerSettings


def main() -> None:
    settings = ServerSettings.from_env()
    uvicorn.run(
        "studio_server.main:create_app",
        factory=True,
        host=settings.host,
        port=settings.port,
        log_config=None,
    )


if __name__ == "__main__":
    main()
