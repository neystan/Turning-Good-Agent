# Turning-Good-Agent Phase 4 Skills 机制 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 建立本地 skills 扫描、列表、加载和注入机制，为后续自动创建 skill 和 agent 自进化打基础。

**Architecture:** Skills 作为 context 能力，不直接等同于 tools。`skills/` 目录保存 `SKILL.md` 和可选资源，Runtime 在 BUILD 阶段按需加载技能内容注入上下文。

**Tech Stack:** Python 3.11+、Markdown 文件、JSON metadata、pathlib。

---

## Scope

本阶段实现：

- `scan_skills`
- `list_skills`
- `load_skill`
- skill 格式校验
- context 注入已加载 skill

本阶段不实现：

- 自动生成 skill
- skill marketplace
- skill 权限沙箱
- skill 脚本自动执行

## Target File Map

Create: `Turning-Good-Agent/skills/types.py`

定义 `SkillManifest`、`LoadedSkill`。

Create: `Turning-Good-Agent/skills/manager.py`

实现扫描、列表、加载。

Create: `Turning-Good-Agent/skills/validator.py`

校验 skill 目录和 `SKILL.md`。

Modify: `Turning-Good-Agent/config/settings.py`

增加 `skills_dir` 和默认启用技能列表。

Modify: `Turning-Good-Agent/context/builder.py`

把加载后的 skill 内容注入 system context。

## Skill Directory Format

```text
skills/
  writing-helper/
    SKILL.md
    assets/
    scripts/
```

`SKILL.md` 最小格式：

```markdown
---
name: writing-helper
description: 帮助用户整理写作任务。
---

# Instructions

当用户需要整理文章结构时使用。
```

## Task 1: Scan Skills

- [ ] **Step 1: 遍历 skills 目录**

规则：

- 只扫描一级子目录
- 子目录必须包含 `SKILL.md`
- 忽略隐藏目录

- [ ] **Step 2: 解析 frontmatter**

必须字段：

- `name`
- `description`

## Task 2: List Skills

- [ ] **Step 1: 返回技能摘要**

输出：

```python
[
    {"name": "writing-helper", "description": "帮助用户整理写作任务。"}
]
```

- [ ] **Step 2: 后续可接 slash command**

预留：

```text
/skills
/skill load <name>
```

## Task 3: Load Skill

- [ ] **Step 1: 读取 `SKILL.md` 正文**

保留 markdown 原文，交给 context builder 注入。

- [ ] **Step 2: 控制注入长度**

第一版使用配置限制：

```text
max_skill_tokens
```

## Completion Criteria

- 可以扫描本地 skills。
- 可以列出 skills。
- 可以加载指定 skill。
- 被加载 skill 能进入模型上下文。
