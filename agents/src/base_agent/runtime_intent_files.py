"""No-follow filesystem primitives for private runtime-intent records."""

from __future__ import annotations

import json
import os
import secrets
import stat
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path

from base_agent.runtime_intent_models import RuntimeIntentError

_DIRECTORY_FLAGS = os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC | os.O_NOFOLLOW
_READ_FLAGS = os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW
_WRITE_FLAGS = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC | os.O_NOFOLLOW


@dataclass(frozen=True, slots=True)
class SafeDirectory:
    """An open directory whose public path and inode still agree."""

    path: Path
    descriptor: int
    device: int
    inode: int
    required_mode: int | None

    def verify(self) -> None:
        descriptors = _open_directory_chain(self.path, create=False, final_mode=None)
        try:
            current = os.fstat(descriptors[-1])
            held = os.fstat(self.descriptor)
            if (
                not stat.S_ISDIR(held.st_mode)
                or (held.st_dev, held.st_ino) != (self.device, self.inode)
                or (current.st_dev, current.st_ino) != (self.device, self.inode)
            ):
                raise RuntimeIntentError("unsafe_path", f"directory identity changed: {self.path}")
            if self.required_mode is not None and (
                stat.S_IMODE(held.st_mode) != self.required_mode
                or stat.S_IMODE(current.st_mode) != self.required_mode
            ):
                raise RuntimeIntentError(
                    "unsafe_mode", f"private directory mode changed: {self.path}"
                )
        except OSError as exc:
            raise RuntimeIntentError(
                "unsafe_path", f"directory cannot be revalidated: {self.path}"
            ) from exc
        finally:
            _close_all(descriptors)

    def close(self) -> None:
        try:
            os.close(self.descriptor)
        except OSError as exc:
            raise RuntimeIntentError(
                "storage_failure", f"directory descriptor could not be closed: {self.path}"
            ) from exc


def validate_absolute_path(path: object) -> None:
    if (
        not isinstance(path, Path)
        or not path.is_absolute()
        or path.anchor != os.sep
        or ".." in path.parts
        or path == Path(os.sep)
    ):
        raise RuntimeIntentError("unsafe_path", "runtime root must be an absolute canonical path")


def _close_all(descriptors: list[int]) -> None:
    for descriptor in reversed(descriptors):
        with suppress(OSError):
            os.close(descriptor)


def _open_directory_chain(path: Path, *, create: bool, final_mode: int | None) -> list[int]:
    validate_absolute_path(path)
    descriptors: list[int] = []
    try:
        descriptors.append(os.open(os.sep, _DIRECTORY_FLAGS))
        for index, component in enumerate(path.parts[1:], start=1):
            parent = descriptors[-1]
            is_final = index == len(path.parts) - 1
            try:
                current = os.stat(component, dir_fd=parent, follow_symlinks=False)
            except FileNotFoundError:
                if not create:
                    raise RuntimeIntentError(
                        "unsafe_path", f"runtime directory does not exist: {path}"
                    ) from None
                mode = final_mode if is_final and final_mode is not None else 0o700
                try:
                    os.mkdir(component, mode=mode, dir_fd=parent)
                    os.fsync(parent)
                except FileExistsError:
                    pass
                except OSError as exc:
                    raise RuntimeIntentError(
                        "storage_failure", f"runtime directory could not be created: {path}"
                    ) from exc
                try:
                    current = os.stat(component, dir_fd=parent, follow_symlinks=False)
                except OSError as exc:
                    raise RuntimeIntentError(
                        "unsafe_path", f"runtime directory cannot be inspected: {path}"
                    ) from exc
            except OSError as exc:
                raise RuntimeIntentError(
                    "unsafe_path", f"runtime directory cannot be inspected: {path}"
                ) from exc
            if not stat.S_ISDIR(current.st_mode) or stat.S_ISLNK(current.st_mode):
                raise RuntimeIntentError(
                    "unsafe_path", f"runtime directory contains a non-directory: {path}"
                )
            descriptor: int | None = None
            try:
                descriptor = os.open(component, _DIRECTORY_FLAGS, dir_fd=parent)
                opened = os.fstat(descriptor)
                latest = os.stat(component, dir_fd=parent, follow_symlinks=False)
            except OSError as exc:
                if descriptor is not None:
                    with suppress(OSError):
                        os.close(descriptor)
                raise RuntimeIntentError(
                    "unsafe_path", f"runtime directory cannot be opened safely: {path}"
                ) from exc
            if (
                not stat.S_ISDIR(opened.st_mode)
                or not stat.S_ISDIR(latest.st_mode)
                or (opened.st_dev, opened.st_ino) != (latest.st_dev, latest.st_ino)
            ):
                os.close(descriptor)
                raise RuntimeIntentError(
                    "unsafe_path", f"runtime directory changed while opening: {path}"
                )
            descriptors.append(descriptor)
        return descriptors
    except BaseException:
        _close_all(descriptors)
        raise


def open_root_directory(path: Path) -> SafeDirectory:
    descriptors = _open_directory_chain(path, create=True, final_mode=0o700)
    descriptor = descriptors.pop()
    _close_all(descriptors)
    try:
        opened = os.fstat(descriptor)
    except OSError as exc:
        with suppress(OSError):
            os.close(descriptor)
        raise RuntimeIntentError(
            "unsafe_path", f"runtime root cannot be inspected safely: {path}"
        ) from exc
    return SafeDirectory(path, descriptor, opened.st_dev, opened.st_ino, None)


def open_private_child(
    parent: SafeDirectory, name: str, *, enforce_private_mode: bool = True
) -> SafeDirectory:
    parent.verify()
    path = parent.path / name
    try:
        try:
            current = os.stat(name, dir_fd=parent.descriptor, follow_symlinks=False)
        except FileNotFoundError:
            try:
                os.mkdir(name, mode=0o700, dir_fd=parent.descriptor)
                os.fsync(parent.descriptor)
            except FileExistsError:
                pass
            except OSError as exc:
                raise RuntimeIntentError(
                    "storage_failure", f"private directory could not be persisted: {path}"
                ) from exc
            current = os.stat(name, dir_fd=parent.descriptor, follow_symlinks=False)
        if not stat.S_ISDIR(current.st_mode) or stat.S_ISLNK(current.st_mode):
            raise RuntimeIntentError("unsafe_path", f"private path is not a directory: {path}")
        if enforce_private_mode and stat.S_IMODE(current.st_mode) != 0o700:
            raise RuntimeIntentError("unsafe_mode", f"private directory is not mode 0700: {path}")
        descriptor: int | None = None
        try:
            descriptor = os.open(name, _DIRECTORY_FLAGS, dir_fd=parent.descriptor)
            opened = os.fstat(descriptor)
            latest = os.stat(name, dir_fd=parent.descriptor, follow_symlinks=False)
        except OSError as exc:
            if descriptor is not None:
                with suppress(OSError):
                    os.close(descriptor)
            raise RuntimeIntentError(
                "unsafe_path", f"private directory cannot be opened safely: {path}"
            ) from exc
        if (opened.st_dev, opened.st_ino) != (latest.st_dev, latest.st_ino):
            os.close(descriptor)
            raise RuntimeIntentError("unsafe_path", f"private directory changed: {path}")
        if enforce_private_mode and (
            stat.S_IMODE(opened.st_mode) != 0o700 or stat.S_IMODE(latest.st_mode) != 0o700
        ):
            os.close(descriptor)
            raise RuntimeIntentError("unsafe_mode", f"private directory is not mode 0700: {path}")
        return SafeDirectory(
            path,
            descriptor,
            opened.st_dev,
            opened.st_ino,
            0o700 if enforce_private_mode else None,
        )
    except RuntimeIntentError:
        raise
    except OSError as exc:
        raise RuntimeIntentError("unsafe_path", f"private directory is unsafe: {path}") from exc


def _record_kind(directory: SafeDirectory, name: str) -> os.stat_result | None:
    directory.verify()
    try:
        record_stat = os.stat(name, dir_fd=directory.descriptor, follow_symlinks=False)
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise RuntimeIntentError("unsafe_path", f"record cannot be inspected: {name}") from exc
    if not stat.S_ISREG(record_stat.st_mode) or stat.S_ISLNK(record_stat.st_mode):
        raise RuntimeIntentError("unsafe_path", f"record is not a regular file: {name}")
    if stat.S_IMODE(record_stat.st_mode) != 0o600:
        raise RuntimeIntentError("unsafe_mode", f"record is not mode 0600: {name}")
    return record_stat


def read_record_bytes(
    directory: SafeDirectory, name: str, *, max_record_bytes: int
) -> bytes | None:
    before = _record_kind(directory, name)
    if before is None:
        return None
    descriptor: int | None = None
    try:
        descriptor = os.open(name, _READ_FLAGS, dir_fd=directory.descriptor)
        opened = os.fstat(descriptor)
        latest = os.stat(name, dir_fd=directory.descriptor, follow_symlinks=False)
        if (
            not stat.S_ISREG(opened.st_mode)
            or before.st_nlink != 1
            or opened.st_nlink != 1
            or latest.st_nlink != 1
            or (opened.st_dev, opened.st_ino) != (latest.st_dev, latest.st_ino)
            or (opened.st_dev, opened.st_ino) != (before.st_dev, before.st_ino)
        ):
            raise RuntimeIntentError("unsafe_path", f"record changed while opening: {name}")
        if stat.S_IMODE(opened.st_mode) != 0o600 or stat.S_IMODE(latest.st_mode) != 0o600:
            raise RuntimeIntentError("unsafe_mode", f"record mode changed while opening: {name}")
        chunks: list[bytes] = []
        remaining = max_record_bytes + 1
        while remaining > 0:
            chunk = os.read(descriptor, min(65_536, remaining))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        content = b"".join(chunks)
        after = os.fstat(descriptor)
        current = os.stat(name, dir_fd=directory.descriptor, follow_symlinks=False)
        stable_fields = ("st_dev", "st_ino", "st_size", "st_mtime_ns", "st_ctime_ns", "st_nlink")
        if any(getattr(after, field) != getattr(opened, field) for field in stable_fields) or any(
            getattr(current, field) != getattr(after, field) for field in stable_fields
        ):
            raise RuntimeIntentError("unsafe_path", f"record changed while reading: {name}")
        if stat.S_IMODE(after.st_mode) != 0o600 or stat.S_IMODE(current.st_mode) != 0o600:
            raise RuntimeIntentError("unsafe_mode", f"record mode changed while reading: {name}")
        if len(content) > max_record_bytes:
            raise RuntimeIntentError("corrupt_record", f"record exceeds configured limit: {name}")
        directory.verify()
        return content
    except RuntimeIntentError:
        raise
    except OSError as exc:
        raise RuntimeIntentError("unsafe_path", f"record cannot be read safely: {name}") from exc
    finally:
        if descriptor is not None:
            with suppress(OSError):
                os.close(descriptor)


def validate_record_admission(directory: SafeDirectory, name: str) -> None:
    """Reject unsafe file kinds and modes without parsing record contents."""

    metadata = _record_kind(directory, name)
    if metadata is None:
        raise RuntimeIntentError("unsafe_path", f"record disappeared during admission: {name}")
    if metadata.st_nlink != 1:
        raise RuntimeIntentError("unsafe_path", f"record has multiple links: {name}")


def remove_private_staging_record(directory: SafeDirectory, name: str) -> None:
    """Remove one exact private crash-staging file and persist that removal."""

    before = _record_kind(directory, name)
    if before is None:
        return
    descriptor: int | None = None
    try:
        descriptor = os.open(name, _READ_FLAGS, dir_fd=directory.descriptor)
        opened = os.fstat(descriptor)
        current = os.stat(name, dir_fd=directory.descriptor, follow_symlinks=False)
        if (opened.st_dev, opened.st_ino) != (before.st_dev, before.st_ino) or (
            current.st_dev,
            current.st_ino,
        ) != (before.st_dev, before.st_ino):
            raise RuntimeIntentError("unsafe_path", f"staging record changed: {name}")
        if stat.S_IMODE(opened.st_mode) != 0o600 or stat.S_IMODE(current.st_mode) != 0o600:
            raise RuntimeIntentError("unsafe_mode", f"staging record mode changed: {name}")
        os.close(descriptor)
        descriptor = None
        directory.verify()
        latest = os.stat(name, dir_fd=directory.descriptor, follow_symlinks=False)
        if (latest.st_dev, latest.st_ino) != (before.st_dev, before.st_ino):
            raise RuntimeIntentError("unsafe_path", f"staging record changed: {name}")
        os.unlink(name, dir_fd=directory.descriptor)
        os.fsync(directory.descriptor)
        directory.verify()
    except RuntimeIntentError:
        raise
    except OSError as exc:
        raise RuntimeIntentError(
            "storage_failure", f"staging record could not be removed: {name}"
        ) from exc
    finally:
        if descriptor is not None:
            with suppress(OSError):
                os.close(descriptor)


def decode_record(content: bytes, *, name: str) -> object:
    try:
        return json.loads(content.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RuntimeIntentError("corrupt_record", f"record is not canonical JSON: {name}") from exc


def _write_all(descriptor: int, content: bytes) -> None:
    offset = 0
    while offset < len(content):
        written = os.write(descriptor, content[offset:])
        if written <= 0:
            raise OSError("short write")
        offset += written


def _temporary_name(name: str) -> str:
    return f".{name}.{secrets.token_hex(12)}.tmp"


def _cleanup_temporary(
    directory: SafeDirectory,
    temporary: str,
    descriptor: int | None,
    *,
    primary_error: BaseException,
) -> None:
    if descriptor is not None:
        try:
            os.close(descriptor)
        except BaseException as cleanup_error:
            primary_error.add_note(f"Temporary-file cleanup close failed: {cleanup_error}")
            with suppress(OSError):
                os.close(descriptor)
    try:
        os.unlink(temporary, dir_fd=directory.descriptor)
    except BaseException as cleanup_error:
        primary_error.add_note(f"Temporary-file cleanup unlink failed: {cleanup_error}")
        with suppress(OSError):
            os.unlink(temporary, dir_fd=directory.descriptor)


def _write_private_temporary(
    directory: SafeDirectory, name: str, content: bytes
) -> tuple[str, int]:
    directory.verify()
    temporary = _temporary_name(name)
    descriptor = os.open(
        temporary,
        _WRITE_FLAGS,
        0o600,
        dir_fd=directory.descriptor,
    )
    try:
        os.fchmod(descriptor, 0o600)
        _write_all(descriptor, content)
        os.fsync(descriptor)
    except BaseException as primary_error:
        _cleanup_temporary(
            directory,
            temporary,
            descriptor,
            primary_error=primary_error,
        )
        raise
    return temporary, descriptor


def _publish_exclusive(
    directory: SafeDirectory,
    name: str,
    content: bytes,
    *,
    record_kind: str,
) -> bool:

    temporary: str | None = None
    descriptor: int | None = None
    try:
        temporary, descriptor = _write_private_temporary(directory, name, content)
        os.close(descriptor)
        descriptor = None
        try:
            os.link(
                temporary,
                name,
                src_dir_fd=directory.descriptor,
                dst_dir_fd=directory.descriptor,
                follow_symlinks=False,
            )
        except FileExistsError:
            return False
        directory.verify()
        os.fsync(directory.descriptor)
        directory.verify()
        return True
    except FileExistsError:
        return False
    except RuntimeIntentError:
        raise
    except OSError as exc:
        raise RuntimeIntentError(
            "storage_failure", f"{record_kind} could not be published: {name}"
        ) from exc
    finally:
        if descriptor is not None:
            with suppress(OSError):
                os.close(descriptor)
        if temporary is not None:
            with suppress(OSError):
                os.unlink(temporary, dir_fd=directory.descriptor)


def publish_owner_exclusive(directory: SafeDirectory, name: str, content: bytes) -> bool:
    """Publish a complete owner inode without replacing a concurrent winner."""

    return _publish_exclusive(
        directory,
        name,
        content,
        record_kind="owner",
    )


def publish_record_exclusive(directory: SafeDirectory, name: str, content: bytes) -> bool:
    """Publish a complete initial record without replacing a concurrent winner."""

    return _publish_exclusive(
        directory,
        name,
        content,
        record_kind="intent record",
    )


def replace_record(directory: SafeDirectory, name: str, content: bytes) -> None:
    temporary: str | None = None
    descriptor: int | None = None
    try:
        temporary, descriptor = _write_private_temporary(directory, name, content)
        os.close(descriptor)
        descriptor = None
        os.replace(
            temporary,
            name,
            src_dir_fd=directory.descriptor,
            dst_dir_fd=directory.descriptor,
        )
        temporary = None
        directory.verify()
        os.fsync(directory.descriptor)
        directory.verify()
    except RuntimeIntentError:
        raise
    except OSError as exc:
        raise RuntimeIntentError(
            "storage_failure", f"record could not be replaced: {name}"
        ) from exc
    finally:
        if descriptor is not None:
            with suppress(OSError):
                os.close(descriptor)
        if temporary is not None:
            with suppress(OSError):
                os.unlink(temporary, dir_fd=directory.descriptor)
