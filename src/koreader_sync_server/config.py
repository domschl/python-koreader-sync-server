from __future__ import annotations

import os
import sys
import tomllib
from dataclasses import dataclass
from pathlib import Path


@dataclass
class ServerConfig:
    bind_address: str = "0.0.0.0"
    port: int = 8080
    tls: bool = False
    cert_file: str | None = None
    key_file: str | None = None


@dataclass
class UserConfig:
    username: str = "reader"
    password: str = "secretpassword"


@dataclass
class StorageConfig:
    state_file: Path = Path("~/.config/koreader-sync-server/state.json").expanduser()


@dataclass
class LibraryConfig:
    ebook_repo: Path | None = None
    scan_interval_seconds: int = 1800
    watchdog_enabled: bool = True


@dataclass
class BiographConfig:
    enabled: bool = False
    url: str = "http://nalanda:8000/"
    api_token: str | None = None


@dataclass
class Config:
    server: ServerConfig
    user: UserConfig
    storage: StorageConfig
    library: LibraryConfig
    biograph: BiographConfig

    @classmethod
    def load(cls, config_path: str | Path | None = None) -> Config:
        """Load configuration from TOML file or fallback to defaults."""
        data: dict[str, dict[str, str | int | bool]] = {}

        if config_path is None:
            possible_paths = [
                Path("config.toml"),
                Path("~/.config/koreader-sync-server/config.toml").expanduser(),
                Path("/etc/koreader-sync-server/config.toml"),
            ]
            for p in possible_paths:
                if p.exists():
                    config_path = p
                    break

        if config_path is not None:
            path = Path(config_path).expanduser()
            if path.exists():
                with open(path, "rb") as f:
                    data = tomllib.load(f)

        server_raw = data.get("server", {})
        server_cfg = ServerConfig(
            bind_address=str(server_raw.get("bind_address", "0.0.0.0")),
            port=int(server_raw.get("port", 8080)),
            tls=bool(server_raw.get("tls", False)),
            cert_file=str(server_raw["cert_file"]) if server_raw.get("cert_file") else None,
            key_file=str(server_raw["key_file"]) if server_raw.get("key_file") else None,
        )

        user_raw = data.get("user", {})
        user_cfg = UserConfig(
            username=str(user_raw.get("username", "reader")),
            password=str(user_raw.get("password", "secretpassword")),
        )

        storage_raw = data.get("storage", {})
        state_file_str = str(storage_raw.get("state_file", "~/.config/koreader-sync-server/state.json"))
        storage_cfg = StorageConfig(
            state_file=Path(state_file_str).expanduser()
        )

        library_raw = data.get("library", {})
        repo_str = library_raw.get("ebook_repo")
        library_cfg = LibraryConfig(
            ebook_repo=Path(str(repo_str)).expanduser() if repo_str else None,
            scan_interval_seconds=int(library_raw.get("scan_interval_seconds", 1800)),
            watchdog_enabled=bool(library_raw.get("watchdog_enabled", True)),
        )

        biograph_raw = data.get("biograph", {})
        biograph_cfg = BiographConfig(
            enabled=bool(biograph_raw.get("enabled", False)),
            url=str(biograph_raw.get("url", "http://nalanda:8000/")),
            api_token=str(biograph_raw["api_token"]) if biograph_raw.get("api_token") else None,
        )

        return cls(
            server=server_cfg,
            user=user_cfg,
            storage=storage_cfg,
            library=library_cfg,
            biograph=biograph_cfg,
        )
