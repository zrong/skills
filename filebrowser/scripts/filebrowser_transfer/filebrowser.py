"""FileBrowser Quantum source adapter."""

from __future__ import annotations

import mimetypes
from pathlib import Path, PurePosixPath
from typing import Literal, cast

import httpx

from .models import FileBrowserSourceConfig, RemoteFile


class FileBrowserError(RuntimeError):
    """Raised for FileBrowser configuration, protocol, and transfer errors."""


def normalize_remote_path(value: str) -> str:
    raw = value.strip().replace("\\", "/")
    if not raw:
        raise FileBrowserError("FileBrowser path must not be empty")
    if not raw.startswith("/"):
        raw = f"/{raw}"
    path = PurePosixPath(raw)
    if ".." in path.parts or len(raw) > 1000:
        raise FileBrowserError("FileBrowser path must be absolute and must not contain '..'")
    normalized = "/" + "/".join(part for part in path.parts if part not in {"/", "."})
    return normalized


def _non_negative_int(value: object) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value if value >= 0 else None
    if isinstance(value, str):
        try:
            parsed = int(value)
        except ValueError:
            return None
        return parsed if parsed >= 0 else None
    return None


class FileBrowserClient:
    """Synchronous client for streaming FileBrowser downloads and uploads."""

    def __init__(
        self,
        config: FileBrowserSourceConfig,
        *,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self.config = config
        token = config.token.resolve(f"FileBrowser token for source {config.name}")
        self._client = httpx.Client(
            base_url=config.base_url,
            headers={
                "Authorization": f"Bearer {token}",
                # FileBrowser may gzip resource downloads while preserving a
                # compressed Content-Length.  httpx transparently decodes the
                # body, so request the identity representation to keep the
                # transport length usable for upload/download verification.
                "Accept-Encoding": "identity",
            },
            timeout=httpx.Timeout(config.timeout_seconds, connect=30.0),
            verify=config.verify_tls,
            transport=transport,
        )

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> FileBrowserClient:
        return self

    def __exit__(self, _exc_type: object, _exc: object, _traceback: object) -> None:
        self.close()

    def metadata(self, remote_path: str) -> RemoteFile:
        path = normalize_remote_path(remote_path)
        response = self._client.get(
            "/api/resources",
            params={
                "source": self.config.source,
                "path": path,
                "skipExtendedAttrs": "true",
                "metadata": "false",
                "content": "false",
            },
        )
        self._raise_for_status(response, "read FileBrowser metadata")
        try:
            value: object = response.json()
        except ValueError as exc:
            raise FileBrowserError("FileBrowser returned invalid metadata JSON") from exc
        if not isinstance(value, dict):
            raise FileBrowserError("FileBrowser returned unexpected metadata")
        payload = cast(dict[str, object], value)
        if payload.get("type") == "directory":
            raise FileBrowserError("FileBrowser directories cannot be uploaded as one object")

        size = _non_negative_int(payload.get("size"))
        self._check_size(size)
        name = PurePosixPath(path).name
        content_type_value = payload.get("type")
        content_type = (
            content_type_value
            if isinstance(content_type_value, str) and "/" in content_type_value
            else mimetypes.guess_type(name)[0] or "application/octet-stream"
        )
        return RemoteFile(
            source=self.config.source,
            path=path,
            name=name,
            size=size,
            content_type=content_type,
        )

    def download(self, remote: RemoteFile, destination: Path) -> int:
        destination.parent.mkdir(parents=True, exist_ok=True)
        partial = destination.with_suffix(f"{destination.suffix}.part")
        partial.unlink(missing_ok=True)
        try:
            written = self._download_modern(remote, partial)
            partial.replace(destination)
            return written
        except Exception:
            partial.unlink(missing_ok=True)
            raise

    # ------------------------------------------------------------------
    # File management operations (info / update / delete / move / search
    # / preview / list_sources / upload_file_to_dir / download_files).
    # These are not used by the transfer pipeline; they exist so other
    # skills can share a single FileBrowser client instead of writing
    # their own HTTP wrappers.
    # ------------------------------------------------------------------

    def info(
        self,
        path: str,
        *,
        content: bool = False,
        checksum: str | None = None,
    ) -> dict[str, object]:
        """Return raw FileBrowser metadata for ``path``.

        ``content=True`` asks FileBrowser to inline the file body for
        small text files. ``checksum`` is forwarded as the ``checksum``
        query parameter when supplied.
        """
        normalized = normalize_remote_path(path)
        params: dict[str, str] = {"source": self.config.source, "path": normalized}
        if content:
            params["content"] = "true"
        if checksum:
            params["checksum"] = checksum
        response = self._client.get("/api/resources", params=params)
        self._raise_for_status(response, "read FileBrowser resource info")
        try:
            value: object = response.json()
        except ValueError as exc:
            raise FileBrowserError("FileBrowser returned invalid resource info JSON") from exc
        if not isinstance(value, dict):
            raise FileBrowserError("FileBrowser returned unexpected resource info")
        return cast(dict[str, object], value)

    def exists(self, path: str) -> bool:
        """Return True if ``path`` resolves to a file or directory on this source."""
        try:
            self.info(path)
        except FileBrowserError as exc:
            if "does not exist" in str(exc):
                return False
            raise
        return True

    def list_files(self, path: str) -> list[dict[str, object]]:
        """Return immediate child entries (files and folders) of a directory."""
        payload = self.info(path)
        # Quantum splits listing children into "files" and "folders"; when a
        # directory has none of one kind the key is absent entirely.
        items: list[dict[str, object]] = []
        for key in ("folders", "files"):
            value = payload.get(key)
            if isinstance(value, list):
                items.extend(
                    cast(dict[str, object], item)
                    for item in cast(list[object], value)
                    if isinstance(item, dict)
                )
        return items

    def ensure_dir(self, path: str) -> str:
        """Create ``path`` (a directory) if missing; return the normalized path.

        Returns the existing directory path unchanged when it already
        exists. Refuses to overwrite or replace an existing file path
        that is not a directory.
        """
        normalized = normalize_remote_path(path)
        try:
            existing = self.info(normalized)
        except FileBrowserError as exc:
            if "does not exist" not in str(exc):
                raise
            existing = None
        if existing is not None:
            if existing.get("type") != "directory":
                raise FileBrowserError(
                    f"FileBrowser path exists and is not a directory: {normalized}"
                )
            return normalized
        response = self._client.post(
            "/api/resources",
            params={
                "source": self.config.source,
                "path": normalized,
                "isDir": "true",
            },
        )
        if response.status_code == 409:
            existing = self.info(normalized)
            if existing.get("type") == "directory":
                return normalized
        self._raise_for_status(response, "create FileBrowser directory")
        return normalized

    def update_file(self, path: str, content: bytes, *, overwrite: bool = False) -> None:
        """Overwrite ``path`` with ``content`` via a PUT-style raw upload."""
        normalized = normalize_remote_path(path)
        if normalized == "/":
            raise FileBrowserError("Refusing to update the FileBrowser root path")
        self._ensure_destination_writable(
            normalized,
            overwrite=overwrite,
            overwrite_flag="--override",
        )
        response = self._client.put(
            "/api/resources",
            params={
                "source": self.config.source,
                "path": normalized,
            },
            content=content,
            headers={"content-type": "application/octet-stream"},
        )
        self._raise_for_status(response, "update FileBrowser file")

    def delete(self, path: str) -> None:
        """Remove ``path`` (file or directory) from this source."""
        normalized = normalize_remote_path(path)
        if normalized == "/":
            raise FileBrowserError("Refusing to delete the FileBrowser root path")
        response = self._client.delete(
            "/api/resources",
            params={"source": self.config.source, "path": normalized},
        )
        self._raise_for_status(response, "delete FileBrowser resource")

    def move(
        self,
        from_path: str,
        to_path: str,
        *,
        action: Literal["rename", "copy"] = "rename",
        overwrite: bool = False,
    ) -> None:
        """Rename/copy a resource. ``action`` selects the verb, ``overwrite`` replaces target."""
        source = normalize_remote_path(from_path)
        destination = normalize_remote_path(to_path)
        if source == "/":
            raise FileBrowserError("Refusing to move the FileBrowser root path")
        if source == destination:
            raise FileBrowserError("FileBrowser move source and destination are identical")
        # Quantum ignores the body's overwrite flag on conflicts and may
        # auto-suffix the destination instead of failing, so enforce the
        # documented "no overwrite by default" contract client-side.
        self._ensure_destination_writable(destination, overwrite=overwrite)
        # FileBrowser Quantum requires a JSON body with per-item source+path
        # pairs; the upstream query-string form is rejected with HTTP 400.
        response = self._client.patch(
            "/api/resources",
            json={
                "items": [
                    {
                        "fromSource": self.config.source,
                        "fromPath": source,
                        "toSource": self.config.source,
                        "toPath": destination,
                    }
                ],
                "action": action,
                "overwrite": overwrite,
            },
        )
        self._raise_for_status(response, "move FileBrowser resource")
        failures = self._patch_failure_messages(response)
        if failures:
            raise FileBrowserError("FileBrowser move/copy failed: " + "; ".join(failures))

    @staticmethod
    def _patch_failure_messages(response: httpx.Response) -> list[str]:
        """Extract per-item failure messages from a Quantum patch response."""
        try:
            value: object = response.json()
        except ValueError:
            return []
        if not isinstance(value, dict):
            return []
        items = value.get("failed")
        if not isinstance(items, list):
            return []
        messages: list[str] = []
        for item in cast(list[object], items):
            if not isinstance(item, dict):
                continue
            payload = cast(dict[str, object], item)
            message = payload.get("message")
            from_path = payload.get("fromPath")
            if isinstance(message, str) and message:
                messages.append(message)
            elif isinstance(from_path, str):
                messages.append(f"{from_path}: unknown failure")
            else:
                messages.append("unknown failure")
        return messages

    def search(self, query: str, *, scope: str | None = None) -> list[dict[str, object]]:
        """List matching files. ``scope`` narrows the search to a directory path."""
        # Quantum exposes search at /api/tools/search and requires either
        # ``sources`` (comma-separated) or scoped entries formatted
        # ``sourceName:relativePath``; bare paths are rejected.
        params: dict[str, str] = {"sources": self.config.source, "query": query}
        if scope:
            params["scope"] = f"{self.config.source}:{normalize_remote_path(scope)}"
        response = self._client.get("/api/tools/search", params=params)
        self._raise_for_status(response, "search FileBrowser")
        try:
            value: object = response.json()
        except ValueError as exc:
            raise FileBrowserError("FileBrowser returned invalid search JSON") from exc
        if not isinstance(value, list):
            raise FileBrowserError("FileBrowser returned unexpected search results")
        return [
            cast(dict[str, object], item)
            for item in cast(list[object], value)
            if isinstance(item, dict)
        ]

    def preview(self, path: str, size: str | None = None, output: str | None = None) -> Path:
        """Download a thumbnail/preview to ``output`` (or a temp path) and return it."""
        normalized = normalize_remote_path(path)
        # Quantum serves previews from /api/resources/preview and rejects
        # requests without an explicit source ("small"|"large"|"xlarge"|"original").
        params: dict[str, str] = {"source": self.config.source, "path": normalized}
        if size:
            params["size"] = size
        response = self._client.get("/api/resources/preview", params=params)
        self._raise_for_status(response, "fetch FileBrowser preview")
        if output:
            destination = Path(output)
        else:
            from tempfile import NamedTemporaryFile

            suffix = Path(normalized).suffix or ".bin"
            with NamedTemporaryFile(delete=False, suffix=suffix) as handle:
                destination = Path(handle.name)
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(response.content)
        return destination

    def list_sources(self) -> list[dict[str, object]]:
        """Return configured sources (best-effort; tolerates empty listings)."""
        response = self._client.get("/api/settings", params={"property": "sources"})
        self._raise_for_status(response, "list FileBrowser sources")
        try:
            value: object = response.json()
        except ValueError as exc:
            raise FileBrowserError("FileBrowser returned invalid sources JSON") from exc
        if not isinstance(value, list):
            return []
        return [
            cast(dict[str, object], item)
            for item in cast(list[object], value)
            if isinstance(item, dict)
        ]

    def upload_file_to_dir(
        self,
        local_path: str | Path,
        remote_dir: str,
        *,
        remote_name: str | None = None,
        overwrite: bool = False,
    ) -> RemoteFile:
        """Ensure ``remote_dir`` exists then upload ``local_path`` into it.

        Returns the resulting RemoteFile with verified remote size.
        """
        local = Path(local_path)
        if not local.is_file():
            raise FileBrowserError(f"Local path is not a file: {local}")
        self.ensure_dir(remote_dir)
        target_name = remote_name or local.name
        destination = normalize_remote_path(f"{remote_dir.rstrip('/')}/{target_name}")
        return self.upload_file(local, destination, overwrite=overwrite)

    def download_files(
        self,
        files: list[str],
        *,
        algo: str = "zip",
        output: str | Path | None = None,
    ) -> Path:
        """Bundle multiple ``files`` (each ``<source-name>::/path`` or ``/path``)
        into a single archive and stream it to ``output`` (defaults to a temp
        file with the right extension).

        FileBrowser Quantum serves bundles through the same downloadHandler as
        single-file downloads: repeated ``file`` query parameters plus ``source``
        and ``algo``.  Quantum only supports ``zip`` and ``tar.gz`` archives.
        """
        if not files:
            raise FileBrowserError("download_files requires at least one path")
        if algo not in ("zip", "tar.gz"):
            raise FileBrowserError(
                f"download_files algo must be zip or tar.gz, got {algo!r}"
            )
        params: list[tuple[str, str]] = [
            ("file", self._strip_source_prefix(item)) for item in files
        ]
        params += [("algo", algo), ("source", self.config.source)]
        with self._client.stream(
            "GET", "/api/resources/download", params=params
        ) as response:
            if response.status_code in {404, 405}:
                response.read()
                # Older Quantum builds expose the same handler at /api/raw.
                with self._client.stream("GET", "/api/raw", params=params) as legacy:
                    self._raise_for_status(legacy, "download FileBrowser bundle")
                    return self._write_bundle(legacy, output, algo)
            self._raise_for_status(response, "download FileBrowser bundle")
            return self._write_bundle(response, output, algo)

    def _strip_source_prefix(self, item: str) -> str:
        """Drop the skill-level ``<source-name>::`` prefix and normalize the path."""
        prefix = f"{self.config.name}::"
        if item.startswith(prefix):
            item = item[len(prefix):]
        elif "::" in item:
            other = item.split("::", 1)[0]
            raise FileBrowserError(
                f"download_files path source {other!r} does not match "
                f"configured source {self.config.name!r}"
            )
        return normalize_remote_path(item)

    @staticmethod
    def _write_bundle(
        response: httpx.Response, output: str | Path | None, algo: str
    ) -> Path:
        if output is None:
            suffix = ".zip" if algo == "zip" else f".{algo}"
            from tempfile import NamedTemporaryFile

            with NamedTemporaryFile(delete=False, suffix=suffix) as handle:
                destination = Path(handle.name)
        else:
            destination = Path(output)
        destination.parent.mkdir(parents=True, exist_ok=True)
        with destination.open("wb") as target:
            for chunk in response.iter_bytes():
                target.write(chunk)
        return destination

    def upload_file(
        self,
        local_path: Path,
        remote_path: str,
        *,
        overwrite: bool = False,
    ) -> RemoteFile:
        """Stream a local file to FileBrowser and verify its remote size."""
        if not local_path.is_file():
            raise FileBrowserError(f"Local path is not a file: {local_path}")
        size = local_path.stat().st_size
        self._check_size(size)
        destination = normalize_remote_path(remote_path)
        self._ensure_destination_writable(destination, overwrite=overwrite)

        if size == 0 or size <= self.config.upload_chunk_bytes:
            self._upload_one(local_path, destination, overwrite=overwrite)
        else:
            self._upload_chunks(local_path, destination, size, overwrite=overwrite)

        remote = self.metadata(destination)
        downloaded_size = self._download_response_size(remote)
        verified_size = downloaded_size if downloaded_size is not None else remote.size
        if verified_size != size:
            raise FileBrowserError(
                "FileBrowser size mismatch after upload: "
                f"expected {size} bytes, got {verified_size}"
            )
        return RemoteFile(
            source=remote.source,
            path=remote.path,
            name=remote.name,
            size=verified_size,
            content_type=remote.content_type,
        )

    def _ensure_destination_writable(
        self,
        path: str,
        *,
        overwrite: bool,
        overwrite_flag: str = "--overwrite",
    ) -> None:
        response = self._client.get(
            "/api/resources",
            params={
                "source": self.config.source,
                "path": path,
                "skipExtendedAttrs": "true",
                "metadata": "false",
                "content": "false",
            },
        )
        if response.status_code == 404:
            return
        self._raise_for_status(response, "read FileBrowser destination metadata")
        try:
            value: object = response.json()
        except ValueError as exc:
            raise FileBrowserError(
                "FileBrowser returned invalid destination metadata JSON"
            ) from exc
        if not isinstance(value, dict):
            raise FileBrowserError("FileBrowser returned unexpected destination metadata")
        payload = cast(dict[str, object], value)
        if payload.get("type") == "directory":
            raise FileBrowserError("FileBrowser destination path is a directory")
        if not overwrite:
            raise FileBrowserError(
                f"FileBrowser destination already exists; pass {overwrite_flag} to replace it"
            )

    def _upload_one(self, local_path: Path, destination: str, *, overwrite: bool) -> None:
        with local_path.open("rb") as stream:
            response = self._client.post(
                "/api/resources",
                params={
                    "source": self.config.source,
                    "path": destination,
                    "override": str(overwrite).lower(),
                },
                content=stream,
                headers={"content-type": "application/octet-stream"},
            )
        self._raise_for_status(response, "upload FileBrowser file")

    def _upload_chunks(
        self,
        local_path: Path,
        destination: str,
        size: int,
        *,
        overwrite: bool,
    ) -> None:
        offset = 0
        with local_path.open("rb") as stream:
            while chunk := stream.read(self.config.upload_chunk_bytes):
                response = self._client.post(
                    "/api/resources",
                    params={
                        "source": self.config.source,
                        "path": destination,
                        "override": str(overwrite).lower(),
                    },
                    content=chunk,
                    headers={
                        "content-type": "application/octet-stream",
                        "X-File-Chunk-Offset": str(offset),
                        "X-File-Total-Size": str(size),
                    },
                )
                self._raise_for_status(response, "upload FileBrowser file chunk")
                offset += len(chunk)

    def _download_modern(self, remote: RemoteFile, destination: Path) -> int:
        with self._client.stream(
            "GET",
            "/api/resources/download",
            params={"source": remote.source, "file": remote.path},
        ) as response:
            if response.status_code in {404, 405}:
                return self._download_legacy(remote, destination)
            self._raise_for_status(response, "download FileBrowser file")
            return self._write_stream(response, remote, destination)

    def _download_response_size(self, remote: RemoteFile) -> int | None:
        with self._client.stream(
            "GET",
            "/api/resources/download",
            params={"source": remote.source, "file": remote.path},
        ) as response:
            if response.status_code in {404, 405}:
                return self._legacy_download_response_size(remote)
            self._raise_for_status(response, "verify FileBrowser upload")
            return _non_negative_int(response.headers.get("content-length"))

    def _legacy_download_response_size(self, remote: RemoteFile) -> int | None:
        with self._client.stream(
            "GET",
            "/api/raw",
            params={"files": f"{remote.source}::{remote.path}", "inline": "true"},
        ) as response:
            self._raise_for_status(response, "verify FileBrowser upload through legacy API")
            return _non_negative_int(response.headers.get("content-length"))

    def _download_legacy(self, remote: RemoteFile, destination: Path) -> int:
        with self._client.stream(
            "GET",
            "/api/raw",
            params={"files": f"{remote.source}::{remote.path}", "inline": "true"},
        ) as response:
            self._raise_for_status(response, "download FileBrowser file through legacy API")
            return self._write_stream(response, remote, destination)

    def _write_stream(
        self,
        response: httpx.Response,
        remote: RemoteFile,
        destination: Path,
    ) -> int:
        header_size = _non_negative_int(response.headers.get("content-length"))
        self._check_size(header_size)
        written = 0
        with destination.open("wb") as output:
            for chunk in response.iter_bytes():
                written += len(chunk)
                self._check_size(written)
                output.write(chunk)
        if header_size is not None and written != header_size:
            raise FileBrowserError(
                "FileBrowser download response size mismatch: "
                f"expected {header_size} bytes, downloaded {written}"
            )
        if header_size is None and remote.size is not None and written != remote.size:
            raise FileBrowserError(
                f"FileBrowser size mismatch: expected {remote.size} bytes, downloaded {written}"
            )
        return written

    def _check_size(self, size: int | None) -> None:
        limit = self.config.max_transfer_bytes
        if size is not None and limit > 0 and size > limit:
            raise FileBrowserError(
                f"FileBrowser file exceeds max_transfer_bytes ({size} > {limit})"
            )

    @staticmethod
    def _raise_for_status(response: httpx.Response, operation: str) -> None:
        if response.is_success:
            return
        message = {
            400: "FileBrowser rejected the request",
            401: "FileBrowser token is invalid",
            403: "FileBrowser denied access to the resource",
            404: "FileBrowser resource does not exist",
            409: "FileBrowser reported a resource conflict",
            413: "FileBrowser rejected the file because it is too large",
        }.get(response.status_code, f"Failed to {operation}: HTTP {response.status_code}")
        raise FileBrowserError(message)
