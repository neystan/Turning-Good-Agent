from __future__ import annotations

import asyncio
import inspect
import json
import logging
import multiprocessing
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from multiprocessing.connection import Connection
from typing import Any, Protocol
from uuid import uuid4

from .registry import ChannelAccount


logger = logging.getLogger(__name__)

_PROCESS_CONTROL_KEY = "__tga_feishu_process__"
_PROCESS_READY = "ready"
_PROCESS_DISCONNECTED = "disconnected"
_PROCESS_FAILED = "failed"
_PROCESS_STOPPED = "stopped"
_PROCESS_START_TIMEOUT_SECONDS = 5.0
_PROCESS_RECONNECT_DELAYS_SECONDS = (1.0, 5.0, 30.0)


@dataclass(frozen=True, slots=True)
class FeishuConnectionState:
    """飞书单 Bot 连接的脱敏状态。"""

    connected: bool
    cardkit_enabled: bool


FeishuConnectionStateHandler = Callable[[FeishuConnectionState], Awaitable[None]]


@dataclass(slots=True)
class _CardStream:
    card_id: str
    content: str
    sequence: int = 0


FeishuEventHandler = Callable[[dict[str, object]], Awaitable[None]]


class _WsConnection(Protocol):
    async def start(self, on_event: FeishuEventHandler) -> bool: ...

    async def close(self) -> None: ...


WsConnectionFactory = Callable[[Any, str, str, str, Any], _WsConnection]


class FeishuClient(Protocol):
    """飞书 SDK 边界；业务 Transport 不直接依赖 lark-oapi。"""

    async def start_bot(
        self, account: ChannelAccount, on_event: FeishuEventHandler
    ) -> FeishuConnectionState: ...

    async def stop_bot(self, account_id: str) -> None: ...

    async def send_text(self, account: ChannelAccount, chat_id: str, content: str) -> bool: ...

    async def update_card(
        self,
        account: ChannelAccount,
        chat_id: str,
        content: str,
        *,
        completed: bool = False,
    ) -> bool: ...


class _ProcessWsConnection:
    """在独立进程中隔离 lark-oapi 的模块级事件循环。"""

    def __init__(
        self,
        account_id: str,
        app_id: str,
        app_secret: str,
        domain: str,
        *,
        on_state_change: FeishuConnectionStateHandler | None = None,
    ) -> None:
        self._account_id = account_id
        self._app_id = app_id
        self._app_secret = app_secret
        self._domain = domain
        self._process: multiprocessing.Process | None = None
        self._receiver: Connection | None = None
        self._pump_task: asyncio.Task[None] | None = None
        self._reconnect_task: asyncio.Task[None] | None = None
        self._ready_future: asyncio.Future[bool] | None = None
        self._on_event: FeishuEventHandler | None = None
        self._on_state_change = on_state_change
        self._closing = False
        self._ready = False
        self._ever_ready = False
        self._cardkit_enabled = False
        self._disconnected_reported = False
        self._exit_status: str | None = None

    async def start(self, on_event: FeishuEventHandler) -> bool:
        if self._process is not None or self._reconnect_task is not None:
            return False
        self._on_event = on_event
        self._closing = False
        self._ready = False
        self._exit_status = None
        try:
            if await self._start_process(on_event):
                return True
            await _close_connection_quietly(self)
            return False
        except asyncio.CancelledError:
            await _close_connection_quietly(self)
            raise

    def set_state_change_handler(self, handler: FeishuConnectionStateHandler) -> None:
        self._on_state_change = handler

    def set_cardkit_enabled(self, enabled: bool) -> None:
        self._cardkit_enabled = enabled

    async def close(self) -> None:
        self._closing = True
        try:
            await self._cancel_reconnect()
            await self._stop_current_process()
        finally:
            if self._process is None and self._receiver is None and self._pump_task is None:
                self._ready_future = None
                self._on_event = None
                self._ready = False
                self._ever_ready = False
                self._cardkit_enabled = False
                self._disconnected_reported = False
                self._exit_status = None
                self._closing = False

    async def _start_process(self, on_event: FeishuEventHandler) -> bool:
        receiver: Connection | None = None
        sender: Connection | None = None
        process: multiprocessing.Process | None = None
        try:
            context = multiprocessing.get_context("spawn")
            receiver, sender = context.Pipe(duplex=False)
            process = context.Process(
                target=_run_lark_ws_process,
                args=(self._app_id, self._app_secret, self._domain, sender),
                daemon=True,
                name=f"tga-feishu-{self._account_id}",
            )
            process.start()
        except Exception:
            if receiver is not None:
                receiver.close()
            if sender is not None:
                sender.close()
            return False
        sender.close()
        self._process = process
        self._receiver = receiver
        ready_future = asyncio.get_running_loop().create_future()
        self._ready_future = ready_future
        self._pump_task = asyncio.create_task(
            self._pump_events(process, receiver, on_event, ready_future),
            name=f"feishu-events-{self._account_id}",
        )
        try:
            return await asyncio.wait_for(
                asyncio.shield(ready_future),
                timeout=_PROCESS_START_TIMEOUT_SECONDS,
            )
        except TimeoutError:
            logger.warning("Feishu Bot process readiness timed out")
            return False

    async def _cancel_reconnect(self) -> None:
        reconnect_task = self._reconnect_task
        if reconnect_task is None or reconnect_task is asyncio.current_task():
            return
        reconnect_task.cancel()
        await asyncio.gather(reconnect_task, return_exceptions=True)
        if self._reconnect_task is reconnect_task:
            self._reconnect_task = None

    async def _stop_current_process(self) -> None:
        process = self._process
        if process is not None:
            if process.is_alive():
                process.terminate()
            await asyncio.to_thread(process.join, 5)
            if process.is_alive():
                process.kill()
                await asyncio.to_thread(process.join, 5)
            process.close()
            if self._process is process:
                self._process = None
        pump_task = self._pump_task
        if pump_task is not None and pump_task is not asyncio.current_task():
            try:
                await asyncio.wait_for(pump_task, timeout=1)
            except TimeoutError:
                pump_task.cancel()
                await asyncio.gather(pump_task, return_exceptions=True)
            if self._pump_task is pump_task:
                self._pump_task = None
        receiver = self._receiver
        if receiver is not None:
            receiver.close()
            if self._receiver is receiver:
                self._receiver = None
        self._ready_future = None
        self._ready = False

    async def _pump_events(
        self,
        process: multiprocessing.Process,
        receiver: Connection,
        on_event: FeishuEventHandler,
        ready_future: asyncio.Future[bool],
    ) -> None:
        try:
            while True:
                if self._process is not process:
                    return
                ready = await asyncio.to_thread(receiver.poll, 0.1)
                if ready:
                    payload = receiver.recv()
                    if isinstance(payload, dict):
                        status = payload.get(_PROCESS_CONTROL_KEY)
                        if isinstance(status, str):
                            self._exit_status = status
                            if status == _PROCESS_READY:
                                was_ready = self._ever_ready
                                self._ready = True
                                self._ever_ready = True
                                if not ready_future.done():
                                    ready_future.set_result(True)
                                if was_ready:
                                    self._disconnected_reported = False
                                    await self._report_state(
                                        FeishuConnectionState(True, self._cardkit_enabled)
                                    )
                            elif status == _PROCESS_DISCONNECTED:
                                if not self._closing and self._ready:
                                    self._ready = False
                                    if not self._disconnected_reported:
                                        self._disconnected_reported = True
                                        await self._report_state(
                                            FeishuConnectionState(False, False)
                                        )
                            elif status in {_PROCESS_FAILED, _PROCESS_STOPPED}:
                                if not ready_future.done():
                                    ready_future.set_result(False)
                                await self._handle_process_exit(process, status)
                                return
                            continue
                        await on_event(payload)
                    continue
                if not process.is_alive():
                    if not ready_future.done():
                        ready_future.set_result(False)
                    await self._handle_process_exit(process, None)
                    return
        except (EOFError, OSError):
            if not ready_future.done():
                ready_future.set_result(False)
            await self._handle_process_exit(process, None)
        finally:
            if self._pump_task is asyncio.current_task():
                self._pump_task = None

    async def _handle_process_exit(
        self,
        process: multiprocessing.Process,
        status: str | None,
    ) -> None:
        if self._process is not process:
            return
        was_ready = self._ready
        self._ready = False
        if self._closing:
            return
        if not was_ready:
            logger.warning("Feishu Bot process exited before readiness")
            return
        if not self._disconnected_reported:
            self._disconnected_reported = True
            await self._report_state(FeishuConnectionState(False, False))
        if status is not None:
            logger.warning("Feishu Bot process exited with status %s", status)
        else:
            logger.warning("Feishu Bot process exited after readiness")
        self._schedule_reconnect()

    def _schedule_reconnect(self) -> None:
        reconnect_task = self._reconnect_task
        if self._closing or (reconnect_task is not None and not reconnect_task.done()):
            return
        self._reconnect_task = asyncio.create_task(
            self._reconnect(),
            name=f"feishu-reconnect-{self._account_id}",
        )

    async def _reconnect(self) -> None:
        current_task = asyncio.current_task()
        try:
            for delay_seconds in _PROCESS_RECONNECT_DELAYS_SECONDS:
                await asyncio.sleep(delay_seconds)
                if self._closing:
                    return
                try:
                    await self._stop_current_process()
                except asyncio.CancelledError:
                    raise
                except Exception:
                    logger.warning("Feishu Bot process reconnect cleanup failed")
                    continue
                if self._closing:
                    return
                on_event = self._on_event
                if on_event is None:
                    return
                self._exit_status = None
                try:
                    started = await self._start_process(on_event)
                except asyncio.CancelledError:
                    raise
                except Exception:
                    logger.warning("Feishu Bot process reconnect startup failed")
                    started = False
                if started:
                    reconnect_after_return = not self._ready and not self._closing
                    if self._reconnect_task is current_task:
                        self._reconnect_task = None
                    if reconnect_after_return:
                        self._schedule_reconnect()
                    return
                try:
                    await self._stop_current_process()
                except asyncio.CancelledError:
                    raise
                except Exception:
                    logger.warning("Feishu Bot process reconnect cleanup failed")
            if not _PROCESS_RECONNECT_DELAYS_SECONDS and not self._closing:
                try:
                    await self._stop_current_process()
                except asyncio.CancelledError:
                    raise
                except Exception:
                    logger.warning("Feishu Bot process reconnect cleanup failed")
            if not self._closing:
                await self._report_failed_state()
        finally:
            if self._reconnect_task is current_task:
                self._reconnect_task = None

    async def _report_failed_state(self) -> None:
        if self._disconnected_reported:
            return
        self._disconnected_reported = True
        await self._report_state(FeishuConnectionState(False, False))

    async def _report_state(self, state: FeishuConnectionState) -> None:
        on_state_change = self._on_state_change
        if on_state_change is None:
            return
        try:
            await on_state_change(state)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.warning("Feishu Bot connection state callback failed")


def _process_connection_factory(
    _lark: Any,
    app_id: str,
    app_secret: str,
    domain: str,
    dispatcher: Any,
) -> _WsConnection:
    del dispatcher
    return _ProcessWsConnection("bot", app_id, app_secret, domain)


class LarkFeishuClient:
    """官方 SDK 的窄适配层。

    SDK 版本差异被限制在本模块。若 SDK 不可用，单个 Bot 只会进入 failed/
    disconnected 状态，不影响同一 Gateway 的其他 Channel。
    """

    def __init__(
        self,
        *,
        sdk_loader: Callable[[], tuple[Any, Any, Any, Any]] | None = None,
        connection_factory: WsConnectionFactory | None = None,
    ) -> None:
        self._sdk_loader = sdk_loader or _load_lark_sdk
        self._connection_factory = connection_factory or _process_connection_factory
        self._connections: dict[str, _WsConnection] = {}
        self._rest_clients: dict[str, Any] = {}
        self._handlers: dict[str, FeishuEventHandler] = {}
        self._state_handlers: dict[str, Callable[[FeishuConnectionState], Any]] = {}
        self._card_streams: dict[tuple[str, str], _CardStream] = {}

    def set_state_handler(
        self,
        account_id: str,
        handler: Callable[[FeishuConnectionState], Any],
    ) -> None:
        self._state_handlers[account_id] = handler

    async def start_bot(
        self, account: ChannelAccount, on_event: FeishuEventHandler
    ) -> FeishuConnectionState:
        candidate_connection: _WsConnection | None = None
        try:
            app_id, app_secret, domain = _credentials(account)
            _disable_lark_sdk_logging()
            lark, auth_v3, _im_v1, cardkit_v1 = self._sdk_loader()
            rest_client = (
                lark.Client.builder()
                .app_id(app_id)
                .app_secret(app_secret)
                .domain(domain)
                .build()
            )
            if not await _validate_credentials(rest_client, auth_v3, app_id, app_secret):
                return FeishuConnectionState(False, False)
            loop = asyncio.get_running_loop()

            def receive(event: object) -> None:
                payload = _normalize_event(event)
                if payload is None:
                    return
                try:
                    callback_loop = asyncio.get_running_loop()
                except RuntimeError:
                    callback_loop = None
                if callback_loop is loop:
                    loop.create_task(on_event(payload))
                    return
                future = asyncio.run_coroutine_threadsafe(on_event(payload), loop)
                future.add_done_callback(_consume_event_result)

            dispatcher = (
                lark.EventDispatcherHandler.builder("", "")
                .register_p2_im_message_receive_v1(receive)
                .build()
            )
            candidate_connection = self._connection_factory(
                lark,
                app_id,
                app_secret,
                domain,
                dispatcher,
            )
            cardkit_enabled = _cardkit_supported(rest_client, cardkit_v1)
            set_cardkit_enabled = getattr(candidate_connection, "set_cardkit_enabled", None)
            if callable(set_cardkit_enabled):
                set_cardkit_enabled(cardkit_enabled)
            state_handler = self._state_handlers.get(account.id)
            set_state_change_handler = getattr(
                candidate_connection,
                "set_state_change_handler",
                None,
            )
            if state_handler is not None and callable(set_state_change_handler):
                async def notify_state_change(
                    state: FeishuConnectionState,
                    *,
                    account_id: str = account.id,
                    connection: _WsConnection = candidate_connection,
                ) -> None:
                    if self._connections.get(account_id) is not connection:
                        return
                    result = state_handler(state)
                    if inspect.isawaitable(result):
                        await result

                set_state_change_handler(notify_state_change)
            if not await candidate_connection.start(on_event):
                await _close_connection_quietly(candidate_connection)
                logger.warning("Feishu SDK connection startup failed")
                return FeishuConnectionState(False, False)
            previous_connection = self._connections.get(account.id)
            if previous_connection is not None and previous_connection is not candidate_connection:
                try:
                    await previous_connection.close()
                except asyncio.CancelledError:
                    await _close_connection_quietly(candidate_connection)
                    raise
                except Exception:
                    await _close_connection_quietly(candidate_connection)
                    logger.warning("Feishu SDK connection replacement failed")
                    return FeishuConnectionState(False, False)
            self._connections[account.id] = candidate_connection
            self._rest_clients[account.id] = rest_client
            self._handlers[account.id] = on_event
            for key in tuple(self._card_streams):
                if key[0] == account.id:
                    self._card_streams.pop(key, None)
            return FeishuConnectionState(True, cardkit_enabled)
        except asyncio.CancelledError:
            if candidate_connection is not None:
                await _close_connection_quietly(candidate_connection)
            raise
        except Exception:
            if candidate_connection is not None:
                await _close_connection_quietly(candidate_connection)
            logger.warning("Feishu SDK connection startup failed")
            return FeishuConnectionState(False, False)

    async def stop_bot(self, account_id: str) -> None:
        connection = self._connections.get(account_id)
        if connection is not None:
            await connection.close()
        self._connections.pop(account_id, None)
        self._rest_clients.pop(account_id, None)
        self._handlers.pop(account_id, None)
        self._state_handlers.pop(account_id, None)
        for key in tuple(self._card_streams):
            if key[0] == account_id:
                self._card_streams.pop(key, None)

    async def send_text(self, account: ChannelAccount, chat_id: str, content: str) -> bool:
        rest_client = self._rest_clients.get(account.id)
        if rest_client is None:
            return False
        try:
            _lark, _auth_v3, im_v1, _cardkit_v1 = self._sdk_loader()
            body = (
                im_v1.CreateMessageRequestBody.builder()
                .receive_id(chat_id)
                .msg_type("text")
                .content(json.dumps({"text": content}, ensure_ascii=False))
                .build()
            )
            request = (
                im_v1.CreateMessageRequest.builder()
                .receive_id_type("chat_id")
                .request_body(body)
                .build()
            )
            response = await rest_client.im.v1.message.acreate(request)
        except Exception:
            return False
        return _response_succeeded(response)

    async def update_card(
        self,
        account: ChannelAccount,
        chat_id: str,
        content: str,
        *,
        completed: bool = False,
    ) -> bool:
        rest_client = self._rest_clients.get(account.id)
        if rest_client is None:
            return False
        try:
            _lark, _auth_v3, im_v1, cardkit_v1 = self._sdk_loader()
            if not _cardkit_supported(rest_client, cardkit_v1):
                return False
            key = (account.id, chat_id)
            stream = self._card_streams.get(key)
            if stream is None:
                return await self._create_card_stream(
                    rest_client,
                    im_v1,
                    cardkit_v1,
                    account.id,
                    chat_id,
                    content,
                    completed=completed,
                )
            next_content = content if completed else stream.content + content
            accepted = await self._update_card_stream(
                rest_client,
                cardkit_v1,
                stream,
                next_content,
                completed=completed,
            )
        except Exception:
            return False
        if not accepted:
            self._card_streams.pop(key, None)
            return False
        if completed:
            self._card_streams.pop(key, None)
        else:
            stream.content = next_content
        return True

    async def _create_card_stream(
        self,
        rest_client: Any,
        im_v1: Any,
        cardkit_v1: Any,
        account_id: str,
        chat_id: str,
        content: str,
        *,
        completed: bool,
    ) -> bool:
        card_body = (
            cardkit_v1.CreateCardRequestBody.builder()
            .type("card_json")
            .data(json.dumps(_card_spec(content, completed=completed), ensure_ascii=False))
            .build()
        )
        card_request = cardkit_v1.CreateCardRequest.builder().request_body(card_body).build()
        card_response = await rest_client.cardkit.v1.card.acreate(card_request)
        card_id = _field(_field(card_response, "data"), "card_id")
        if not _response_succeeded(card_response) or not isinstance(card_id, str) or not card_id:
            return False
        message_body = (
            im_v1.CreateMessageRequestBody.builder()
            .receive_id(chat_id)
            .msg_type("interactive")
            .content(
                json.dumps(
                    {"type": "card", "data": {"card_id": card_id}},
                    ensure_ascii=False,
                )
            )
            .build()
        )
        message_request = (
            im_v1.CreateMessageRequest.builder()
            .receive_id_type("chat_id")
            .request_body(message_body)
            .build()
        )
        message_response = await rest_client.im.v1.message.acreate(message_request)
        if not _response_succeeded(message_response):
            return False
        if not completed:
            self._card_streams[(account_id, chat_id)] = _CardStream(card_id, content)
        return True

    @staticmethod
    async def _update_card_stream(
        rest_client: Any,
        cardkit_v1: Any,
        stream: _CardStream,
        content: str,
        *,
        completed: bool,
    ) -> bool:
        card = (
            cardkit_v1.Card.builder()
            .type("card_json")
            .data(json.dumps(_card_spec(content, completed=completed), ensure_ascii=False))
            .build()
        )
        body = (
            cardkit_v1.UpdateCardRequestBody.builder()
            .card(card)
            .uuid(str(uuid4()))
            .sequence(stream.sequence + 1)
            .build()
        )
        request = (
            cardkit_v1.UpdateCardRequest.builder()
            .card_id(stream.card_id)
            .request_body(body)
            .build()
        )
        response = await rest_client.cardkit.v1.card.aupdate(request)
        if not _response_succeeded(response):
            return False
        stream.sequence += 1
        return True


def _run_lark_ws_process(
    app_id: str,
    app_secret: str,
    domain: str,
    sender: Connection,
) -> None:
    _disable_lark_sdk_logging()
    try:
        lark, _auth_v3, _im_v1, _cardkit_v1 = _load_lark_sdk()

        def receive(event: object) -> None:
            payload = _normalize_event(event)
            if payload is None:
                return
            try:
                sender.send(payload)
            except (BrokenPipeError, EOFError, OSError):
                return

        dispatcher = (
            lark.EventDispatcherHandler.builder("", "")
            .register_p2_im_message_receive_v1(receive)
            .build()
        )
        client = lark.ws.Client(
            app_id,
            app_secret,
            log_level=lark.LogLevel.CRITICAL,
            event_handler=dispatcher,
            domain=domain,
        )
        try:
            if hasattr(client, "on_reconnecting"):
                client.on_reconnecting = lambda: _send_process_status(
                    sender, _PROCESS_DISCONNECTED
                )
            if hasattr(client, "on_reconnected"):
                client.on_reconnected = lambda: _send_process_status(
                    sender, _PROCESS_READY
                )
        except Exception:
            pass
        ready_sent = False
        original_connect = getattr(client, "_connect", None)
        if callable(original_connect):
            async def connect_and_report_ready() -> object:
                nonlocal ready_sent
                result = await original_connect()
                if getattr(client, "_conn", None) is None:
                    raise RuntimeError("Feishu WebSocket connection was not established")
                _send_process_status(sender, _PROCESS_READY)
                ready_sent = True
                return result

            client._connect = connect_and_report_ready
        client.start()
        if not ready_sent:
            _send_process_status(sender, _PROCESS_READY)
        _send_process_status(sender, _PROCESS_STOPPED)
    except BaseException:
        _send_process_status(sender, _PROCESS_FAILED)
        return
    finally:
        sender.close()


def _send_process_status(sender: Connection, status: str) -> None:
    try:
        sender.send({_PROCESS_CONTROL_KEY: status})
    except (BrokenPipeError, EOFError, OSError):
        return


def _disable_lark_sdk_logging() -> None:
    logger = logging.getLogger("Lark")
    logger.disabled = True
    logger.propagate = False


async def _close_connection_quietly(connection: _WsConnection) -> None:
    try:
        await connection.close()
    except asyncio.CancelledError:
        raise
    except Exception:
        return


def _load_lark_sdk() -> tuple[Any, Any, Any, Any]:
    try:
        import lark_oapi
        import lark_oapi.api.auth.v3 as auth_v3
        import lark_oapi.api.cardkit.v1 as cardkit_v1
        import lark_oapi.api.im.v1 as im_v1
    except ImportError as exc:  # pragma: no cover - exercised only in real deployment
        raise RuntimeError("飞书 Transport 需要 lark-oapi") from exc
    return lark_oapi, auth_v3, im_v1, cardkit_v1


def _credentials(account: ChannelAccount) -> tuple[str, str, str]:
    app_id = account.private.get("app_id")
    app_secret = account.private.get("app_secret")
    domain = account.private.get("domain")
    if not isinstance(app_id, str) or not app_id:
        raise RuntimeError("飞书凭据未配置")
    if not isinstance(app_secret, str) or not app_secret:
        raise RuntimeError("飞书凭据未配置")
    if not isinstance(domain, str) or not domain:
        raise RuntimeError("飞书域未配置")
    if not domain.startswith(("https://", "http://")):
        domain = f"https://{domain}"
    return app_id, app_secret, domain.rstrip("/")


async def _validate_credentials(
    rest_client: Any,
    auth_v3: Any,
    app_id: str,
    app_secret: str,
) -> bool:
    body = (
        auth_v3.InternalTenantAccessTokenRequestBody.builder()
        .app_id(app_id)
        .app_secret(app_secret)
        .build()
    )
    request = (
        auth_v3.InternalTenantAccessTokenRequest.builder()
        .request_body(body)
        .build()
    )
    response = await rest_client.auth.v3.tenant_access_token.ainternal(request)
    return _response_succeeded(response)


def _response_succeeded(response: object) -> bool:
    success = getattr(response, "success", None)
    if callable(success):
        return bool(success())
    return getattr(response, "code", None) in (None, 0)


def _cardkit_supported(rest_client: object, cardkit_v1: object) -> bool:
    try:
        create = rest_client.cardkit.v1.card.acreate  # type: ignore[attr-defined]
        update = rest_client.cardkit.v1.card.aupdate  # type: ignore[attr-defined]
        request = cardkit_v1.CreateCardRequest  # type: ignore[attr-defined]
        request_body = cardkit_v1.CreateCardRequestBody  # type: ignore[attr-defined]
        card = cardkit_v1.Card  # type: ignore[attr-defined]
        update_request = cardkit_v1.UpdateCardRequest  # type: ignore[attr-defined]
        update_request_body = cardkit_v1.UpdateCardRequestBody  # type: ignore[attr-defined]
    except AttributeError:
        return False
    return all(
        callable(callback)
        for callback in (
            create,
            update,
            getattr(request, "builder", None),
            getattr(request_body, "builder", None),
            getattr(card, "builder", None),
            getattr(update_request, "builder", None),
            getattr(update_request_body, "builder", None),
        )
    )


def _card_spec(content: str, *, completed: bool) -> dict[str, object]:
    return {
        "schema": "2.0",
        "config": {"streaming_mode": not completed},
        "body": {"elements": [{"tag": "markdown", "content": content}]},
    }


def _normalize_event(payload: object) -> dict[str, object] | None:
    event = _field(payload, "event")
    header = _field(payload, "header")
    sender = _field(event, "sender")
    sender_id = _field(sender, "sender_id")
    message = _field(event, "message")
    open_id = _field(sender_id, "open_id")
    message_id = _field(message, "message_id")
    event_id = _field(header, "event_id") or message_id
    chat_id = _field(message, "chat_id")
    chat_type = _field(message, "chat_type")
    message_type = _field(message, "message_type")
    content = _field(message, "content")
    if not all(isinstance(value, str) and value for value in (
        open_id,
        str(event_id) if event_id is not None else "",
        str(message_id) if message_id is not None else "",
        chat_id,
        chat_type,
        message_type,
    )):
        return None
    normalized_message: dict[str, object] = {
        "chat_id": chat_id,
        "chat_type": chat_type,
        "message_type": message_type,
        "content": content if isinstance(content, str) else "",
    }
    for key in ("root_id", "parent_id", "thread_id"):
        value = _field(message, key)
        if isinstance(value, str) and value:
            normalized_message[key] = value
    mention_count = _field(message, "mention_count")
    if isinstance(mention_count, int) and mention_count > 0:
        normalized_message["mention_count"] = mention_count
    else:
        mentions = _field(message, "mentions")
        if isinstance(mentions, list):
            count = sum(1 for item in mentions if isinstance(item, dict))
            if count:
                normalized_message["mention_count"] = count
    for key in ("create_time", "create_time_ms"):
        value = _field(message, key)
        if isinstance(value, (str, int, float)) and value != "":
            normalized_message[key] = value
    return {
        "event_id": str(event_id),
        "message_id": str(message_id),
        "sender": {"open_id": open_id},
        "message": normalized_message,
    }


def _field(value: object, name: str) -> object | None:
    if isinstance(value, dict):
        return value.get(name)
    return getattr(value, name, None)


def _consume_event_result(future: object) -> None:
    try:
        result = getattr(future, "result", None)
        if callable(result):
            result()
    except Exception:
        return
