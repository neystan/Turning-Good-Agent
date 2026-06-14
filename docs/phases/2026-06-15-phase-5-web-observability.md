# Turning-Good-Agent Phase 5 Web 可观测面板 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 提供一个本地 Web 面板，用于查看 session、messages、summary、trace、token usage 和 COMPACT 统计。

**Architecture:** 第一版只读取本地 JSON/JSONL 文件，不引入数据库。后端提供只读 API，前端是面向调试的工作台，不做营销页。

**Tech Stack:** Python 3.11+、FastAPI 或标准库 HTTP server、HTML/CSS/JS、JSON/JSONL。

---

## Scope

本阶段实现：

- session 列表
- 单 session 消息查看
- summary 查看
- token usage 图表或表格
- trace 时间线
- COMPACT 统计展示

本阶段不实现：

- 用户认证
- 远程部署
- 编辑 session
- 多用户权限

## Target File Map

Create: `Turning-Good-Agent/web/server.py`

启动本地观测服务，提供 API 和静态页面。

Create: `Turning-Good-Agent/web/static/index.html`

单页 UI。

Create: `Turning-Good-Agent/web/static/styles.css`

简洁工作台样式。

Create: `Turning-Good-Agent/web/static/app.js`

读取 API 并渲染数据。

Modify: `Turning-Good-Agent/cli.py`

增加：

```bash
python -m Turning-Good-Agent dashboard
```

## API Design

```text
GET /api/sessions
GET /api/sessions/{session_id}
GET /api/sessions/{session_id}/messages
GET /api/sessions/{session_id}/traces
GET /api/sessions/{session_id}/token-usage
```

## UI Layout

页面分三栏：

- 左侧：session 列表
- 中间：消息与 summary
- 右侧：token、trace、compact 统计

关键要求：

- 不是 landing page
- 不使用装饰性 hero
- 面向调试和扫描
- 表格、时间线、状态标签要密集但清楚

## Completion Criteria

- 可以启动本地 dashboard。
- 能看到所有 session。
- 能查看单 session 完整消息。
- 能查看 token usage 递增记录。
- 能查看 COMPACT 是否发生和对应 5 个字段。
