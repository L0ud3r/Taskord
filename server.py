import os
import json
import httpx
from mcp.server.fastmcp import FastMCP

# Initialize FastMCP Server
mcp = FastMCP("DiscordProjectManager")

# Configuration
DISCORD_TOKEN = os.environ.get("DISCORD_BOT_TOKEN")
GUILD_ID = "1543979060823330946"
HEADERS = {
    "Authorization": f"Bot {DISCORD_TOKEN}",
    "Content-Type": "application/json"
}
BASE_URL = "https://discord.com/api/v10"
STATE_FILE = "roadmap_state.json"
STATUS_ICONS = {
    "done": "✅",
    "progress": "🔄",
    "testing": "⚠️",
    "planned": "⬜",
}

def get_channel_id(channel_name: str, category_name: str | None = None) -> str:
    """Fetch a channel ID by name, optionally scoped to a category."""
    response = httpx.get(f"{BASE_URL}/guilds/{GUILD_ID}/channels", headers=HEADERS)
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

def load_state():
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE, "r") as f:
            return json.load(f)
    return {}

def save_state(state):
    with open(STATE_FILE, "w") as f:
        json.dump(state, f)

@mcp.tool()
def save_idea(project_name: str, idea_text: str) -> str:
    """Saves a brainstormed idea into the designated project ideas channel."""
    try:
        try:
            channel_name = "to-do"
            channel_id = get_channel_id(channel_name, category_name=project_name)
        except ValueError:
            channel_name = f"{project_name.lower()}-ideas"
            channel_id = get_channel_id(channel_name)
        payload = {"content": f"💡 **New Idea:**\n{idea_text}"}
        resp = httpx.post(f"{BASE_URL}/channels/{channel_id}/messages", headers=HEADERS, json=payload)
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
        resp = httpx.post(f"{BASE_URL}/channels/{channel_id}/messages", headers=HEADERS, json=payload)
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
            headers=HEADERS,
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
        resp = httpx.get(f"{BASE_URL}/channels/{channel_id}/messages/{message_id}", headers=HEADERS)
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
            headers=HEADERS, 
            json={"content": new_content}
        )
        patch_resp.raise_for_status()
        
        return f"Successfully updated '{task_name}' to {status} and recalculated progress."
    except Exception as e:
        return f"Failed to update roadmap: {str(e)}"
    
if __name__ == "__main__":
    mcp.run()
