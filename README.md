# Python KOReader Sync Server (`python-koreader-sync-server`)

A lightweight, single-user / multi-device implementation of the KOReader sync (`kosync`) protocol in Python 3.13+.

It allows KOReader instances on e-ink readers (Kindle, Kobo, PocketBook, Android, etc.) to sync reading positions, progress percentages, and timestamps across devices.

## Features

1. **KOReader Sync API Protocol**:
   - Standard REST authentication via `x-auth-user` and `x-auth-key`.
   - User creation (`POST /users/create`) and authentication (`GET /users/auth`).
   - Progress fetch (`GET /syncs/progress/{document}`) and update (`PUT /syncs/progress`).
2. **Reverse Ebook Resolution**:
   - Recursively scans an ebook directory tree (`ebook_repo`, e.g., `~/MetaLibrary`) to map KOReader content partial MD5 hashes and filename MD5 hashes back to actual ebook relative file paths.
3. **Directory Watchdog**:
   - Uses `watchdog` to monitor real-time filesystem changes (file creation, modification, deletion, move) inside `ebook_repo` and dynamically updates the in-memory hash index without needing full rescans.
4. **Biograph Telemetry Integration**:
   - Automatically forwards reading progress updates as structured `koreader-progress` events to a Biograph telemetry backend (e.g. `http://nalanda:8000/`).
5. **Single-User Configuration**:
   - Configurable credentials in `config.toml` that auto-initialize on startup.

---

## References & Acknowledgements

- **Primary Reference**: [koreader/koreader-sync-server](https://github.com/koreader/koreader-sync-server) - The original KOReader sync server project.
- **Secondary Local Reference**: `~/gith/domschl/Indrajala/indraserver/src/indra_kosync.py` - Local partial KOReader sync implementation.
- **Biograph Project**: `~/gith/domschl/Biograph` - Life-event telemetry tracking server.

---

## Installation & Quickstart

### Prerequisites
- Python >= 3.13
- [`uv`](https://github.com/astral-sh/uv) package manager

### Installation

```bash
# Clone the repository
git clone https://github.com/koreader/python-koreader-sync-server.git
cd python-koreader-sync-server

# Install dependencies and sync virtualenv using uv
uv sync
```

### Running the Server

1. Create or copy a configuration file:
   ```bash
   cp config.example.toml config.toml
   ```
2. Edit `config.toml` to set your desired username, password, library path (`ebook_repo`), and Biograph settings.
3. Start the server:
   ```bash
   uv run koreader-sync-server --config config.toml
   ```

---

## Configuration (`config.toml`)

```toml
[server]
bind_address = "0.0.0.0"
port = 8080
tls = false
cert_file = ""
key_file = ""

[user]
username = "reader"
password = "secretpassword"

[storage]
state_file = "~/.config/koreader-sync-server/state.json"

[library]
ebook_repo = "~/MetaLibrary"
scan_interval_seconds = 1800
watchdog_enabled = true

[biograph]
enabled = true
url = "http://nalanda:8000/"
api_token = ""
```

---

## Development & Testing

Run unit and integration tests using `pytest`:

```bash
uv run pytest
```

---

## Systemd Service

Copy `systemd/koreader-sync-server.service` to `/etc/systemd/system/`:

```bash
sudo cp systemd/koreader-sync-server.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now koreader-sync-server
```

---

## Packaging

### Arch Linux (PKGBUILD)

Building an Arch Linux package:
```bash
cd packaging/arch
makepkg -si
```

### Debian Package (.deb)

Building a `.deb` package:
```bash
dpkg-buildpackage -us -uc -b
```

---

## License

This project is licensed under the **GNU General Public License v3.0 (GPL-3.0-or-later)** to honor the licensing of the original KOReader project. See [LICENSE](file:///home/dsc/gith/domschl/python-koreader-sync-server/LICENSE) for full details.
