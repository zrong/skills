"""FileBrowser Quantum source adapter."""

from __future__ import annotations

import mimetypes
from pathlib import Path, PurePosixPath
from typing import cast

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
    if normalized == "/":
        raise FileBrowserError("FileBrowser path must identify a file")
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
            headers={"Authorization": f"Bearer {token}"},
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
        if remote.size != size:
            raise FileBrowserError(
                f"FileBrowser size mismatch after upload: expected {size} bytes, got {remote.size}"
            )
        return remote

    def _ensure_destination_writable(self, path: str, *, overwrite: bool) -> None:
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
                "FileBrowser destination already exists; pass --overwrite to replace it"
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
        if remote.size is not None and written != remote.size:
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
