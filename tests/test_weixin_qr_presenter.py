from __future__ import annotations

import asyncio
import base64
import importlib
import os
import sys
import threading
from pathlib import Path
from types import SimpleNamespace


weixin_module = importlib.import_module("Turning-Good-Agent.channels.weixin")
qr_module = importlib.import_module("Turning-Good-Agent.channels.weixin_qr")
LocalIlinkQrPresenter = getattr(weixin_module, "LocalIlinkQrPresenter", None)
_PNG_BYTES = b"\x89PNG\r\n\x1a\nlocal-qr"


def test_local_qr_presenter_keeps_image_temporary_until_local_viewer_closes(tmp_path: Path) -> None:
    async def run() -> None:
        assert LocalIlinkQrPresenter is not None
        started = asyncio.Event()
        presented_paths: list[Path] = []

        async def local_viewer(image_path: Path) -> None:
            presented_paths.append(image_path)
            assert image_path.read_bytes() == _PNG_BYTES
            started.set()
            await asyncio.Event().wait()

        presenter = LocalIlinkQrPresenter(
            viewer=local_viewer,
            temporary_root=tmp_path,
        )
        encoded = base64.b64encode(_PNG_BYTES).decode("ascii")

        assert await presenter.present("binding-1", f"data:image/png;base64,{encoded}") is True
        await asyncio.wait_for(started.wait(), timeout=1)

        assert len(presented_paths) == 1
        assert presented_paths[0].exists()
        assert presented_paths[0].parent.parent == tmp_path

        await presenter.close()

        assert not presented_paths[0].exists()
        assert list(tmp_path.iterdir()) == []

    asyncio.run(run())


def test_local_qr_presenter_rejects_non_image_content_without_writing_it(tmp_path: Path) -> None:
    async def run() -> None:
        assert LocalIlinkQrPresenter is not None

        async def local_viewer(_image_path: Path) -> None:
            raise AssertionError("invalid QR content must not reach a local viewer")

        presenter = LocalIlinkQrPresenter(
            viewer=local_viewer,
            temporary_root=tmp_path,
        )

        assert await presenter.present("binding-1", "not-an-image") is False

        assert list(tmp_path.iterdir()) == []
        await presenter.close()

    asyncio.run(run())


def test_local_qr_presenter_rejects_image_types_the_local_viewer_cannot_render(tmp_path: Path) -> None:
    async def run() -> None:
        assert LocalIlinkQrPresenter is not None
        presenter = LocalIlinkQrPresenter(
            viewer=lambda _image_path: asyncio.sleep(0),
            temporary_root=tmp_path,
        )
        encoded = base64.b64encode(b"\xff\xd8\xffnot-supported-by-tk").decode("ascii")

        assert await presenter.present("binding-1", encoded) is False
        assert list(tmp_path.iterdir()) == []
        await presenter.close()

    asyncio.run(run())


def test_local_qr_presenter_removes_dead_process_artifacts_on_start(tmp_path: Path) -> None:
    stale_directory = tmp_path / "tga-ilink-qr-2147483647-stale"
    stale_directory.mkdir()
    (stale_directory / "login.png").write_bytes(_PNG_BYTES)

    assert LocalIlinkQrPresenter is not None
    LocalIlinkQrPresenter(
        viewer=lambda _image_path: asyncio.sleep(0),
        temporary_root=tmp_path,
    )

    assert not stale_directory.exists()


def test_local_qr_presenter_cleans_image_when_window_start_is_cancelled(tmp_path: Path) -> None:
    async def run() -> None:
        assert LocalIlinkQrPresenter is not None
        started = asyncio.Event()

        async def blocked_window_start(_image_path: Path):
            started.set()
            await asyncio.Event().wait()

        original = qr_module._start_local_window
        qr_module._start_local_window = blocked_window_start
        try:
            presenter = LocalIlinkQrPresenter(temporary_root=tmp_path)
            encoded = base64.b64encode(_PNG_BYTES).decode("ascii")
            task = asyncio.create_task(
                presenter.present("binding-1", f"data:image/png;base64,{encoded}")
            )
            await asyncio.wait_for(started.wait(), timeout=1)
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
            else:
                raise AssertionError("cancelled presentation must propagate cancellation")
            assert list(tmp_path.iterdir()) == []
        finally:
            qr_module._start_local_window = original

    asyncio.run(run())


def test_local_qr_presenter_notifies_when_local_viewer_exits(tmp_path: Path) -> None:
    async def run() -> None:
        assert LocalIlinkQrPresenter is not None
        started = asyncio.Event()
        release = asyncio.Event()
        closed = asyncio.Event()
        closed_bindings: list[str] = []

        async def local_viewer(_image_path: Path) -> None:
            started.set()
            await release.wait()

        async def on_closed(binding_id: str) -> None:
            closed_bindings.append(binding_id)
            closed.set()

        presenter = LocalIlinkQrPresenter(
            viewer=local_viewer,
            temporary_root=tmp_path,
        )
        presenter.set_closed_callback(on_closed)
        encoded = base64.b64encode(_PNG_BYTES).decode("ascii")

        assert await presenter.present("binding-1", f"data:image/png;base64,{encoded}") is True
        await asyncio.wait_for(started.wait(), timeout=1)
        assert presenter.is_presenting("binding-1") is True
        release.set()
        await asyncio.wait_for(closed.wait(), timeout=1)

        assert closed_bindings == ["binding-1"]
        assert presenter.is_presenting("binding-1") is False
        assert list(tmp_path.iterdir()) == []
        await presenter.close()

    asyncio.run(run())


def test_local_qr_presenter_passes_candidate_generation_on_close(tmp_path: Path) -> None:
    async def run() -> None:
        assert LocalIlinkQrPresenter is not None
        started = asyncio.Event()
        release = asyncio.Event()
        closed = asyncio.Event()
        notifications: list[tuple[str, int | None]] = []

        async def local_viewer(_image_path: Path) -> None:
            started.set()
            await release.wait()

        async def on_closed(binding_id: str, generation: int | None) -> None:
            notifications.append((binding_id, generation))
            closed.set()

        presenter = LocalIlinkQrPresenter(viewer=local_viewer, temporary_root=tmp_path)
        presenter.set_closed_callback(on_closed)
        encoded = base64.b64encode(_PNG_BYTES).decode("ascii")

        assert await presenter.present(
            "binding-generation",
            f"data:image/png;base64,{encoded}",
            42,
        ) is True
        await asyncio.wait_for(started.wait(), timeout=1)
        release.set()
        await asyncio.wait_for(closed.wait(), timeout=1)
        assert notifications == [("binding-generation", 42)]
        await presenter.close()

    asyncio.run(run())


def test_parent_disconnect_watcher_observes_closed_parent_pipe() -> None:
    reader_descriptor, writer_descriptor = os.pipe()
    reader = os.fdopen(reader_descriptor, "rb", buffering=0)
    try:
        disconnected = qr_module._watch_parent_stdin(reader)
        os.close(writer_descriptor)
        writer_descriptor = -1
        assert disconnected.wait(1)
    finally:
        if writer_descriptor >= 0:
            os.close(writer_descriptor)
        reader.close()


def test_local_viewer_removes_qr_file_when_parent_pipe_disconnects(tmp_path: Path) -> None:
    directory = tmp_path / "tga-ilink-qr-12345-test"
    directory.mkdir()
    image_path = directory / "login.png"
    image_path.write_bytes(_PNG_BYTES)
    disconnected = threading.Event()
    disconnected.set()

    class Window:
        def title(self, _value: str) -> None:
            return None

        def resizable(self, _width: bool, _height: bool) -> None:
            return None

        def after(self, _milliseconds: int, callback) -> None:
            callback()

        def mainloop(self) -> None:
            return None

        def destroy(self) -> None:
            return None

    class Label:
        def __init__(self, _window: Window, **_kwargs: object) -> None:
            return None

        def pack(self, **_kwargs: object) -> None:
            return None

        def configure(self, **_kwargs: object) -> None:
            return None

    fake_tk = SimpleNamespace(Tk=Window, PhotoImage=lambda **_kwargs: object(), Label=Label)
    original_tk = sys.modules.get("tkinter")
    original_watcher = qr_module._watch_parent_stdin
    sys.modules["tkinter"] = fake_tk
    qr_module._watch_parent_stdin = lambda _stream: disconnected
    try:
        assert qr_module._display_qr_window(image_path) == 0
    finally:
        qr_module._watch_parent_stdin = original_watcher
        if original_tk is None:
            sys.modules.pop("tkinter", None)
        else:
            sys.modules["tkinter"] = original_tk

    assert not directory.exists()


def test_parent_disconnect_removes_qr_before_viewer_mainloop_returns(tmp_path: Path) -> None:
    directory = tmp_path / "tga-ilink-qr-12345-mainloop"
    directory.mkdir()
    image_path = directory / "login.png"
    image_path.write_bytes(_PNG_BYTES)
    disconnected = threading.Event()
    disconnected.set()

    class Window:
        def __init__(self) -> None:
            self.callback = None

        def title(self, _value: str) -> None:
            return None

        def resizable(self, _width: bool, _height: bool) -> None:
            return None

        def after(self, _milliseconds: int, callback) -> None:
            self.callback = callback

        def mainloop(self) -> None:
            assert self.callback is not None
            self.callback()
            assert not directory.exists()

        def destroy(self) -> None:
            return None

    class Label:
        def __init__(self, _window: Window, **_kwargs: object) -> None:
            return None

        def pack(self, **_kwargs: object) -> None:
            return None

        def configure(self, **_kwargs: object) -> None:
            return None

    fake_tk = SimpleNamespace(Tk=Window, PhotoImage=lambda **_kwargs: object(), Label=Label)
    original_tk = sys.modules.get("tkinter")
    original_watcher = qr_module._watch_parent_stdin
    sys.modules["tkinter"] = fake_tk
    qr_module._watch_parent_stdin = lambda _stream: disconnected
    try:
        assert qr_module._display_qr_window(image_path) == 0
    finally:
        qr_module._watch_parent_stdin = original_watcher
        if original_tk is None:
            sys.modules.pop("tkinter", None)
        else:
            sys.modules["tkinter"] = original_tk

    assert not directory.exists()
