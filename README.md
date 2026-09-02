# Taskord (Discord Project Manager MCP Server)

Taskord is a **Model Context Protocol (MCP)** server (`DiscordProjectManager`) built with Python and FastMCP. It enables AI coding assistants and LLMs to manage project planning, brainstormed ideas, roadmap lifecycles, and live task progress tracking seamlessly inside Discord servers.

---

## 📋 Table of Contents
- [Overview](#overview)
- [Architecture & Tech Stack](#architecture--tech-stack)
- [Configuration & Environment Variables](#configuration--environment-variables)
- [Core Features & MCP Tools](#core-features--mcp-tools)
  - [`set_guild_id` & `get_server_config`](#1-set_guild_id--get_server_config)
  - [`save_idea`](#2-save_idea)
  - [`log_pull_request_activity`](#3-log_pull_request_activity)
  - [`create_project_workspace`](#4-create_project_workspace)
  - [`create_roadmap`](#5-create_roadmap)
  - [`replace_roadmap`](#6-replace_roadmap)
  - [`update_roadmap_task`](#7-update_roadmap_task)
- [Progress Calculation & Discord Formatting](#progress-calculation--discord-formatting)
- [State Management (`roadmap_state.json`)](#state-management-roadmap_statejson)
- [Installation & Setup](#installation--setup)
- [Usage with MCP Clients](#usage-with-mcp-clients)

---

## Overview

Taskord turns Discord into an interactive project management dashboard. Instead of flooding channels with new messages for every update, Taskord tracks specific Discord message IDs and performs in-place updates (`PATCH`) on roadmaps, complete with automatic recalculation of visual ASCII progress bars.

---

## Architecture & Tech Stack

- **Server Framework:** [FastMCP (`mcp.server.fastmcp`)](https://github.com/modelcontextprotocol/python-sdk)
- **Language:** Python 3.10+
- **HTTP Client:** `httpx` (Synchronous HTTP calls)
- **API Target:** Discord REST API v10 (`https://discord.com/api/v10`)
- **Persistence:** Local JSON file (`roadmap_state.json`)

```mermaid
flowchart TD
    AI[AI Assistant / MCP Client] -->|MCP Protocol| FastMCP[Taskord MCP Server\n'DiscordProjectManager']
    FastMCP -->|Read/Write State| State[roadmap_state.json]
    FastMCP -->|Discord REST API v10| Discord[Discord API / Guild Channels]
    Discord -->|Render Updates| User[Discord Community / Team]
```

---

## Configuration & Environment Variables

| Variable / File | Type | Source / Default | Description |
| :--- | :--- | :--- | :--- |
| `DISCORD_BOT_TOKEN` | Environment Variable | `os.environ.get("DISCORD_BOT_TOKEN")` | Bot authorization token for Discord API authentication. |
| `DISCORD_GUILD_ID` | Environment Variable | `os.environ.get("DISCORD_GUILD_ID")` | Optional environment variable specifying the target Discord Guild (Server) ID. |
| `config.json` | Local File (Git Ignored) | `config.json` | Local configuration storing `"guild_id"`. Template provided at `config.json.example`. |
| `roadmap_state.json` | Local File (Git Ignored) | `roadmap_state.json` | Local state tracking active roadmap message IDs. Template at `roadmap_state.json.example`. |
| `BASE_URL` | Constant | `"https://discord.com/api/v10"` | Discord v10 REST endpoint base URL. |

> [!NOTE]
> If Guild ID is not provided in `DISCORD_GUILD_ID` or `config.json`, the server will interactively prompt for it in a terminal session, or you can configure it on the fly using the `set_guild_id` MCP tool.

---

## Core Features & MCP Tools

### 1. `set_guild_id` & `get_server_config`
Configures or checks the target Discord Guild ID at runtime and saves it to local `config.json`.

- `set_guild_id(guild_id: str)`: Configures and persists the Discord Guild (Server) ID.
- `get_server_config()`: Returns the active Guild ID, state file, and config file status.

---

### 2. `save_idea`
Saves a brainstormed concept or task idea into the designated Discord project ideas/to-do channel.

- **Parameters:**
  - `project_name` (`str`): Name of the project / Discord category.
  - `idea_text` (`str`): Description/text of the brainstormed idea.
- **Channel Resolution Strategy:**
  1. Searches for channel `#to-do` under the category named `project_name`.
  2. Fallback: Searches for channel `#{project_name}-ideas`.
- **Discord Output Format:**
  ```markdown
  💡 **New Idea:**
  <idea_text>
  ```

---

### 3. `log_pull_request_activity`
Posts a pull-request lifecycle update in the project's `#git` channel. This tool is designed for agents, CI jobs, or webhook handlers to call whenever a pull request changes state.

- **Parameters:**
  - `project_name` (`str`): Project / Discord category containing `#git`.
  - `repository` (`str`): Repository identifier, for example `L0ud3r/Taskord`.
  - `pull_request_number` (`int`): Positive GitHub pull-request number.
  - `event` (`str`): `opened`, `merged`, or `closed`.
  - `title`, `author`, `url`, `summary` (`str`, optional): PR metadata included when supplied.
- **Behavior:** Validates the event, finds `#git` beneath the project's category, and posts a single formatted audit entry. It never needs a GitHub token because the caller supplies the event data.

**Example output:**
```markdown
🟣 Merged **Pull Request L0ud3r/Taskord#12**
**Title:** feat: add project scaffolding
**Author:** L0ud3r
**Link:** https://github.com/L0ud3r/Taskord/pull/12
**Summary:** Creates a category and the standard project channels.
```

### 4. `create_project_workspace`
Creates the standard Discord workspace for a new project in one MCP call.

- **Parameters:**
  - `project_name` (`str`): The project/category name to create (up to 100 characters).
- **Behavior:** Creates a Discord category with the provided name and three text channels beneath it: `#roadmap`, `#to-do`, and `#git`. The tool first checks for an existing category with the same name and stops without making changes if it finds one, preventing accidental duplicate workspaces.
- **Output:** Returns the category ID and the channels created. A roadmap message is not created until `create_roadmap` is called.

### 5. `create_roadmap`
Posts a new formatted roadmap message to Discord and records its channel and message IDs for tracking.

- **Parameters:**
  - `project_name` (`str`): Name of the project.
  - `initial_roadmap` (`str`): Markdown-formatted roadmap text.
- **Channel Resolution Strategy:**
  1. Channel `#roadmap` under category `project_name`.
  2. Channel `#{project_name}` under category `Roadmaps`.
  3. Channel `#{project_name}-roadmap`.
- **State Action:** Saves `{ "channel_id": channel_id, "message_id": message_id }` into `roadmap_state.json` under key `project_name.lower()`.

---

### 6. `replace_roadmap`
Completely updates/replaces the contents of the existing tracked roadmap message using an in-place Discord API `PATCH` request.

- **Parameters:**
  - `project_name` (`str`): Name of the project.
  - `roadmap` (`str`): Replacement markdown content.
- **Behavior:** Ensures the roadmap remains a single canonical message without cluttering the channel with new messages.

---

### 7. `update_roadmap_task`
Updates the status icon of a single task in the roadmap message and automatically recalculates category progress percentages and progress bars.

- **Parameters:**
  - `project_name` (`str`): Name of the project.
  - `task_name` (`str`): Substring matching the task line in the roadmap.
  - `status` (`str`): One of the supported status keys:
    - `"done"` → `✅`
    - `"progress"` → `🔄`
    - `"testing"` → `⚠️`
    - `"planned"` → `⬜`
- **Workflow:**
  1. Fetches current message content via `GET /channels/{channel_id}/messages/{message_id}`.
  2. Locates the line containing `task_name` and replaces its status icon.
  3. Groups tasks by category headers and counts completed (`✅`) vs. total tasks.
  4. Generates a 10-block visual progress bar (`█` and `░`) with percentage completion for each category.
  5. Appends the standard Legend and patches the message in-place on Discord.

---

## Progress Calculation & Discord Formatting

### Status Icons
| Status Key | Emoji | Meaning | Counts Toward Progress |
| :--- | :---: | :--- | :---: |
| `done` | `✅` | Completed | **Yes** (100% per task) |
| `progress` | `🔄` | In Progress | No |
| `testing` | `⚠️` | In Testing | No |
| `planned` | `⬜` | Planned | No |

### Visual Progress Bar Format
The progress bar uses a 10-block Unicode representation:
$$\text{pct} = \left\lfloor \frac{\text{completed}}{\text{total}} \times 100 \right\rfloor$$
$$\text{filled blocks} = \lfloor \text{pct} / 10 \rfloor, \quad \text{empty blocks} = 10 - \text{filled blocks}$$

**Example Discord Output:**
```markdown
🗺️ **Project Roadmap**

Backend Development
✅ Setup FastMCP server
🔄 Implement Discord REST API client
⬜ Add SQLite database backend

Frontend Dashboard
✅ Setup React UI
⬜ Connect WebSockets

---
 **Overall Progress**
Backend Development: ██████░░░░ 66%
Frontend Dashboard: █████░░░░░ 50%

---
**Legend**
✅ Completed
🔄 In Progress
⚠️ In Testing
⬜ Planned
```

---

## State Management (`roadmap_state.json`)

The server tracks active roadmap messages locally in `roadmap_state.json`.

### Schema
```json
{
  "<project_key>": {
    "channel_id": "<discord_channel_id>",
    "message_id": "<discord_message_id>"
  }
}
```

### Current Tracked Projects
| Project Key | Channel ID | Message ID |
| :--- | :--- | :--- |
| `fut17-revival` | `1544006434688081961` | `1544279621468692530` |
| `centsible` | `1544006194484617317` | `1544324964172173383` |
| `lsarcade` | `1544362172430159962` | `1544374155317739612` |

---

## Installation & Setup

### Prerequisites
- Python 3.10+
- Discord Bot Token with permissions to view channels and send/edit messages in the specified Guild.

### Dependencies
Install the required packages:
```bash
pip install mcp httpx
```

### Environment Configuration
Set your Discord Bot token:
```bash
# Windows PowerShell
$env:DISCORD_BOT_TOKEN="your_bot_token_here"

# Linux / macOS
export DISCORD_BOT_TOKEN="your_bot_token_here"
```

### Running the Server
```bash
python server.py
```

---

## Usage with MCP Clients

Add the server to your MCP configuration (e.g., Claude Desktop, Cursor, Antigravity, or Cline):

```json
{
  "mcpServers": {
    "taskord": {
      "command": "python",
      "args": ["C:/Projects/Taskord/server.py"],
      "env": {
        "DISCORD_BOT_TOKEN": "your_bot_token_here"
      }
    }
  }
}
```

---

## 💡 Planned Improvements & Registered Suggestions

The following feature requests have been registered in the `#suggestions` channel (Taskord category) on Discord:

1. **Pull Request Activity Logging:** ✅ Implemented with `log_pull_request_activity`.
   - Record GitHub/Git pull requests (opened, merged, closed) in each project's `#git` channel.
   - Keep project members informed with PR titles, authors, links, and summaries.

2. **Automated Project & Channel Scaffolding:** ✅ Implemented with `create_project_workspace`.
   - Create a project on demand directly through MCP tools.
   - Automatically provisions a Discord Category (matching the project name) and spawns 3 standard channels:
     - `#roadmap`
     - `#to-do`
     - `#git`

3. **Intelligent Work Analysis & Multi-Channel Sync:**
   - Automated sync engine that analyzes recent codebase work, commits, and progress.
   - Crawls and synchronizes all text channels under a project to align tasks, roadmaps, and git logs with actual development status.

