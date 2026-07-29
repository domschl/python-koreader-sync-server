from __future__ import annotations

import argparse
import asyncio
import logging
import sys

from koreader_sync_server.biograph import BiographClient
from koreader_sync_server.config import Config
from koreader_sync_server.library import LibraryResolver
from koreader_sync_server.server import KOSyncServer
from koreader_sync_server.storage import StorageManager


def setup_logging(verbose: bool) -> None:
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        handlers=[logging.StreamHandler(sys.stdout)],
    )


async def main_async(config_path: str | None, verbose: bool) -> None:
    setup_logging(verbose)
    logger = logging.getLogger("koreader_sync_server")
    logger.info("Starting Python KOReader Sync Server...")

    config = Config.load(config_path)

    # 1. Initialize Storage
    storage = StorageManager(config.storage.state_file)
    storage.ensure_user(config.user.username, config.user.password)

    # 2. Initialize Ebook Library Resolver & Watchdog
    library = LibraryResolver(
        repo_dir=config.library.ebook_repo,
        watchdog_enabled=config.library.watchdog_enabled,
    )
    library.start()

    # 3. Initialize Biograph Client
    biograph = BiographClient(config.biograph)
    await biograph.start()

    # 4. Create and start HTTP Server
    server = KOSyncServer(config, storage, library, biograph)

    try:
        await server.run()
    except asyncio.CancelledError:
        logger.info("Server task cancelled, shutting down...")
    finally:
        await biograph.stop()
        library.stop()
        logger.info("KOReader Sync Server shutdown complete.")


def main() -> None:
    parser = argparse.ArgumentParser(description="Python KOReader Sync Server")
    parser.add_argument("-c", "--config", help="Path to config.toml file", default=None)
    parser.add_argument("-v", "--verbose", action="store_true", help="Enable verbose debug logging")
    args = parser.parse_args()

    try:
        asyncio.run(main_async(args.config, args.verbose))
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
