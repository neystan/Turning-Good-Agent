# 会话管理 Spec

## 目标

定义 Turning-Good-Agent 当前 MVP 的会话目录、清理规则和生命周期约束。

## 存储结构

运行时目录：

```text
.sessions/
  <北京时间>_<session_id>/
    session.json
    messages.jsonl
    turn_traces.jsonl
    true_token_usage.jsonl
    tool_calls.jsonl
```

说明：

- `session_id` 是逻辑会话标识，用于恢复会话。
- 目录名带北京时间，也就是东八区时间，便于人工区分多次会话。
- `session.json` 中保存真实 `id`、`created_at`、`updated_at`、`summary`、`uncompacted_history`。

## 命令语义

### /new

- 只切换到新的逻辑会话。
- 不写入空会话目录。
- 只有新会话真正产生消息时，才创建目录。

### /clear

- 删除当前逻辑会话对应的整个目录。
- 如果当前会话还没有落盘，也应直接返回成功，不留下空目录。

### /history

- 读取当前逻辑会话全部消息。
- 如果当前会话尚未落盘，返回“暂无历史消息”。

## 生命周期

时间闸门：

```text
retention_days = 7
```

规则：

- 默认按 `updated_at` 判断是否过期，缺失时退回 `created_at`。
- 超过 `7` 天未更新的会话目录，在后续请求进入 Runtime 前清理。
- 该参数集中配置在 `settings.local.json -> sessions.retention_days`。

## 后续扩展

- 增加会话列表接口，支持按时间、标题、channel 检索。
- 增加归档状态，区分“删除”和“冷存储”。
- 为 Web 端会话管理面板暴露会话元数据和清理结果。
