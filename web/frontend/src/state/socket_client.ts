import type { ConnectionState, TaskEvent } from "../types";

type SocketAction = Record<string, unknown> & { type: string };

type SocketClientOptions = {
  onEvent: (event: TaskEvent & { type: string }) => void;
  onConnectionChange: (state: ConnectionState) => void;
};

const RETRY_DELAYS = [250, 500, 1_000, 2_000, 4_000, 5_000] as const;

/** 管理 WebSocket 重连、事件游标和当前会话订阅。 */
export class SessionSocketClient {
  private socket: WebSocket | null = null;
  private retryTimer: number | null = null;
  private retryIndex = 0;
  private closed = false;
  private activeSessionId: string | null = null;
  private readonly lastEventIds: Record<string, number> = {};

  /** 初始化本机 Web Host 的连接客户端。 */
  constructor(private readonly options: SocketClientOptions) {}

  /** 开始连接；重复调用不会创建并行连接。 */
  connect(): void {
    if (this.closed || this.socket?.readyState === WebSocket.OPEN || this.socket?.readyState === WebSocket.CONNECTING) return;
    this.options.onConnectionChange(this.retryIndex ? "reconnecting" : "connecting");
    const protocol = window.location.protocol === "https:" ? "wss" : "ws";
    const socket = new WebSocket(`${protocol}://${window.location.host}/ws/web`);
    this.socket = socket;
    socket.onopen = () => this.handleOpen(socket);
    socket.onmessage = (event) => this.handleMessage(event);
    socket.onclose = () => this.handleClose(socket);
    socket.onerror = () => this.handleSocketError(socket);
  }

  /** 切换当前订阅会话，并保留已经消费的事件游标。 */
  setActiveSession(sessionId: string | null): void {
    this.activeSessionId = sessionId;
    if (!this.socket) {
      this.connect();
      return;
    }
    if (this.socket.readyState !== WebSocket.OPEN) {
      this.reconnect();
      return;
    }
    this.subscribeActiveSession();
  }

  /** 载入刷新前已消费的事件游标，避免订阅时重复回放。 */
  setLastEventId(sessionId: string, eventId: number): void {
    if (eventId > (this.lastEventIds[sessionId] || 0)) this.lastEventIds[sessionId] = eventId;
  }

  /** 返回指定会话当前已消费的最大事件编号。 */
  lastEventId(sessionId: string): number {
    return this.lastEventIds[sessionId] || 0;
  }

  /** 网络恢复时替换可能未触发关闭事件的旧连接。 */
  reconnect(): void {
    if (this.closed) return;
    if (this.retryTimer !== null) window.clearTimeout(this.retryTimer);
    this.retryTimer = null;
    const socket = this.socket;
    this.socket = null;
    this.retryIndex = 1;
    socket?.close();
    this.connect();
  }

  /** 发送前端动作，调用方据返回值决定是否标记失败。 */
  send(action: SocketAction): boolean {
    if (this.socket?.readyState !== WebSocket.OPEN) return false;
    try {
      this.socket.send(JSON.stringify(action));
      return true;
    } catch {
      return false;
    }
  }

  /** 显式关闭连接并取消尚未触发的重连。 */
  close(): void {
    this.closed = true;
    if (this.retryTimer !== null) window.clearTimeout(this.retryTimer);
    this.retryTimer = null;
    this.socket?.close();
    this.socket = null;
    this.options.onConnectionChange("disconnected");
  }

  /** 在连接成功后重置退避并恢复当前会话订阅。 */
  private handleOpen(socket: WebSocket): void {
    if (this.socket !== socket) return;
    this.retryIndex = 0;
    this.options.onConnectionChange("connected");
    this.subscribeActiveSession();
  }

  /** 仅确认当前订阅会话的事件，避免切换时丢失迟到事件。 */
  private handleMessage(message: MessageEvent<string>): void {
    const event = JSON.parse(message.data) as TaskEvent & { type: string };
    if (event.event_id && event.session_id === this.activeSessionId) this.lastEventIds[event.session_id] = event.event_id;
    this.options.onEvent(event);
  }

  /** 在非显式关闭时按有限指数退避恢复连接。 */
  private handleClose(socket: WebSocket): void {
    if (this.socket !== socket || this.closed) return;
    this.socket = null;
    this.scheduleReconnect();
  }

  /** 在错误回调未触发 close 时仍启动下一次连接尝试。 */
  private handleSocketError(socket: WebSocket): void {
    if (this.socket !== socket || this.closed) return;
    this.socket = null;
    socket.close();
    this.scheduleReconnect();
  }

  /** 以有限退避调度唯一的下一次连接尝试。 */
  private scheduleReconnect(): void {
    if (this.closed || this.retryTimer !== null) return;
    this.options.onConnectionChange("reconnecting");
    const delay = RETRY_DELAYS[Math.min(this.retryIndex, RETRY_DELAYS.length - 1)];
    this.retryIndex += 1;
    this.retryTimer = window.setTimeout(() => {
      this.retryTimer = null;
      this.connect();
    }, delay);
  }

  /** 订阅当前会话并带上已消费的最大事件编号。 */
  private subscribeActiveSession(): void {
    if (!this.activeSessionId) return;
    this.send({
      type: "session.subscribe",
      session_id: this.activeSessionId,
      after_event_id: this.lastEventIds[this.activeSessionId] || 0,
    });
  }
}
