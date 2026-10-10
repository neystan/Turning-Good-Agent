# Turning-Good-Agent Phase 10 Web 附件、文档按需读取与多模态契约

> 状态：**首版实现完成，持续验收中。** 本文是 Phase 10 的当前产品、接口与验收契约；若本文与当前代码冲突，以当前代码为准，再按变更流程更新本文。
>
> Phase 10 复用 Phase 7 的单 Gateway、单 `AgentRuntime`、单 `AgentLoop`、共享 `AsyncMessageBus` 和会话隔离，复用 Phase 6 的 Web Composer、Session、Tool Calls Inspector 与双主题视觉规范，复用 Phase 8 的 IM 不扩展边界和 Phase 9 的父 Agent 最终控制权。Phase 10 首版只增加 Web Channel 的附件能力，不改造飞书、微信、主动任务或 Worker 的附件入口。

## 1. 目标与设计定位

Phase 10 解决 Web 工作面中的两个问题：

1. 用户可以安全上传常用文档，Agent 在确实需要时读取并解析其中的文本；
2. 用户可以上传图片，在当前 active LLM 支持视觉时以一次多模态请求处理；不支持时，Web 在发送前明确阻止并提示。

附件是当前用户消息的受控输入，不是第二套 Runtime、第二个 Session Store 或新的后台服务。普通聊天仍沿用：

```text
Web Composer
  -> 浏览器本地附件状态
  -> 点击发送时 REST multipart 批量上传
  -> Gateway 校验、原子落盘并返回 attachment_id
  -> /ws/web message.send 只携带 attachment_id
  -> 唯一 AgentRuntime / AgentLoop
  -> Session SAVE 边界
```

图片在当前回合构造成 Provider 的多模态 content blocks；文档不预先全文注入，而由父 Agent 通过 `read_attachment` Tool 按需读取。Multi-Agent 只接收父 Agent 已读取、受限且必要的文本证据，不直接访问原始附件。

## 2. v1 范围与明确不做的事

### 2.1 v1 实现

- Web 点击回形针选择文件、拖入文件和粘贴图片；三种入口共用相同校验与 Composer 卡片。
- 文档白名单：PDF、DOCX、XLSX、PPTX、TXT、MD、CSV、JSON。
- 图片白名单：PNG、JPEG/JPG、WebP。
- 文档前端/后端双重校验，后端以真实 MIME、文件内容和扩展名一致性为权威。
- 父 Agent 专用 `read_attachment` 只读 Tool，采用 nanobot 式按需读取。
- 图片与用户文字在一次 Agent Runtime 请求中交给 active LLM。
- 附件 metadata 绑定到用户消息；Tool Result、Assistant 最终回答和正常聊天历史继续使用现有 Session 持久化机制。
- Composer 和已发送用户消息展示附件卡片、处理状态、移除、失败和图片大图预览。
- 附件仅绑定同一 `principal_id + channel + conversation_id`，使用不可枚举的 opaque `attachment_id`。

### 2.2 v1 不实现

- 不把文档全文自动预解析并注入每个回合；不把原始文件路径伪装成用户正文。
- 不把原始二进制、base64、本地绝对路径或远程 URL 写入 Session、聊天正文、摘要或 reasoning。
- 不引入 OCR、扫描 PDF 识别、文档内嵌图片识别或单独的隐藏图片摘要模型。
- 不实现 Office 工作表、幻灯片、段落的精细索引；不增加独立 `inspect` Tool。
- 不把 `read_attachment` 注册进 Slash 命令目录、IM Tool Catalog、主动任务 Tool Profile 或 Phase 9 Worker Tool Profile。
- 不改变飞书、微信、CLI、主动能力、审批规则和 Multi-Agent 拓扑。
- 不复制截图中的“深度思考”“智能搜索”“继续生成”等非附件功能。
- 文档卡片首版不提供在线文档预览或下载；图片只提供受鉴权的大图预览。
- 不设置“未发送附件 24 小时清理”后台任务。

### 2.3 当前实现状态

- Web Composer 已支持附件选择、拖拽和粘贴入口；发送后的文档和图片 metadata 会立即进入聊天时间线，无需刷新页面。
- 用户消息保留原始文字与附件卡片 metadata；前端通过 optimistic message 事件即时呈现，WebSocket 发送失败时保留失败状态，不伪造新的 Agent 回合。
- 文档仍由父 Agent 通过 `read_attachment` 按需解析和读取，不把全文预先注入聊天或 Runtime；图片在发送前根据 active Provider 的视觉能力进行校验。
- 当前真实 Docker 验收使用 `127.0.0.1:8001`。当前 active Provider 报告 `supports_vision=false`，因此本轮只确认“不支持视觉时提示并阻止发送”，未声称真实视觉 Provider 已验收。

## 3. nanobot 参考与 TGA 适配

### 3.1 可复用的方法

nanobot 的 `utils/document.py`、`attachment_ingress.py`、Web media gateway 和 ContextBuilder 提供以下已验证模式：

- MIME 白名单与扩展名双重校验；
- 文件、批次和文本输出上限；
- PDF 页数、内容流和 Office ZIP 安全限制；
- 解析器以有界结果返回，并用明确错误或截断标记结束；
- 专用媒体目录、批量失败回滚、路径归属校验和签名/鉴权预览；
- 图片在 Provider 请求中使用 `image_url` content block；
- 文本文件按 `offset/limit` 分段读取，PDF 按 `pages` 读取。

### 3.2 不直接复制的部分

nanobot 的附件字段是跨 Channel 通用的，并允许多个 Adapter 下载媒体；其 `read_file` 通过本地路径读取附件。TGA 只复用其行为和解析安全边界，不暴露本地路径，不让 Web 上传直接进入工作区，也不在本阶段改造 IM Adapter。TGA 的外部契约始终使用 `attachment_id`，由 Gateway 做主体、Channel 和会话归属校验。

## 4. 上传与文件政策

### 4.1 文档限制

| 项目 | v1 规则 |
| --- | --- |
| 格式 | PDF、DOCX、XLSX、PPTX、TXT、MD、CSV、JSON |
| 单文件大小 | 不超过 50 MB |
| 单条消息文件数 | 文档最多 5 个 |
| 单条消息总大小 | 文档和图片合计不超过 100 MB |
| 文本抽取 | 单文件最多返回 200,000 字符；实际 Tool Result 仍受 nanobot 式约 128,000 字符读取上限约束 |
| PDF | 单次读取最多 20 页，可用页码范围继续读取 |
| Office ZIP | 检查成员数量、解压总大小、单成员大小、加密文档和异常归档 |

解析结果超过上限时必须返回明确截断标记。解析失败、格式不支持、文件超限或无可提取文本时返回脱敏、可读错误，不让异常击穿 AgentLoop。

### 4.2 图片限制

| 项目 | v1 规则 |
| --- | --- |
| 格式 | PNG、JPEG/JPG、WebP |
| 单张大小 | 不超过 10 MB |
| 单条消息张数 | 最多 5 张 |
| 校验 | MIME、扩展名和真实文件签名必须一致 |
| 传输 | 模型请求时转换为受控 image content block；Session 不保存 base64 |

GIF、SVG、动图视频、视频、音频和远程 URL 首版不支持。粘贴入口只接受真实图片文件，不接受 HTML 片段、富文本样式或远程资源。

### 4.3 发送事务

1. 选择、拖拽或粘贴时，文件只存在浏览器内存/本地 Composer 状态，不提前写入服务端。
2. 点击发送后，前端以 REST multipart 提交整批附件；后端逐项校验并在同一批次内原子落盘。
3. 任一文件失败、总量超限或归属校验失败时，删除本批次已经落盘的文件，WebSocket 消息不发送。
4. 批量成功后返回 `attachment_id` 和 metadata；前端再通过 `/ws/web` 发送 `message.send`，只携带这些 ID。
5. WebSocket 发送失败时，附件保持“发送失败”状态，不创建新的 Agent 回合；后续重试必须使用同一会话的合法附件引用或重新提交整批文件，具体由实现保持原子性。

## 5. REST、WebSocket 与归属契约

### 5.1 REST

首版提供以下 Web 控制接口，具体认证复用现有 Web Session/Control 面：

- `POST /api/control/sessions/{session_id}/attachments`
  - `multipart/form-data`；整批上传；成功返回 attachment metadata 列表。
  - 只能绑定当前认证主体和指定 Web 会话；不接受客户端指定 `principal_id`、物理路径或存储 ID。
- `GET /api/control/sessions/{session_id}/attachments/{attachment_id}/preview`
  - 仅对图片开放；返回受鉴权、不可枚举、私有缓存的图片内容。
  - 文档返回“不支持预览”，不提供在线下载。

附件 ID 必须使用不可枚举的随机 opaque 值。服务端每次读取、预览和消息发送都校验完整的 `principal_id + channel + conversation_id` 归属；同一主体的其他会话也不能复用附件 ID。

### 5.2 WebSocket

`/ws/web` 的普通消息新增可选字段：

```json
{
  "type": "message.send",
  "content": "请分析这些内容",
  "attachment_ids": ["att_xxx", "att_yyy"]
}
```

`attachment_ids` 只引用当前会话已成功上传的附件。附件本身不再通过 WebSocket 发送，不允许 data URL、base64、本地路径或远程 URL。

## 6. `read_attachment` Tool

### 6.1 可见性与权限

`read_attachment` 是 Web 父 Agent 的 Runtime-owned、只读 Tool：

- 仅来源为 `/ws/web` 的父 Agent 回合可见；
- 不进入 Slash Catalog、普通设置审批列表、IM Catalog、主动任务 Tool Profile 或 Worker Profile；
- 不创建第二个 Runtime、Session、Bus 或后台执行器；
- Tool Executor 仍执行既有安全预检和二次检查；
- Multi-Agent Worker 不直接访问原始附件，父 Agent 只能把已读取的有限文本证据放入 task brief 或结果上下文。

### 6.2 Schema

```json
{
  "type": "object",
  "properties": {
    "attachment_id": {"type": "string"},
    "offset": {"type": "integer", "minimum": 1, "default": 1},
    "limit": {"type": "integer", "minimum": 1, "maximum": 2000, "default": 2000},
    "pages": {"type": "string", "description": "PDF 页码范围，例如 1-5"},
    "force": {"type": "boolean", "default": false}
  },
  "required": ["attachment_id"]
}
```

### 6.3 读取行为

- TXT、MD、CSV、JSON 按行读取，返回 `LINE_NUM|CONTENT`，使用 `offset/limit` 分页；
- PDF 使用 `pages`，单次最多 20 页；页范围之外的内容通过继续读取获得；
- DOCX、XLSX、PPTX 使用 nanobot 同类安全解析器抽取文本，单次结果最多约 128,000 字符；首版不提供 Office 内部范围索引；
- `force=true` 绕过同一附件、同一范围且内容未变时的读取去重；
- 结果必须注明读取范围、总量、是否继续以及是否截断；
- 文件不存在、附件归属不符、已损坏、加密、不支持或没有可提取文本时，返回结构化、脱敏的 Tool Error。

读取结果是普通 Tool Result：进入当前 Runtime 上下文，并在现有 Session SAVE 边界持久化为文本消息，metadata 带 `attachment_id`、文件名、读取范围和截断状态。聊天主流不新增隐藏文件全文气泡；Tool Calls Inspector 展示调用与结果摘要。

## 7. 图片多模态契约

### 7.1 能力声明

后端 active LLM/Model Catalog 显式声明 `supports_vision: true | false`。前端不得按模型名称猜测；未配置或无法判断时按 `false` 处理。Gateway 在发送前再次做权威校验。

### 7.2 请求与保存

1. Web 选择图片后读取后端能力；不支持或未知时，图片卡片标红并禁用发送。
2. 支持视觉时，Gateway 从已归属的附件 ID 读取图片 bytes 和真实 MIME，构造 Provider 需要的 image content blocks。
3. 图片与用户文字在同一次 Agent Runtime 请求中发送；不额外调用隐藏 OCR/视觉预处理模型。
4. Provider 返回的正常 Assistant 文本照常保存为 Assistant 消息；不强制模型输出隐藏的“规范化图片摘要”，不改写用户正文。
5. 用户消息保存原始文字（可以为空）、图片 metadata 和附件卡片信息；Session 不保存 base64 或本地路径。
6. 后续历史重放以附件 metadata 和会话所属原始文件为依据；若当前 Provider 不支持视觉，不重新提交图片，并保留已有的文字历史和明确能力错误，不做静默伪造。

## 8. Session、Tool 与数据生命周期

### 8.1 用户消息 metadata

每个附件绑定到对应用户消息，字段固定为：

```json
{
  "attachment_id": "att_xxx",
  "source": "document | image",
  "filename": "简历.pdf",
  "mime_type": "application/pdf",
  "size_bytes": 293120,
  "processing": "pending | ready | reading | failed | unsupported",
  "text_chars": 12345,
  "truncated": false,
  "error": null
}
```

metadata 不包含路径、base64 或原始二进制。`text_chars` 和 `truncated` 只表示实际已读取并保存的文本；图片不要求 `text_chars`，图片理解由正常 Assistant 文本体现。

### 8.2 生命周期

- 未发送附件只在浏览器本地存在，不创建服务端文件；
- 发送成功后附件与所属 Session 同生命周期；
- 删除 Session 时，删除该 Session 的附件及派生缩略图；
- 不设置未发送附件的 24 小时后台清理任务；
- 断线重连恢复同一 Session 后，已发送附件仍可被卡片展示和 `read_attachment` 读取；
- 任一会话不得读取其他主体、Channel 或 conversation 的附件。

## 9. Web 交互与视觉契约

### 9.1 Composer

- 回形针入口保留 TGA 现有按钮体系；点击打开文件选择器。
- 拖拽文件进入 Composer 时显示可接受区域；离开或取消不产生服务端文件。
- 粘贴只提取真实图片文件；富文本、HTML、远程 URL 被忽略并给出可读提示。
- 待发送卡片显示类型图标/缩略图、文件名、大小、状态和移除按钮。
- 发送按钮在正文为空但存在合法附件时可用；正文与附件都为空时不可用。
- 视觉能力不支持时仅在选择图片后显示提示；移除图片后文字聊天恢复可发送。

### 9.2 已发送消息

- 用户消息右侧保留附件卡片和原始文字，沿用现有 TGA 气泡布局。
- 图片卡片支持点击大图预览；预览接口必须鉴权并禁止公共缓存。
- 文档卡片显示类型、文件名和大小；首版不提供在线文档预览或下载。
- `read_attachment` 的调用、范围、耗时和结果摘要进入现有 Tool Calls Inspector，不增加新的 reasoning 或隐藏思维链表面。

### 9.3 视觉语言

继续使用 Phase 6 的背景分层、留白、轻阴影、圆角滑块、双主题和避免细描边堆叠的规则。复现参考截图中的附件卡片与状态，不复制其非附件按钮、页面结构或产品品牌。

## 10. 错误、安全与兼容边界

- 前端限制只改善体验，后端 MIME、大小、数量、归属和能力检查是唯一权威；任何绕过前端的请求都必须安全失败。
- 批量上传采用原子语义；失败不留下部分文件、不创建 Session 消息、不启动 Runtime 回合。
- 文件名只作为展示 metadata，落盘名使用 opaque ID；路径穿越、伪造 MIME、压缩炸弹、加密 Office 和超大 PDF 必须拒绝。
- 媒体预览使用鉴权、路径归属检查、`nosniff`、私有缓存；不暴露绝对路径。
- 文档读取错误返回用户可读、脱敏信息；不把异常堆栈、密钥、Token 或内部路径放进聊天、Tool Result 或日志正文。
- Phase 8 IM 继续保持 `attachments/media` 的现有空行为；Phase 7 主动任务不读取 Web 附件；Phase 9 Worker 不取得原始附件 Tool。
- 不新增控制面 WebSocket 状态事件；附件上传、读取和模型能力状态通过现有 Web REST/普通消息事件表达。

## 11. 验收与当前结果

### 11.1 验收要求

Phase 10 的完整验收至少包括以下内容：

1. 后端测试覆盖 MIME/扩展名伪造、大小/数量/总量、批量回滚、路径归属、跨会话 ID、PDF/Office/文本解析、截断、页码/行号分页、加密 ZIP、损坏文件和无文本文件。
2. Runtime 测试覆盖 `read_attachment` 只在 Web 父 Agent 可见、Worker/IM/主动任务不可见、Tool Result 进入 Session、原始路径/base64 不持久化、图片单次多模态请求和不支持视觉时阻止发送。
3. Web Playwright 覆盖选择/拖拽/粘贴、附件-only 发送、Composer 状态、失败回滚、图片能力提示、模型切换重校验、已发送卡片、图片预览、会话重连和双主题。
4. 回归必须继续通过 Phase 6–9 的现有 Python、Playwright、`pnpm run build`、`compileall` 和 `git diff --check`；本阶段不以历史验收数字替代当前执行结果。
5. 验收报告必须记录真实页面、真实 active Provider（至少一个支持视觉和一个不支持/未知能力的配置）以及文档分段读取证据。

### 11.2 本轮已记录结果

- PowerShell：`$env:TGA_WEB_URL='http://127.0.0.1:8001'; npx playwright test tests/workbench_attachments.spec.ts --reporter=line`：`5 passed`。
- PowerShell：`$env:TGA_WEB_URL='http://127.0.0.1:8001'; npx playwright test --reporter=line`：`75 passed / 12 skipped`（87 tests）。
- `pnpm run build`：通过；仅有 Vite 配置加载和 chunk size advisory。
- `.venv\Scripts\python -m compileall Turning-Good-Agent tests`：通过。
- 真实 Docker 浏览器交互验证：上传并发送文档后，聊天时间线在不刷新页面的情况下显示附件卡片；页面 navigation count 为 `1`，附件文件名和大小可见。
- 当前真实 active Provider 的视觉能力为 `supports_vision=false`；不支持视觉的提示和发送阻止已覆盖，支持视觉的真实 Provider 尚未在本轮验收。
- 本轮未重新执行 Phase 6–9 全量 Python 回归；后续发布验收仍需补齐并记录该部分结果。

## 12. 后续阶段边界

- IM 附件、语音/视频、OCR、扫描文档、Office 精细索引和安全下载另立阶段契约。
- 如果未来需要让 Multi-Agent 参与附件任务，父 Agent 先完成附件归属、读取和脱敏，再把有限文本证据交给 Worker；不得把附件路径或附件 Tool 直接加入 Worker Profile。
- 任何扩大附件 Channel 范围、改变 Session 持久化或新增媒体后台生命周期的提案，都必须先更新本文并重新经过需求确认。
