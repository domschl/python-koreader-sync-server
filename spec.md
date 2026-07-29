# Specification: Python KOReader Sync Server (`python-koreader-sync-server`)

## 1. Overview & Goals

`python-koreader-sync-server` is a lightweight, single-user / multi-device implementation of the KOReader sync (`kosync`) protocol written in Python 3.13+. It allows KOReader instances on e-ink devices (Kindle, Kobo, PocketBook, Android, etc.) to synchronize reading positions, progress percentages, and timestamps across devices.

Key extensions:
1. **Reverse Ebook Resolution**: Scans a local library directory (e.g. `~/MetaLibrary`) to map KOReader content MD5 hashes and filename MD5 hashes back to actual ebook relative paths and metadata.
2. **Biograph Integration**: Forwards reading progress updates to a Biograph telemetry backend (`http://nalanda:8000/`) as structured life events.
3. **Minimal External Dependencies**: Built with standard Python library / minimal dependencies managed via `uv`.

---

## 2. References

- **Primary Reference Project**: [koreader/koreader-sync-server](https://github.com/koreader/koreader-sync-server) (Authoritative source for the KOReader Sync API protocol)
- **Secondary / Partial Reference**: `~/gith/domschl/Indrajala/indraserver/src/indra_kosync.py` (Local partial implementation for background context)
- **Biograph Backend**: `~/gith/domschl/Biograph`
- **Test Ebook Library Directory**: `~/MetaLibrary`


---

## 3. Protocol Specification

KOReader clients communicate over HTTP/HTTPS REST endpoints using standard custom headers for authentication:

### 3.1 Headers & Authentication
- `x-auth-user`: Username string
- `x-auth-key`: Password string

### 3.2 Endpoints

| Method | Endpoint | Auth Required | Description | Request Payload | Response |
|--------|----------|---------------|-------------|-----------------|----------|
| `GET` | `/` | No | Root endpoint | None | `200 OK` (`text/plain`: "KOSync server") |
| `GET` | `/healthcheck` | No | Health check | None | `200 OK` (`{"state": "OK"}`) |
| `POST` | `/users/create` | No | User registration | `{"username": "...", "password": "..."}` | `201 Created` (`{"username": "..."}`) |
| `GET` | `/users/auth` | Yes | Credentials check | None | `200 OK` (`{"authorized": "OK"}`) or `401 Unauthorized` |
| `GET` | `/syncs/progress/{document}` | Yes | Get progress for document hash | None | `200 OK` JSON progress payload |
| `PUT` | `/syncs/progress` | Yes | Update progress for document | JSON progress payload | `200 OK` (`{"document": "...", "timestamp": <unix_ts>}`) |

#### Progress Schema (`GET /syncs/progress/{document}` and `PUT /syncs/progress`)
```json
{
  "document": "e8d6411d38202d51bf519969d273a216",
  "progress": "/body/page[12]",
  "percentage": 0.42,
  "device": "Kindle Oasis",
  "device_id": "84a7ef91b2c3",
  "timestamp": 1722245000
}
```
*Note*: If progress for a document does not exist yet for an authenticated user, the server creates an initial entry (e.g. `progress: "0"`, `percentage: 0.0`) and returns `200 OK`.

---

## 4. Hash Algorithms & Reverse Resolution

KOReader identifies books using two hash methods:

### 4.1 Filename Hash
- Standard MD5 hex digest of the ebook filename:
  - **Basename Hash** (KOReader client standard): `hashlib.md5(file_path.name.encode('utf-8')).hexdigest()`
  - **Relative Path Hash**: `hashlib.md5(rel_path.encode('utf-8')).hexdigest()`

### 4.2 Partial Content MD5 Hash (`partial_koreader_md5`)
- Sample size: 1024 bytes.
- Loop index $i \in [-1, 10]$ (12 iterations total):
  - Exponent = $2 \times i$
  - If exponent $< 0 \implies \text{offset} = 0$
  - Else $\implies \text{offset} = 1024 \ll (2i)$
- At each offset, seek and read up to 1024 bytes into the MD5 hash context. Stop if EOF is reached.
- Returns 32-character MD5 hex digest.

### 4.3 Reverse Resolution Service & File Watchdog
- **Directory Scanner**: Recursively scans `ebook_repo` (e.g., `~/MetaLibrary`) for ebook files (`.epub`, `.pdf`, `.mobi`, `.azw3`, `.cbz`, `.fb2`, `.djvu`, `.txt`).
- **Lookup Table**: Computes both filename MD5 and partial content MD5 for each file and maintains an in-memory mapping:
  `hash -> {"filename": rel_path, "abs_path": abs_path}`.
- **Directory Watchdog**: Uses the `watchdog` library (`watchdog.observers.Observer`) to monitor filesystem events (file creation, modification, deletion, move) inside `ebook_repo`. Incrementally updates or removes hash entries in real time as files change in the directory tree.
- **Progress Annotation**: When `PUT /syncs/progress` is called, the server looks up the document hash to obtain the actual file name/path for logging and Biograph telemetry events.

---

## 5. Biograph Telemetry Integration

When `biograph.enabled = true` and `biograph.url` is set (e.g., `http://nalanda:8000/`):
- Upon receiving a progress update via `PUT /syncs/progress`, the server posts an event to `{biograph_url}/events/`:
  ```json
  {
    "timestamp": "2026-07-29T07:31:31Z",
    "ev_type": "koreader-progress",
    "source_host": "<hostname>",
    "source_app": "koreader-sync-server",
    "content": {
      "user": "username",
      "document_hash": "e8d6411d38202d51bf519969d273a216",
      "filename": "Literature/Fiction/Book.epub",
      "progress": "/body/page[12]",
      "percentage": 0.42,
      "device": "Kindle Oasis",
      "device_id": "84a7ef91b2c3",
      "timestamp": 1722245000
    },
    "summary": "Reading progress on 'Literature/Fiction/Book.epub': 42.0%"
  }
  ```
- Biograph backend helper function `is_ereader_event()` in `~/gith/domschl/Biograph/backend/biograph/api/events.py` may be extended if necessary to include `"koreader-"` or `"kosync-"`.

---

## 6. Configuration & Persistence

### 6.1 TOML Configuration (`config.toml`)
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

### 6.2 Data Persistence & User Auth Initialization
- Single-user credentials specified in `config.toml` (`[user]`) are auto-initialized on server startup if not already present in `state.json`.
- `POST /users/create` will succeed for the configured single-user credentials or initialize user accounts.
- `GET /users/auth` validates against the configured credentials.
- Reading positions and user state are saved atomically to `state.json` (or SQLite database).

---

## 7. Project Code Standards

- **Python Version**: `>=3.13`
- **Package Management**: `uv`
- **Third-Party Dependencies**:
  - `watchdog`: Used for real-time filesystem monitoring of `ebook_repo`.
  - Keep all other dependencies minimal.
- **Type Hints**: Strict modern Python type annotations (`str | None`, `list[str]`, `dict[str, Any]`).

---

## 8. Packaging & Licensing

- **Service**: Linux systemd unit file (`koreader-sync-server.service`).
- **Packaging**: Support Arch Linux (`PKGBUILD`) and Debian (`.deb`).
- **License**: GNU General Public License v3.0 (GPL-3.0) to match KOReader reference project.


