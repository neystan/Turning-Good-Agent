# Phase 2.5 Basic Tools Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add TGA's basic built-in tools for files, grep, restricted shell execution, web fetch/search, and weather lookup.

**Architecture:** Keep the existing `BaseTool`/`ToolRegistry`/`ToolLoader` flow. Add focused tool modules plus shared path, security, and exec-session helpers; tool results continue to flow through `AgentLoop` working messages and existing tool-call observability.

**Tech Stack:** Python 3.11+, asyncio, urllib from stdlib, local JSON Schema validation already present in `tools/base.py`.

---

### Task 1: Security and Path Helpers

**Files:**
- Create: `Turning-Good-Agent/tools/path_utils.py`
- Create: `Turning-Good-Agent/tools/security.py`
- Test: `tests/test_basic_tools.py`

- [ ] Write failing tests for workspace path containment, blocked device paths, session-state write rejection, dangerous command rejection, and output truncation.
- [ ] Implement path resolution and security helpers with concise Chinese comments.
- [ ] Run `pytest tests/test_basic_tools.py -q`.

### Task 2: Filesystem Tools

**Files:**
- Create: `Turning-Good-Agent/tools/filesystem_tools.py`
- Test: `tests/test_basic_tools.py`

- [ ] Write failing tests for `list_dir`, `find_file`, `read_file`, `write_file`, `edit_file`, and `grep`.
- [ ] Implement the six tools using `security.py` and `path_utils.py`.
- [ ] Verify `ToolLoader` discovers the tools.
- [ ] Run `pytest tests/test_basic_tools.py tests/test_tools_loop.py -q`.

### Task 3: Shell Tools

**Files:**
- Create: `Turning-Good-Agent/tools/exec_sessions.py`
- Create: `Turning-Good-Agent/tools/shell_tools.py`
- Test: `tests/test_basic_tools.py`

- [ ] Write failing tests for one-shot `exec`, dangerous command blocking, timeout handling, long-running session polling, and `write_stdin` termination.
- [ ] Implement the exec session manager and shell tools.
- [ ] Run `pytest tests/test_basic_tools.py -q`.

### Task 4: Web and Weather Tools

**Files:**
- Create: `Turning-Good-Agent/tools/web_tools.py`
- Create: `Turning-Good-Agent/tools/info_tools.py`
- Test: `tests/test_basic_tools.py`

- [ ] Write failing tests for URL scheme rejection, webpage text extraction, search output format, and weather parameter validation.
- [ ] Implement `web_fetch`, `web_search`, and `weather`.
- [ ] Run `pytest tests/test_basic_tools.py -q`.

### Task 5: Docs and Verification

**Files:**
- Modify: `README.md`
- Modify: `docs/PROJECT_ARCHITECTURE.md`
- Modify: `docs/TURNING_GOOD_AGENT_SPEC.md`
- Modify: `docs/phases/2026-06-25-phase-2-5-basic-tools.md`

- [ ] Update docs to match the actual tool modules and boundaries.
- [ ] Run `pytest -q`.
- [ ] Run `printf '/exit\n' | python -m Turning-Good-Agent chat`.
- [ ] Run `git diff --check`.
