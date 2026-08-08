from __future__ import annotations

import asyncio
import base64
import binascii
import inspect
import os
import shutil
import sys
import tempfile
import threading
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import BinaryIO


_MAX_QR_IMAGE_BYTES = 4 * 1024 * 1024
_IMAGE_SUFFIXES = {
    "image/gif": ".gif",
    "image/png": ".png",
}
QrViewer = Callable[[Path], Awaitable[None]]
QrClosedCallback = Callable[[str], Awaitable[None] | None]


class LocalIlinkQrPresenter:
    """在本机临时图像窗口中呈现 iLink 登录二维码。"""

    def __init__(
        self,
        *,
        viewer: QrViewer | None = None,
        temporary_root: Path | None = None,
    ) -> None:
        self._viewer = viewer
        self._temporary_root = temporary_root or Path(tempfile.gettempdir())
        _cleanup_orphaned_qr_directories(self._temporary_root)
        self._presentations: dict[str, asyncio.Task[None]] = {}
        self._on_closed: QrClosedCallback | None = None
        self._closed = False

    def set_closed_callback(self, callback: QrClosedCallback | None) -> None:
        self._on_closed = callback

    def is_presenting(self, binding_id: str) -> bool:
        task = self._presentations.get(binding_id)
        return task is not None and not task.done()

    async def present(self, binding_id: str, qr_content: str) -> bool:
        if self._closed:
            return False
        image = _decode_qr_image(qr_content)
        if image is None:
            return False
        await self.dismiss(binding_id)
        image_bytes, suffix = image
        directory: Path | None = None
        try:
            self._temporary_root.mkdir(parents=True, exist_ok=True)
            directory = Path(
                tempfile.mkdtemp(
                    prefix=f"tga-ilink-qr-{os.getpid()}-",
                    dir=str(self._temporary_root),
                )
            )
            image_path = directory / f"login{suffix}"
            image_path.write_bytes(image_bytes)
        except OSError:
            if directory is not None:
                shutil.rmtree(directory, ignore_errors=True)
            return False
        if self._viewer is None:
            try:
                process = await _start_local_window(image_path)
            except asyncio.CancelledError:
                shutil.rmtree(directory, ignore_errors=True)
                raise
            except Exception:
                shutil.rmtree(directory, ignore_errors=True)
                return False
            if process is None:
                shutil.rmtree(directory, ignore_errors=True)
                return False
            task = asyncio.create_task(
                self._wait_for_local_window(binding_id, image_path, process),
                name=f"weixin-qr-{binding_id}",
            )
        else:
            task = asyncio.create_task(
                self._present_image(binding_id, image_path),
                name=f"weixin-qr-{binding_id}",
            )
        self._presentations[binding_id] = task
        return True

    async def dismiss(self, binding_id: str) -> None:
        task = self._presentations.pop(binding_id, None)
        if task is None:
            return
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)

    async def close(self) -> None:
        self._closed = True
        await asyncio.gather(
            *(self.dismiss(binding_id) for binding_id in tuple(self._presentations)),
        )

    async def _present_image(self, binding_id: str, image_path: Path) -> None:
        notify_closed = True
        try:
            assert self._viewer is not None
            await self._viewer(image_path)
        except asyncio.CancelledError:
            notify_closed = False
            raise
        except Exception:
            return
        finally:
            self._remove_presentation(binding_id, image_path)
            if notify_closed:
                await self._notify_closed(binding_id)

    async def _wait_for_local_window(
        self,
        binding_id: str,
        image_path: Path,
        process: asyncio.subprocess.Process,
    ) -> None:
        notify_closed = True
        try:
            await process.wait()
        except asyncio.CancelledError:
            notify_closed = False
            await _terminate_process(process)
            raise
        finally:
            if process.stdin is not None:
                process.stdin.close()
            self._remove_presentation(binding_id, image_path)
            if notify_closed:
                await self._notify_closed(binding_id)

    def _remove_presentation(self, binding_id: str, image_path: Path) -> None:
        if self._presentations.get(binding_id) is asyncio.current_task():
            self._presentations.pop(binding_id, None)
        shutil.rmtree(image_path.parent, ignore_errors=True)

    async def _notify_closed(self, binding_id: str) -> None:
        callback = self._on_closed
        if callback is None:
            return
        try:
            result = callback(binding_id)
            if inspect.isawaitable(result):
                await result
        except asyncio.CancelledError:
            raise
        except Exception:
            return


def _decode_qr_image(qr_content: str) -> tuple[bytes, str] | None:
    content = qr_content.strip()
    if content.startswith("data:"):
        header, separator, payload = content.partition(",")
        if not separator or ";base64" not in header.lower():
            return None
        if _IMAGE_SUFFIXES.get(header[5:].split(";", 1)[0].lower()) is None:
            return None
    else:
        payload = content
    try:
        image_bytes = base64.b64decode("".join(payload.split()), validate=True)
    except (binascii.Error, ValueError):
        return None
    if not image_bytes or len(image_bytes) > _MAX_QR_IMAGE_BYTES:
        return None
    detected_suffix = _image_suffix(image_bytes)
    if detected_suffix is None:
        return None
    return image_bytes, detected_suffix


def _image_suffix(image_bytes: bytes) -> str | None:
    if image_bytes.startswith(b"\x89PNG\r\n\x1a\n"):
        return ".png"
    if image_bytes.startswith((b"GIF87a", b"GIF89a")):
        return ".gif"
    return None


def _cleanup_orphaned_qr_directories(temporary_root: Path) -> None:
    try:
        candidates = tuple(temporary_root.glob("tga-ilink-qr-*"))
    except OSError:
        return
    for candidate in candidates:
        process_id = _process_id_from_directory(candidate)
        if (
            process_id is None
            or process_id == os.getpid()
            or candidate.is_symlink()
            or not candidate.is_dir()
            or _process_is_running(process_id)
        ):
            continue
        shutil.rmtree(candidate, ignore_errors=True)


def _process_id_from_directory(directory: Path) -> int | None:
    prefix = "tga-ilink-qr-"
    if not directory.name.startswith(prefix):
        return None
    value = directory.name[len(prefix) :].split("-", 1)[0]
    try:
        process_id = int(value)
    except ValueError:
        return None
    return process_id if process_id > 0 else None


def _process_is_running(process_id: int) -> bool:
    try:
        os.kill(process_id, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


async def _start_local_window(image_path: Path) -> asyncio.subprocess.Process | None:
    try:
        process = await asyncio.create_subprocess_exec(
            sys.executable,
            str(Path(__file__).resolve()),
            "--display-qr",
            str(image_path),
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
    except OSError:
        return None
    if process.stdout is None or process.stdin is None:
        await _terminate_process(process)
        return None
    try:
        ready = await asyncio.wait_for(process.stdout.readline(), timeout=2)
    except asyncio.CancelledError:
        await _terminate_process(process)
        raise
    except asyncio.TimeoutError:
        await _terminate_process(process)
        return None
    if ready == b"ready\n":
        return process
    await _terminate_process(process)
    return None


async def _terminate_process(process: asyncio.subprocess.Process) -> None:
    if process.stdin is not None:
        process.stdin.close()
    if process.returncode is not None:
        return
    try:
        process.terminate()
    except ProcessLookupError:
        return
    try:
        await asyncio.wait_for(process.wait(), timeout=2)
    except asyncio.TimeoutError:
        try:
            process.kill()
        except ProcessLookupError:
            return
        await process.wait()


def _display_qr_window(image_path: Path) -> int:
    window = None
    image = None
    image_label = None

    def close_window_and_remove_artifact() -> None:
        nonlocal image
        if image_label is not None:
            try:
                image_label.configure(image="")
                image_label.image = None
            except Exception:
                pass
        image = None
        if window is not None:
            try:
                window.destroy()
            except Exception:
                pass
        shutil.rmtree(image_path.parent, ignore_errors=True)

    try:
        import tkinter as tk

        window = tk.Tk()
        window.title("WeChat iLink Login")
        window.resizable(False, False)
        image = tk.PhotoImage(file=str(image_path))
        tk.Label(window, text="Scan this QR code with WeChat").pack(padx=16, pady=(16, 8))
        image_label = tk.Label(window, image=image)
        image_label.image = image
        image_label.pack(padx=16, pady=(0, 16))
        parent_disconnected = _watch_parent_stdin(sys.stdin.buffer)

        def close_when_parent_exits() -> None:
            if parent_disconnected.is_set():
                close_window_and_remove_artifact()
                return
            window.after(250, close_when_parent_exits)

        window.after(250, close_when_parent_exits)
        print("ready", flush=True)
        window.mainloop()
    except Exception:
        return 1
    finally:
        close_window_and_remove_artifact()
    return 0


def _watch_parent_stdin(stream: BinaryIO) -> threading.Event:
    disconnected = threading.Event()

    def wait_for_parent() -> None:
        try:
            while stream.read(1):
                pass
        except OSError:
            pass
        finally:
            disconnected.set()

    threading.Thread(target=wait_for_parent, daemon=True).start()
    return disconnected


def _main(argv: list[str]) -> int:
    if len(argv) != 2 or argv[0] != "--display-qr":
        return 2
    return _display_qr_window(Path(argv[1]))


if __name__ == "__main__":
    raise SystemExit(_main(sys.argv[1:]))
