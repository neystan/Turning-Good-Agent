import type { ProactiveNotice, ProactiveSnapshot, ProactiveState } from "../proactive_types";

type Handlers = {
  onSnapshot: (snapshot: ProactiveSnapshot) => void;
  onNotice: (notice: ProactiveNotice) => void;
  onConnection: (state: ProactiveState["connection"]) => void;
};

export class ProactiveSocket {
  private socket: WebSocket | null = null;
  private retryTimer: number | null = null;
  private closed = false;
  constructor(private readonly handlers: Handlers) {}

  connect(): void {
    this.closed = false;
    this.open();
  }

  reconnect(): void {
    if (this.closed) return;
    if (this.retryTimer !== null) {
      window.clearTimeout(this.retryTimer);
      this.retryTimer = null;
    }
    if (this.socket) {
      this.socket.close();
      return;
    }
    this.open();
  }

  close(): void {
    this.closed = true;
    if (this.retryTimer !== null) window.clearTimeout(this.retryTimer);
    this.retryTimer = null;
    this.socket?.close();
    this.socket = null;
  }

  private open(): void {
    if (this.closed) return;
    this.handlers.onConnection("connecting");
    const protocol = window.location.protocol === "https:" ? "wss:" : "ws:";
    const socket = new WebSocket(protocol + "//" + window.location.host + "/ws/proactive");
    this.socket = socket;
    socket.onopen = () => this.handlers.onConnection("connected");
    socket.onmessage = (event) => {
      try {
        const payload = JSON.parse(event.data) as ProactiveSnapshot | ProactiveNotice;
        if (payload.type === "notice") {
          this.handlers.onNotice(payload);
          return;
        }
        this.handlers.onSnapshot(payload);
      } catch {
        // 协议错误不应影响同一连接后续的最新完整快照。
      }
    };
    socket.onerror = () => socket.close();
    socket.onclose = () => {
      if (this.socket === socket) this.socket = null;
      if (this.closed) return;
      this.handlers.onConnection("reconnecting");
      this.retryTimer = window.setTimeout(() => {
        this.retryTimer = null;
        this.open();
      }, 1200);
    };
  }
}
