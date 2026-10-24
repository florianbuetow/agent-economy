"""No-follow filesystem primitives for the durable demo run store."""

from __future__ import annotations

import json
import os
import secrets
import stat
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path


class RunStoreFileError(RuntimeError):
    """A stable failure raised by the private record layer."""

    def __init__(self, reason: str, message: str) -> None:
        self.reason = reason
        super().__init__(message)


_DIRECTORY_FLAGS = os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC | os.O_NOFOLLOW
_READ_FLAGS = os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW
_WRITE_FLAGS = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC | os.O_NOFOLLOW


def _validate_absolute(path: object) -> Path:
    if (
        not isinstance(path, Path)
        or not path.is_absolute()
        or path.anchor != os.sep
        or ".." in path.parts
        or path == Path(os.sep)
    ):
        raise RunStoreFileError("unsafe_path", "store path must be absolute and canonical")
    return path


def _close_all(descriptors: list[int]) -> None:
    for descriptor in reversed(descriptors):
        with suppress(OSError):
            os.close(descriptor)


def _open_chain(path: Path, *, create: bool, private_from: int) -> list[int]:
    _validate_absolute(path)
    descriptors: list[int] = []
    try:
        descriptors.append(os.open(os.sep, _DIRECTORY_FLAGS))
        for index, component in enumerate(path.parts[1:], start=1):
            parent = descriptors[-1]
            try:
                current = os.stat(component, dir_fd=parent, follow_symlinks=False)
            except FileNotFoundError:
                if not create:
                    raise RunStoreFileError(
                        "unsafe_path", f"directory is missing: {path}"
                    ) from None
                try:
                    os.mkdir(component, mode=0o700, dir_fd=parent)
                    os.fsync(parent)
                except FileExistsError:
                    pass
                except OSError as exc:
                    raise RunStoreFileError(
                        "storage_failure", f"directory could not be persisted: {path}"
                    ) from exc
                try:
                    current = os.stat(component, dir_fd=parent, follow_symlinks=False)
                except OSError as exc:
                    raise RunStoreFileError(
                        "unsafe_path", f"directory cannot be inspected: {path}"
                    ) from exc
            except OSError as exc:
                raise RunStoreFileError(
                    "unsafe_path", f"directory cannot be inspected: {path}"
                ) from exc
            if not stat.S_ISDIR(current.st_mode) or stat.S_ISLNK(current.st_mode):
                raise RunStoreFileError("unsafe_path", f"path is not a real directory: {path}")
            if index >= private_from and stat.S_IMODE(current.st_mode) != 0o700:
                raise RunStoreFileError(
                    "unsafe_mode", f"private directory is not mode 0700: {path}"
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
                raise RunStoreFileError(
                    "unsafe_path", f"directory cannot be opened safely: {path}"
                ) from exc
            if not stat.S_ISDIR(opened.st_mode) or (opened.st_dev, opened.st_ino) != (
                latest.st_dev,
                latest.st_ino,
            ):
                os.close(descriptor)
                raise RunStoreFileError("unsafe_path", f"directory changed while opening: {path}")
            descriptors.append(descriptor)
        return descriptors
    except BaseException:
        _close_all(descriptors)
        raise


@dataclass(frozen=True, slots=True)
class SafeDirectory:
    """An open private directory with a stable public path and inode."""

    path: Path
    descriptor: int
    device: int
    inode: int
    private_from: int

    def verify(self) -> None:
        descriptors = _open_chain(self.path, create=False, private_from=self.private_from)
        try:
            current = os.fstat(descriptors[-1])
            held = os.fstat(self.descriptor)
            if (
                not stat.S_ISDIR(held.st_mode)
                or (held.st_dev, held.st_ino) != (self.device, self.inode)
                or (current.st_dev, current.st_ino) != (self.device, self.inode)
            ):
                raise RunStoreFileError("unsafe_path", f"directory identity changed: {self.path}")
            if stat.S_IMODE(held.st_mode) != 0o700 or stat.S_IMODE(current.st_mode) != 0o700:
                raise RunStoreFileError(
                    "unsafe_mode", f"private directory mode changed: {self.path}"
                )
        except OSError as exc:
            raise RunStoreFileError(
                "unsafe_path", f"directory cannot be revalidated: {self.path}"
            ) from exc
        finally:
            _close_all(descriptors)

    def close(self) -> None:
        try:
            os.close(self.descriptor)
        except OSError as exc:
            raise RunStoreFileError(
                "storage_failure", f"directory descriptor could not be closed: {self.path}"
            ) from exc


def open_private_directory(path: Path, *, create: bool, private_root: Path) -> SafeDirectory:
    """Open a directory while rejecting symlinked ancestors and unsafe private modes."""

    _validate_absolute(path)
    _validate_absolute(private_root)
    try:
        private_from = path.parts.index(private_root.name, len(private_root.parent.parts))
    except ValueError as exc:
        raise RunStoreFileError("unsafe_path", "private directory escaped its root") from exc
    if path.parts[: len(private_root.parts)] != private_root.parts:
        raise RunStoreFileError("unsafe_path", "private directory escaped its root")
    descriptors = _open_chain(path, create=create, private_from=private_from)
    descriptor = descriptors.pop()
    _close_all(descriptors)
    opened = os.fstat(descriptor)
    return SafeDirectory(path, descriptor, opened.st_dev, opened.st_ino, private_from)


def list_names(directory: SafeDirectory) -> tuple[str, ...]:
    directory.verify()
    try:
        with os.scandir(directory.descriptor) as entries:
            return tuple(sorted(entry.name for entry in entries))
    except OSError as exc:
        raise RunStoreFileError("unsafe_path", "directory cannot be scanned safely") from exc


def _record_stat(directory: SafeDirectory, name: str) -> os.stat_result | None:
    directory.verify()
    try:
        record = os.stat(name, dir_fd=directory.descriptor, follow_symlinks=False)
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise RunStoreFileError("unsafe_path", f"record cannot be inspected: {name}") from exc
    if not stat.S_ISREG(record.st_mode) or stat.S_ISLNK(record.st_mode) or record.st_nlink != 1:
        raise RunStoreFileError("unsafe_path", f"record is not a single-link regular file: {name}")
    if stat.S_IMODE(record.st_mode) != 0o600:
        raise RunStoreFileError("unsafe_mode", f"record is not mode 0600: {name}")
    return record


def validate_regular_file(directory: SafeDirectory, name: str, *, expected_mode: int) -> None:
    """Validate a non-record file's kind, link count, mode, and stable inode."""

    directory.verify()
    descriptor: int | None = None
    try:
        before = os.stat(name, dir_fd=directory.descriptor, follow_symlinks=False)
        descriptor = os.open(name, _READ_FLAGS, dir_fd=directory.descriptor)
        opened = os.fstat(descriptor)
        current = os.stat(name, dir_fd=directory.descriptor, follow_symlinks=False)
        if (
            not stat.S_ISREG(before.st_mode)
            or stat.S_ISLNK(before.st_mode)
            or before.st_nlink != 1
            or opened.st_nlink != 1
            or current.st_nlink != 1
            or (opened.st_dev, opened.st_ino) != (before.st_dev, before.st_ino)
            or (current.st_dev, current.st_ino) != (before.st_dev, before.st_ino)
        ):
            raise RunStoreFileError("unsafe_path", f"file is not a stable regular file: {name}")
        if (
            stat.S_IMODE(before.st_mode) != expected_mode
            or stat.S_IMODE(opened.st_mode) != expected_mode
            or stat.S_IMODE(current.st_mode) != expected_mode
        ):
            raise RunStoreFileError("unsafe_mode", f"file mode is unsafe: {name}")
        directory.verify()
    except RunStoreFileError:
        raise
    except OSError as exc:
        raise RunStoreFileError("unsafe_path", f"file cannot be validated safely: {name}") from exc
    finally:
        if descriptor is not None:
            with suppress(OSError):
                os.close(descriptor)


def read_record(directory: SafeDirectory, name: str, *, max_record_bytes: int) -> bytes | None:
    """Read one stable private record, enforcing its byte limit before decoding."""

    before = _record_stat(directory, name)
    if before is None:
        return None
    descriptor: int | None = None
    try:
        descriptor = os.open(name, _READ_FLAGS, dir_fd=directory.descriptor)
        opened = os.fstat(descriptor)
        latest = os.stat(name, dir_fd=directory.descriptor, follow_symlinks=False)
        if (
            not stat.S_ISREG(opened.st_mode)
            or opened.st_nlink != 1
            or latest.st_nlink != 1
            or (opened.st_dev, opened.st_ino) != (before.st_dev, before.st_ino)
            or (latest.st_dev, latest.st_ino) != (before.st_dev, before.st_ino)
        ):
            raise RunStoreFileError("unsafe_path", f"record changed while opening: {name}")
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
        stable = ("st_dev", "st_ino", "st_size", "st_mtime_ns", "st_ctime_ns", "st_nlink")
        if any(getattr(after, field) != getattr(opened, field) for field in stable) or any(
            getattr(current, field) != getattr(after, field) for field in stable
        ):
            raise RunStoreFileError("unsafe_path", f"record changed while reading: {name}")
        if len(content) > max_record_bytes:
            raise RunStoreFileError("record_too_large", f"record exceeds configured limit: {name}")
        directory.verify()
        return content
    except RunStoreFileError:
        raise
    except OSError as exc:
        raise RunStoreFileError("unsafe_path", f"record cannot be read safely: {name}") from exc
    finally:
        if descriptor is not None:
            with suppress(OSError):
                os.close(descriptor)


def decode_record(content: bytes, *, name: str) -> object:
    try:
        return json.loads(content.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RunStoreFileError("corrupt_record", f"record is not JSON: {name}") from exc


def _write_all(descriptor: int, content: bytes) -> None:
    offset = 0
    while offset < len(content):
        written = os.write(descriptor, content[offset:])
        if written <= 0:
            raise OSError("short write")
        offset += written


def _stage(directory: SafeDirectory, name: str, content: bytes) -> tuple[str, int]:
    directory.verify()
    temporary = f".{name}.{secrets.token_hex(12)}.tmp"
    descriptor = os.open(temporary, _WRITE_FLAGS, 0o600, dir_fd=directory.descriptor)
    try:
        os.fchmod(descriptor, 0o600)
        _write_all(descriptor, content)
        os.fsync(descriptor)
        return temporary, descriptor
    except BaseException as primary_error:
        with suppress(OSError):
            os.close(descriptor)
        with suppress(OSError):
            os.unlink(temporary, dir_fd=directory.descriptor)
        raise primary_error


def publish_exclusive(directory: SafeDirectory, name: str, content: bytes) -> bool:
    """Durably publish a new private record without replacing a winner."""

    temporary: str | None = None
    descriptor: int | None = None
    try:
        temporary, descriptor = _stage(directory, name, content)
        os.close(descriptor)
        descriptor = None
        published = True
        try:
            os.link(
                temporary,
                name,
                src_dir_fd=directory.descriptor,
                dst_dir_fd=directory.descriptor,
                follow_symlinks=False,
            )
        except FileExistsError:
            published = False
        os.unlink(temporary, dir_fd=directory.descriptor)
        temporary = None
        directory.verify()
        os.fsync(directory.descriptor)
        directory.verify()
        return published
    except RunStoreFileError:
        raise
    except OSError as exc:
        raise RunStoreFileError(
            "storage_failure", f"record could not be published: {name}"
        ) from exc
    finally:
        if descriptor is not None:
            with suppress(OSError):
                os.close(descriptor)
        if temporary is not None:
            with suppress(OSError):
                os.unlink(temporary, dir_fd=directory.descriptor)


def replace_record(directory: SafeDirectory, name: str, content: bytes) -> None:
    """Durably replace one admitted private record."""

    if _record_stat(directory, name) is None:
        raise RunStoreFileError("corrupt_record", f"record is missing: {name}")
    temporary: str | None = None
    descriptor: int | None = None
    try:
        temporary, descriptor = _stage(directory, name, content)
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
    except RunStoreFileError:
        raise
    except OSError as exc:
        raise RunStoreFileError("storage_failure", f"record could not be replaced: {name}") from exc
    finally:
        if descriptor is not None:
            with suppress(OSError):
                os.close(descriptor)
        if temporary is not None:
            with suppress(OSError):
                os.unlink(temporary, dir_fd=directory.descriptor)


def fsync_directory(directory: SafeDirectory) -> None:
    """Persist the current directory entries after revalidating its identity."""

    try:
        directory.verify()
        os.fsync(directory.descriptor)
        directory.verify()
    except RunStoreFileError:
        raise
    except OSError as exc:
        raise RunStoreFileError(
            "storage_failure", f"directory could not be persisted: {directory.path}"
        ) from exc


def rename_private_child(
    parent: SafeDirectory,
    child: SafeDirectory,
    destination: str,
) -> None:
    """Atomically expose one complete private child directory and persist its name."""

    try:
        parent.verify()
        child.verify()
        os.rename(
            child.path.name,
            destination,
            src_dir_fd=parent.descriptor,
            dst_dir_fd=parent.descriptor,
        )
        held = os.fstat(child.descriptor)
        published = os.stat(destination, dir_fd=parent.descriptor, follow_symlinks=False)
        if (
            not stat.S_ISDIR(held.st_mode)
            or not stat.S_ISDIR(published.st_mode)
            or (held.st_dev, held.st_ino) != (published.st_dev, published.st_ino)
        ):
            raise RunStoreFileError(
                "unsafe_path", f"published directory identity changed: {destination}"
            )
        fsync_directory(parent)
    except RunStoreFileError:
        raise
    except OSError as exc:
        raise RunStoreFileError(
            "storage_failure", f"private directory could not be published: {destination}"
        ) from exc


def remove_private_child(
    parent: SafeDirectory,
    child: SafeDirectory,
    *,
    names: tuple[str, ...],
) -> None:
    """Remove one validated unpublished staging directory and persist the removal."""

    try:
        parent.verify()
        child.verify()
        for name in names:
            os.unlink(name, dir_fd=child.descriptor)
        fsync_directory(child)
        held = os.fstat(child.descriptor)
        current = os.stat(child.path.name, dir_fd=parent.descriptor, follow_symlinks=False)
        if (
            not stat.S_ISDIR(held.st_mode)
            or not stat.S_ISDIR(current.st_mode)
            or (held.st_dev, held.st_ino) != (current.st_dev, current.st_ino)
        ):
            raise RunStoreFileError(
                "unsafe_path", f"staging directory identity changed: {child.path}"
            )
        os.rmdir(child.path.name, dir_fd=parent.descriptor)
        fsync_directory(parent)
        child.close()
    except RunStoreFileError as exc:
        try:
            child.close()
        except RunStoreFileError as cleanup_error:
            exc.add_note(f"Staging-directory close also failed: {cleanup_error}")
        raise
    except OSError as exc:
        failure = RunStoreFileError(
            "storage_failure", f"private staging directory could not be removed: {child.path}"
        )
        try:
            child.close()
        except RunStoreFileError as cleanup_error:
            failure.add_note(f"Staging-directory close also failed: {cleanup_error}")
        raise failure from exc
