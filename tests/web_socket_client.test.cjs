const assert = require("node:assert/strict");
const test = require("node:test");

const sockets = [];

class FakeSocket {
  static CONNECTING = 0;
  static OPEN = 1;

  constructor(url) {
    this.url = url;
    this.readyState = FakeSocket.CONNECTING;
    sockets.push(this);
  }

  close() {
    this.readyState = 3;
    this.onclose?.();
  }

  send(message) {
    this.sent = [...(this.sent || []), JSON.parse(message)];
  }
}

global.WebSocket = FakeSocket;
global.window = {
  location: { protocol: "http:", host: "agent.test" },
  setTimeout,
  clearTimeout,
};

const { SessionSocketClient } = require("/tmp/tga-web-socket/state/socket_client.js");

test("网络恢复时替换可能半失效的 WebSocket", () => {
  const states = [];
  const client = new SessionSocketClient({ onEvent() {}, onConnectionChange: (state) => states.push(state) });
  client.connect();
  const first = sockets.at(-1);

  client.reconnect();

  assert.equal(first.readyState, 3);
  assert.equal(sockets.length, 2);
  assert.equal(sockets.at(-1).url, "ws://agent.test/ws");
  assert.equal(states.at(-1), "reconnecting");
});

test("WebSocket 错误即使未额外触发关闭事件也会继续重连", () => {
  const originalTimeout = window.setTimeout;
  window.setTimeout = (callback) => {
    callback();
    return 1;
  };
  const states = [];
  const count = sockets.length;
  const client = new SessionSocketClient({ onEvent() {}, onConnectionChange: (state) => states.push(state) });
  client.connect();
  sockets.at(-1).onerror();
  window.setTimeout = originalTimeout;

  assert.equal(sockets.length, count + 2);
  assert.equal(states.at(-1), "reconnecting");
});

test("切换会话不重置已消费事件游标", () => {
  const client = new SessionSocketClient({ onEvent() {}, onConnectionChange() {} });
  client.connect();
  const socket = sockets.at(-1);
  socket.readyState = FakeSocket.OPEN;
  socket.onopen();
  client.setActiveSession("session-1");
  socket.onmessage({ data: JSON.stringify({ event_id: 9, session_id: "session-1", request_id: "turn-1", type: "task.running", payload: {} }) });
  client.setActiveSession("session-1", true);

  assert.equal(socket.sent.at(-1).after_event_id, 9);
});

test("切换后收到旧会话迟到事件时不推进旧会话游标", () => {
  const client = new SessionSocketClient({ onEvent() {}, onConnectionChange() {} });
  client.connect();
  const socket = sockets.at(-1);
  socket.readyState = FakeSocket.OPEN;
  socket.onopen();
  client.setActiveSession("session-a");
  socket.onmessage({ data: JSON.stringify({ event_id: 14, session_id: "session-a", request_id: "turn-a", type: "task.completed", payload: {} }) });
  client.setActiveSession("session-b");
  socket.onmessage({ data: JSON.stringify({ event_id: 21, session_id: "session-a", request_id: "turn-a", type: "response.completed", payload: { content: "完成" } }) });
  client.setActiveSession("session-a");

  assert.equal(socket.sent.at(-1).after_event_id, 14);
});
