# Web Workbench Redesign Design

状态：已实施，待运行环境补充 Playwright 截图验证。

## 目标

将 Phase 6 Web 工作台从“状态面板式聊天页”重构为对话优先的本机 Agent 工作台。保留现有 FastAPI、REST、WebSocket、Session 和 Runtime 协议；本次不改 Agent 业务规则、持久化格式或 MCP 生命周期。

## 视觉方向

提供完整深色与浅色主题，并将最后一次选择保存到浏览器本地。首次打开默认深色。两套主题共享同一套语义 token，不以大面积绿色或多种状态色做装饰。

| 语义 | 颜色 |
| --- | --- |
| 页面基底 | `#0f1115` |
| 工作面 | `#171a21` |
| 悬停/选中 | `#222734` |
| 主操作与连接 | `#6ea8fe` |
| 等待审批 | `#e7b45b` |
| 失败/停止 | `#e06c75` |
| 主文字/次要文字 | `#eef1f6` / `#9aa4b5` |

深色主题参考中性近黑背景、低对比深灰工作面、细边框与蓝色发送按钮；浅色主题参考柔和近白输入工作面和低对比阴影。输入区使用“输入在上、工具栏在下”的一体化 Composer：左侧是附件入口与权限策略，右侧是发送或 Stop。权限策略改为带手掌图标的下拉，提供 `默认权限` 与 `完全访问权限`；后者仍不能绕过既有安全预检。

界面维持左侧会话栏、中央对话、右侧检查器，但取消页面级卡片堆叠。每个 turn 使用一条紧凑、可折叠的执行步骤；聊天消息保持无框 assistant 与轻量 user 气泡。

## 前端结构

将 `web/frontend/src/App.tsx` 拆分为以下职责明确的模块：

- `components/SessionSidebar`：会话分组、重命名、归档、恢复与删除。
- `components/ChatTimeline`：消息、发送状态、每 turn 的“思考中”执行步骤与审批条。
- `components/Composer`：发送、运行中 guidance、Stop 与全局自动批准。
- `components/SessionInspector`：概览指标、token、压缩、工具调用和 trace 明细。
- `components/NoticeRegion`：可访问的连接、请求与操作失败提示。
- `state/`：会话加载、WebSocket、turn reducer 和请求生命周期；不把状态散落在组件回调中。

## 状态与交互

1. 会话历史请求使用 `AbortController` 和递增请求版本；旧会话响应不得覆盖新会话。
2. WebSocket 使用有限指数退避重连；重连后按每 session 的 `after_event_id` 重订阅。界面明确显示连接中、已断开和已恢复。
3. 每个 `request_id` 对应一个独立 turn，消息、工具、审批、Stop 和终态只归属该 turn。运行中标题固定为 `思考中` 并使用克制的动态点；这只表示任务仍在执行，不展示或伪造模型内部推理。
4. 首条消息和后续消息均显示 `sending`、`sent` 或 `failed`；失败可重试，不将失败当作已持久化消息。
5. 运行中发送 guidance 后，聊天流立即显示这条 user 消息，并在当前 turn 步骤中显示 `已引导`。不新增 `guidance.consumed` 事件；该状态只表示补充已送入当前任务队列，未消费 guidance 在 Stop 后仍保留为草稿。
6. 步骤只显示真实事件：`已引导`、工具调用、MCP 调用、Skill 加载、等待批准、上下文压缩和终态。运行时默认展开，完成后折叠为工具数和耗时摘要；完整工具结果不进入主聊天流。
7. 归档会话保持只读，但输入区提供直接“恢复并继续”操作。
8. REST/WebSocket 操作失败写入 `aria-live` 通知；删除继续保留显式二次确认。
9. 检查器默认展示 token、上下文、压缩与工具失败摘要，再按需展开原始记录；主对话不展示完整工具结果。

## 后端边界

优先仅修改前端。若现有 WebSocket 的错误事件无法关联到 `request_id`，仅补最小字段以支持失败消息定位；不增加新的 JSONL 或第二套状态来源。

## 验证

- React TypeScript 构建。
- Python 全量回归与 WebSocket/REST 冒烟。
- Playwright 在具备 Chromium 的环境检查桌面与移动端：会话切换、重连、发送失败、按 turn 轨迹、归档恢复、检查器和深浅主题。
- 按 Web Interface Guidelines 与 frontend-design-review 复核最终 UI。
