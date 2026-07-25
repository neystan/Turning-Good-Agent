/** 串行化历史加载，防止旧会话慢响应覆盖当前会话。 */
export class SessionHistoryLoader {
  private controller: AbortController | null = null;
  private version = 0;

  /** 取消上一请求并只返回最新一次加载结果。 */
  async load<T>(sessionId: string, fetcher: (signal: AbortSignal) => Promise<T>): Promise<T | null> {
    void sessionId;
    this.controller?.abort();
    const controller = new AbortController();
    const version = ++this.version;
    this.controller = controller;
    try {
      const result = await fetcher(controller.signal);
      return version === this.version ? result : null;
    } catch (error) {
      if (controller.signal.aborted) return null;
      throw error;
    } finally {
      if (version === this.version) this.controller = null;
    }
  }

  /** 主动取消仍在等待的历史请求。 */
  cancel(): void {
    this.version += 1;
    this.controller?.abort();
    this.controller = null;
  }
}
