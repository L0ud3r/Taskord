import os
import sys
import json
import httpx
from mcp.server.fastmcp import FastMCP

# Initialize FastMCP Server
mcp = FastMCP("DiscordProjectManager")

# Configuration Constants
BASE_URL = "https://discord.com/api/v10"
CONFIG_FILE = "config.json"
STATE_FILE = "roadmap_state.json"

STATUS_ICONS = {
    "done": "✅",
    "progress": "🔄",
    "testing": "⚠️",
    "planned": "⬜",
}

def load_config() -> dict:
    """Load configuration from config.json."""
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}
    return {}

def save_config(config: dict) -> None:
    """Persist configuration to config.json."""
    with open(CONFIG_FILE, "w", encoding="utf-8") as f:
        json.dump(config, f, indent=2)

def get_headers() -> dict:
    """Get HTTP headers for Discord API authentication."""
    token = os.environ.get("DISCORD_BOT_TOKEN")
    if not token:
        raise ValueError("DISCORD_BOT_TOKEN environment variable is not set.")
    return {
        "Authorization": f"Bot {token}",
        "Content-Type": "application/json"
    }

def get_guild_id() -> str:
    """
    Fetch the Discord Guild ID from environment variable, config.json,
    or prompt the user interactively if in a terminal session.
    """
    env_guild = os.environ.get("DISCORD_GUILD_ID")
    if env_guild and env_guild.strip():
        return env_guild.strip()

    config = load_config()
    guild_id = config.get("guild_id")
    if guild_id and str(guild_id).strip():
        return str(guild_id).strip()

    # Interactive prompt fallback if running directly in a terminal
    try:
        if sys.stdin and sys.stdin.isatty():
            print("Discord Guild (Server) ID is not configured.")
            entered = input("Please enter your Discord Guild ID: ").strip()
            if entered:
                config["guild_id"] = entered
                save_config(config)
                return entered
    except Exception:
        pass

    raise ValueError(
        "Discord Guild ID is not configured. Please set DISCORD_GUILD_ID environment variable, "
        "provide 'guild_id' in config.json, or use the set_guild_id MCP tool."
    )

def get_channel_id(channel_name: str, category_name: str | None = None) -> str:
    """Fetch a channel ID by name, optionally scoped to a category."""
    guild_id = get_guild_id()
    response = httpx.get(f"{BASE_URL}/guilds/{guild_id}/channels", headers=get_headers())
    response.raise_for_status()
    channels = response.json()
    category_ids = {
        channel["id"]
        for channel in channels
        if channel.get("type") == 4
        and channel.get("name", "").lower() == (category_name or "").lower()
    }
    for channel in channels:
        if (
            channel["name"].lower() == channel_name.lower()
            and (category_name is None or channel.get("parent_id") in category_ids)
        ):
            return channel["id"]
    category_suffix = f" in category '{category_name}'" if category_name else ""
    raise ValueError(f"Channel '{channel_name}' not found{category_suffix}.")

def load_state() -> dict:
    """Load roadmap state from state file."""
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}
    return {}

def save_state(state: dict) -> None:
    """Save roadmap state to state file."""
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2)

@mcp.tool()
def set_guild_id(guild_id: str) -> str:
    """Configures and saves the target Discord Guild (Server) ID into config.json."""
    cleaned = guild_id.strip()
    if not cleaned:
        return "Error: Guild ID cannot be empty."
    config = load_config()
    config["guild_id"] = cleaned
    save_config(config)
    return f"Successfully updated and saved Discord Guild ID: {cleaned}"

@mcp.tool()
def get_server_config() -> str:
    """Returns the current server configuration status (excluding sensitive tokens)."""
    config = load_config()
    env_guild = os.environ.get("DISCORD_GUILD_ID")
    active_guild = env_guild or config.get("guild_id") or "Not configured"
    source = "Environment (DISCORD_GUILD_ID)" if env_guild else ("config.json" if config.get("guild_id") else "None")
    return (
        f"Active Discord Guild ID: {active_guild} (Source: {source})\n"
        f"Config File: {CONFIG_FILE}\n"
        f"State File: {STATE_FILE}"
    )

@mcp.tool()
def save_idea(project_name: str, idea_text: str) -> str:
    """Saves a brainstormed idea into the designated project ideas/to-do channel."""
    try:
        try:
            channel_name = "to-do"
            channel_id = get_channel_id(channel_name, category_name=project_name)
        except ValueError:
            channel_name = f"{project_name.lower()}-ideas"
            channel_id = get_channel_id(channel_name)
        payload = {"content": f"💡 **New Idea:**\n{idea_text}"}
        resp = httpx.post(f"{BASE_URL}/channels/{channel_id}/messages", headers=get_headers(), json=payload)
        resp.raise_for_status()
        return f"Successfully saved idea to #{channel_name}."
    except Exception as e:
        return f"Failed to save idea: {str(e)}"

@mcp.tool()
def create_roadmap(project_name: str, initial_roadmap: str) -> str:
    """Posts a new formatted roadmap to the project's roadmap channel and saves its state."""
    try:
        project_key = project_name.lower()
        try:
            channel_name = "roadmap"
            channel_id = get_channel_id(channel_name, category_name=project_name)
        except ValueError:
            channel_name = project_key
            try:
                channel_id = get_channel_id(channel_name, category_name="Roadmaps")
            except ValueError:
                channel_name = f"{project_key}-roadmap"
                channel_id = get_channel_id(channel_name)
        payload = {"content": initial_roadmap}
        resp = httpx.post(f"{BASE_URL}/channels/{channel_id}/messages", headers=get_headers(), json=payload)
        resp.raise_for_status()
        
        message_id = resp.json()["id"]
        state = load_state()
        state[project_key] = {"channel_id": channel_id, "message_id": message_id}
        save_state(state)
        
        return f"Roadmap created in #{channel_name}. Tracking message ID {message_id}."
    except Exception as e:
        return f"Failed to create roadmap: {str(e)}"

@mcp.tool()
def replace_roadmap(project_name: str, roadmap: str) -> str:
    """Replaces the currently tracked roadmap message without creating a duplicate."""
    state = load_state()
    project_key = project_name.lower()
    if project_key not in state:
        return f"No active roadmap tracked for {project_name}. Use create_roadmap first."

    channel_id = state[project_key]["channel_id"]
    message_id = state[project_key]["message_id"]
    try:
        resp = httpx.patch(
            f"{BASE_URL}/channels/{channel_id}/messages/{message_id}",
            headers=get_headers(),
            json={"content": roadmap},
        )
        resp.raise_for_status()
        return f"Roadmap replaced for {project_name}. Tracking message ID {message_id}."
    except Exception as e:
        return f"Failed to replace roadmap: {str(e)}"

@mcp.tool()
def update_roadmap_task(project_name: str, task_name: str, status: str) -> str:
    """
    Updates a task's status and automatically recalculates the overall progress bars.
    Status should be 'done' (✅), 'progress' (🔄), 'testing' (⚠️), or 'planned' (⬜).
    """
    state = load_state()
    project_key = project_name.lower()
    
    if project_key not in state:
        return f"No active roadmap tracked for {project_name}. Use create_roadmap first."
        
    channel_id = state[project_key]["channel_id"]
    message_id = state[project_key]["message_id"]
    
    try:
        # Fetch current message
        resp = httpx.get(f"{BASE_URL}/channels/{channel_id}/messages/{message_id}", headers=get_headers())
        resp.raise_for_status()
        content = resp.json()["content"]
        
        if status.lower() not in STATUS_ICONS:
            return "Invalid status. Use done, progress, testing, or planned."
        new_icon = STATUS_ICONS[status.lower()]
        
        lines = content.split('\n')
        task_section = []
        
        # 1. Update the specific task and separate the task list from the progress section
        for i, line in enumerate(lines):
            # Stop parsing when we hit the separator for the progress section
            if line.strip() == "---" and i + 1 < len(lines) and "Overall Progress" in lines[i+1]:
                break
                
            if task_name.lower() in line.lower() and any(icon in line for icon in STATUS_ICONS.values()):
                clean_line = line
                for icon in STATUS_ICONS.values():
                    clean_line = clean_line.replace(icon, "")
                clean_line = clean_line.strip()
                task_section.append(f"{new_icon} {clean_line}")
            else:
                task_section.append(line)
                
        # 2. Parse categories and calculate progress
        categories = {}
        current_cat = None
        
        for line in task_section:
            stripped = line.strip()
            if not stripped or stripped.startswith("🗺️"): # Ignore empty lines and main title
                continue
                
            if any(stripped.startswith(icon) for icon in STATUS_ICONS.values()):
                if current_cat:
                    categories[current_cat]["total"] += 1
                    if stripped.startswith("✅"):
                        categories[current_cat]["completed"] += 1
            else:
                # Extract category name (stripping emojis if present)
                parts = stripped.split(' ', 1)
                if len(parts) > 1 and not parts[0].isalnum(): 
                    current_cat = parts[1]
                else:
                    current_cat = stripped
                    
                if current_cat and current_cat not in categories:
                    categories[current_cat] = {"total": 0, "completed": 0}

        # 3. Build the new Overall Progress section
        progress_lines = ["---", " **Overall Progress**\n"]
        for cat, data in categories.items():
            if data["total"] > 0:
                pct = int((data["completed"] / data["total"]) * 100)
                filled_blocks = pct // 10
                empty_blocks = 10 - filled_blocks
                bar = ("█" * filled_blocks) + ("░" * empty_blocks)
                progress_lines.append(f"{cat}: {bar} {pct}%\n")
                
        # 4. Re-attach the legend
        legend = [
            "---",
            "**Legend**",
            "✅ Completed",
            "🔄 In Progress",
            "⚠️ In Testing",
            "⬜ Planned"
        ]
        
        # Combine everything and update Discord
        new_content = "\n".join(task_section + progress_lines + legend)
        
        patch_resp = httpx.patch(
            f"{BASE_URL}/channels/{channel_id}/messages/{message_id}", 
            headers=get_headers(), 
            json={"content": new_content}
        )
        patch_resp.raise_for_status()
        
        return f"Successfully updated '{task_name}' to {status} and recalculated progress."
    except Exception as e:
        return f"Failed to update roadmap: {str(e)}"

if __name__ == "__main__":
    mcp.run()
