import io
import sqlite3
import sys
from pathlib import Path

from googleapiclient.http import MediaFileUpload, MediaIoBaseDownload

from util.google.creds import get_creds
from util.logging.logger import Logging


class DriveSync:
    DB_FILENAME = "drive_tokens.db"
    DEFAULT_TOKEN_LABEL = "google_drive_default"
    DRIVE_FOLDER = "application/vnd.google-apps.folder"
    DRIVE_MIME_PREFIX = "application/vnd.google-apps."

    def __init__(
        self,
        credential_location,
        output_root,
        token_label = DEFAULT_TOKEN_LABEL,
        db_filename = DB_FILENAME,
        chunk_size = 1024 * 1024,
    ):
        self.credential_location = Path(credential_location) if credential_location else None
        self.output_root = Path(output_root)
        self.token_label = token_label
        self.chunk_size = chunk_size

        self.logger = Logging.get("drive-sync")
        self.service = get_creds(str(self.credential_location) if self.credential_location else None)

        # DB location: store next to creds directory if provided, else CWD drive_tokens.db
        self.db_path = (self.credential_location / db_filename) if self.credential_location else Path(db_filename)

        # In-memory caches to avoid repeated API calls
        self._parent_name_cache = {}
        self._folder_id_by_name_cache = {}

        # Local output mapping from Drive folder name -> local subdir
        self.output_path = {
            "Extracted Questions": self.output_root / "questions",
            "Transcripts": self.output_root / "transcripts",
            "Extracted Audio": self.output_root / "audio",
            "Interviews": self.output_root / "interviews",
        }

    # ----------------------------
    # SQLite / DB
    # ----------------------------
    def _get_connection(self):
        conn = sqlite3.connect(self.db_path)
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS sync_tokens (
                id INTEGER PRIMARY KEY,
                label TEXT NOT NULL,
                token TEXT NOT NULL,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_sync_tokens_label ON sync_tokens (label)")

        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS drive_file_map (
                local_path TEXT PRIMARY KEY,
                drive_file_id TEXT NOT NULL,
                drive_name TEXT,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_drive_file_id ON drive_file_map (drive_file_id)")

        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS drive_folders (
                folder_id TEXT PRIMARY KEY,
                folder_name TEXT NOT NULL,
                parent_id TEXT,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_drive_folders_name ON drive_folders(folder_name)")

        conn.commit()
        return conn

    def load_saved_token(self):
        conn = self._get_connection()
        try:
            cur = conn.execute(
                "SELECT token FROM sync_tokens WHERE label = ? ORDER BY id DESC LIMIT 1",
                (self.token_label,),
            )
            row = cur.fetchone()
            return row[0] if row else None
        finally:
            conn.close()

    def save_token(self, token):
        conn = self._get_connection()
        try:
            conn.execute(
                "INSERT INTO sync_tokens (label, token) VALUES (?, ?)",
                (self.token_label, token),
            )
            conn.commit()
        finally:
            conn.close()

    def prune_token_history(self, keep_last = 50):
        conn = self._get_connection()
        try:
            conn.execute(
                """
                DELETE FROM sync_tokens
                WHERE label = ?
                  AND id NOT IN (
                    SELECT id FROM sync_tokens
                    WHERE label = ?
                    ORDER BY id DESC
                    LIMIT ?
                  )
                """,
                (self.token_label, self.token_label, keep_last),
            )
            conn.commit()
        finally:
            conn.close()

    def save_file_id(self, drive_file_id, local_path, drive_name = None):
        local_path = str(Path(local_path).resolve())
        conn = self._get_connection()
        try:
            conn.execute(
                """
                INSERT INTO drive_file_map (local_path, drive_file_id, drive_name)
                VALUES (?, ?, ?)
                ON CONFLICT(local_path) DO UPDATE SET
                    drive_file_id = excluded.drive_file_id,
                    drive_name = excluded.drive_name,
                    updated_at = CURRENT_TIMESTAMP
                """,
                (local_path, drive_file_id, drive_name),
            )
            conn.commit()
        finally:
            conn.close()

    def get_file_id(self, local_path):
        local_path = str(Path(local_path).resolve())
        conn = self._get_connection()
        try:
            cur = conn.execute(
                "SELECT drive_file_id FROM drive_file_map WHERE local_path = ? LIMIT 1",
                (local_path,),
            )
            row = cur.fetchone()
            return row[0] if row else None
        finally:
            conn.close()

    def save_drive_folder(self, folder_id, folder_name, parent_id = None):
        conn = self._get_connection()
        try:
            conn.execute(
                """
                INSERT INTO drive_folders (folder_id, folder_name, parent_id)
                VALUES (?, ?, ?)
                ON CONFLICT(folder_id) DO UPDATE SET
                    folder_name = excluded.folder_name,
                    parent_id = excluded.parent_id,
                    updated_at = CURRENT_TIMESTAMP
                """,
                (folder_id, folder_name, parent_id),
            )
            conn.commit()
        finally:
            conn.close()

        # Warm cache (folder names can collide; this cache is “best effort”)
        self._folder_id_by_name_cache.setdefault(folder_name, folder_id)

    def get_folder_id_by_name(self, folder_name):
        if folder_name in self._folder_id_by_name_cache:
            return self._folder_id_by_name_cache[folder_name]

        conn = self._get_connection()
        try:
            cur = conn.execute(
                """
                SELECT folder_id
                FROM drive_folders
                WHERE folder_name = ?
                LIMIT 1
                """,
                (folder_name,),
            )
            row = cur.fetchone()
            folder_id = row[0] if row else None
            if folder_id:
                self._folder_id_by_name_cache[folder_name] = folder_id
            return folder_id
        finally:
            conn.close()

    # ----------------------------
    # Drive API calls
    # ----------------------------
    def get_start_page_token(self):
        resp = self.service.changes().getStartPageToken().execute()
        self.logger.debug("getStartPageToken response: %s", resp)
        return resp["startPageToken"]

    def get_parent_name(self, parent_id):
        if not parent_id:
            return ""

        if parent_id in self._parent_name_cache:
            return self._parent_name_cache[parent_id]

        meta = self.service.files().get(
            fileId=parent_id,
            fields="id,name",
            supportsAllDrives=True,
        ).execute()
        name = meta.get("name", "")
        self._parent_name_cache[parent_id] = name
        return name

    def list_all_files_snapshot(self, page_size = 1000):
        all_files = []
        page_token = None

        while True:
            resp = self.service.files().list(
                pageSize=page_size,
                pageToken=page_token,
                fields="nextPageToken, files(id,name,mimeType,modifiedTime,trashed,parents)",
                spaces="drive",
                supportsAllDrives=True,
                includeItemsFromAllDrives=True,
            ).execute()

            files = resp.get("files", [])
            all_files.extend(files)

            page_token = resp.get("nextPageToken")
            self.logger.debug(
                "Snapshot fetched %d files; total=%d; nextPageToken=%s",
                len(files),
                len(all_files),
                "yes" if page_token else "no",
            )
            if not page_token:
                break

        return all_files

    def list_all_changes_since(self, start_page_token, page_size = 100):
        all_changes = []
        page_token = start_page_token
        new_start_page_token = None

        while True:
            resp = self.service.changes().list(
                pageToken=page_token,
                pageSize=page_size,
                fields=(
                    "nextPageToken,newStartPageToken,"
                    "changes(fileId,removed,time,file(id,name,mimeType,modifiedTime,trashed,parents))"
                ),
                includeRemoved=True,
                spaces="drive",
                supportsAllDrives=True,
                includeItemsFromAllDrives=True,
            ).execute()

            changes = resp.get("changes", [])
            all_changes.extend(changes)

            next_page_token = resp.get("nextPageToken")
            new_start_page_token = resp.get("newStartPageToken", new_start_page_token)

            self.logger.debug(
                "Fetched %d changes; total=%d; nextPageToken=%s; newStartPageToken=%s",
                len(changes),
                len(all_changes),
                "yes" if next_page_token else "no",
                "yes" if new_start_page_token else "no",
            )

            if next_page_token:
                page_token = next_page_token
                continue
            break

        return all_changes, new_start_page_token

    # ----------------------------
    # Download / Upload
    # ----------------------------
    def download_file(self, file_id, out_path, drive_parent_name = ""):
        out_path = Path(out_path)

        if out_path.exists():
            self.logger.info("Output file (%s) already exists, skipping.", out_path)
            return out_path

        out_path.parent.mkdir(parents=True, exist_ok=True)

        request = self.service.files().get_media(fileId=file_id)

        self.logger.info("Downloading %s -> %s...", file_id, out_path, console=True)
        sys.stdout.write("Downloading...   0%")
        sys.stdout.flush()
        last_prog = -1

        with io.FileIO(out_path, "wb") as fh:
            downloader = MediaIoBaseDownload(fh, request, chunksize=self.chunk_size)
            done = False
            while not done:
                status, done = downloader.next_chunk()
                if status:
                    prog = int(status.progress() * 100)
                    if prog > (last_prog + 10):
                        sys.stdout.write(f"\rDownloading... {prog:3d}%")
                        sys.stdout.flush()
                        last_prog = prog

        sys.stdout.write("\rDownloading... 100%\n")
        sys.stdout.flush()

        self.logger.info("Downloaded %s", out_path.name, console=True)

        # Save mapping after successful download
        self.save_file_id(file_id, out_path, drive_name=drive_parent_name or out_path.name)
        return out_path

    def create_drive_file(self, local_path, parent_folder_name, mime_type = None):
        local_path = Path(local_path)
        if not local_path.exists() or not local_path.is_file():
            raise FileNotFoundError(f"Local file not found: {local_path}")
        
        drive_file_id = self.get_file_id(local_path)
        if drive_file_id:
            return

        parent_folder_id = self.get_folder_id_by_name(parent_folder_name)
        if not parent_folder_id:
            raise ValueError(f"Could not resolve folder '{parent_folder_name}' to a folder_id. Run bootstrap to index folders.")

        media = MediaFileUpload(str(local_path), mimetype=mime_type, resumable=True)

        request = self.service.files().create(
            body={"name": local_path.name, "parents": [parent_folder_id]},
            media_body=media,
            fields="id,name,mimeType,parents,modifiedTime",
            supportsAllDrives=True,
        )

        self.logger.info("Creating Drive file %s in folder %s", local_path.name, parent_folder_id, console=True)
        sys.stdout.write("Uploading...   0%")
        sys.stdout.flush()
        last_prog = -1

        response = None
        while response is None:
            status, response = request.next_chunk()
            if status:
                prog = int(status.progress() * 100)
                if prog >= last_prog + 5:
                    sys.stdout.write(f"\rUploading... {prog:3d}%")
                    sys.stdout.flush()
                    last_prog = prog

        sys.stdout.write("\rUploading... 100%\n")
        sys.stdout.flush()

        # Persist local_path -> drive_file_id mapping
        self.save_file_id(response["id"], local_path, drive_name=response.get("name"))
        return response

    def update_file_content(self, file_id, local_path, mime_type = None):
        local_path = Path(local_path)
        if not local_path.exists() or not local_path.is_file():
            raise FileNotFoundError(f"Local file not found: {local_path}")

        media = MediaFileUpload(str(local_path), mimetype=mime_type, resumable=True)

        request = self.service.files().update(
            fileId=file_id,
            media_body=media,
            fields="id,name,mimeType,modifiedTime",
            supportsAllDrives=True,
        )

        self.logger.info("Updating Drive file %s from %s", file_id, local_path, console=True)
        sys.stdout.write("Uploading...   0%")
        sys.stdout.flush()
        last_prog = -1

        response = None
        while response is None:
            status, response = request.next_chunk()
            if status:
                prog = int(status.progress() * 100)
                if prog >= last_prog + 5:
                    sys.stdout.write(f"\rUploading... {prog:3d}%")
                    sys.stdout.flush()
                    last_prog = prog

        sys.stdout.write("\rUploading... 100%\n")
        sys.stdout.flush()
        return response

    def create_or_update_by_local_path(
        self,
        local_path,
        folder_name,
        mime_type = None,
    ):
        local_path = Path(local_path)
        drive_file_id = self.get_file_id(local_path)


        if drive_file_id:
            return self.update_file_content(drive_file_id, local_path, mime_type=mime_type)
        return self.create_drive_file(local_path, folder_name, mime_type=mime_type)

    # ----------------------------
    # Orchestrator
    # ----------------------------
    def sync(self):
        token = self.load_saved_token()

        # BOOTSTRAP: no token => full snapshot + set token
        if not token:
            self.logger.info("No saved startPageToken found. Running full snapshot (files().list)...")
            files = self.list_all_files_snapshot()

            if not files:
                self.logger.info("Snapshot: no files found.")
            else:
                for f in files:
                    parents = f.get("parents", [])
                    parent_id = parents[0] if parents else None
                    parent_name = self.get_parent_name(parent_id)

                    mime_type = f.get("mimeType") or ""
                    file_id = f.get("id")
                    name = f.get("name") or file_id

                    # Index folders
                    if mime_type == self.DRIVE_FOLDER:
                        self.save_drive_folder(folder_id=file_id, folder_name=name, parent_id=parent_id)
                        continue

                    # Skip Google-native files (export would be needed)
                    if mime_type.startswith(self.DRIVE_MIME_PREFIX):
                        continue

                    file_name = name.replace(" ", "_")
                    base_dir = self.output_path.get(parent_name) or self.output_root
                    out_path = Path(base_dir) / file_name

                    self.download_file(file_id, out_path, drive_parent_name=parent_name)

            start_token = self.get_start_page_token()
            self.save_token(start_token)
            self.logger.info("Saved startPageToken. Next run will use changes().list for deltas.")
            return

        # INCREMENTAL: token exists => changes
        self.logger.info("Using saved startPageToken: %s", token)
        changes, new_token = self.list_all_changes_since(token)

        if not changes:
            self.logger.info("No changes since last token.")
        else:
            for ch in changes:
                file_id = ch.get("fileId")
                removed = ch.get("removed", False)
                file_obj = ch.get("file") or {}
                when = ch.get("time")

                if removed:
                    self.logger.info("REMOVED fileId=%s time=%s", file_id, when)
                    continue

                mime_type = file_obj.get("mimeType") or ""
                if not file_obj:
                    self.logger.info("CHANGED fileId=%s time=%s (file metadata unavailable)", file_id, when)
                    continue

                # Index folder changes too
                if mime_type == self.DRIVE_FOLDER:
                    parents = file_obj.get("parents", [])
                    parent_id = parents[0] if parents else None
                    self.save_drive_folder(folder_id=file_id, folder_name=file_obj.get("name", ""), parent_id=parent_id)
                    continue

                if mime_type.startswith(self.DRIVE_MIME_PREFIX):
                    continue

                parents = file_obj.get("parents", [])
                parent_id = parents[0] if parents else None
                parent_name = self.get_parent_name(parent_id)

                name = (file_obj.get("name") or file_id).replace(" ", "_")
                base_dir = self.output_path.get(parent_name) or self.output_root
                out_path = Path(base_dir) / name

                self.download_file(file_id, out_path, drive_parent_name=parent_name)

        if new_token:
            self.save_token(new_token)
            self.prune_token_history()
            self.logger.info("Saved newStartPageToken for next incremental sync and pruned token history.")
