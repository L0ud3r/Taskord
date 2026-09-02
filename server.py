import os
import sys
import json
import subprocess
import logging
import re
from logging.handlers import RotatingFileHandler
import httpx
from mcp.server.fastmcp import FastMCP

# Initialize FastMCP Server
mcp = FastMCP("DiscordProjectManager")

# Configuration Constants
BASE_URL = "https://discord.com/api/v10"
CONFIG_FILE = "config.json"
STATE_FILE = "roadmap_state.json"
PULL_REQUEST_SYNC_FILE = "pull_request_sync_state.json"
LOG_DIRECTORY = "logs"
LOG_FILE = os.path.join(LOG_DIRECTORY, "taskord.log")

os.makedirs(LOG_DIRECTORY, exist_ok=True)
logger = logging.getLogger("taskord")
if not logger.handlers:
    log_handler = RotatingFileHandler(
        LOG_FILE,
        maxBytes=512 * 1024,
        backupCount=3,
        encoding="utf-8",
        delay=True,
    )
    log_handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    logger.addHandler(log_handler)
    logger.setLevel(logging.INFO)
    logger.propagate = False

STATUS_ICONS = {
    "done": "✅",
    "progress": "🔄",
    "testing": "🛠️",
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

def run_git_command(repository_path: str, arguments: list[str]) -> str:
    """Run a read-only git command in a repository and return its output."""
    result = subprocess.run(
        ["git", "-C", repository_path, *arguments],
        capture_output=True,
        check=True,
        text=True,
        encoding="utf-8",
    )
    return result.stdout.strip()

def get_github_repository(repository_path: str) -> str:
    """Resolve the GitHub owner/repository name from the local origin remote."""
    origin_url = run_git_command(repository_path, ["remote", "get-url", "origin"])
    match = re.search(r"github\.com[/:]([^/\s:]+)/([^/\s]+?)(?:\.git)?$", origin_url)
    if not match:
        raise ValueError("The origin remote must point to a GitHub repository.")
    return f"{match.group(1)}/{match.group(2)}"

def get_github_pull_requests(repository: str, limit: int) -> list[dict]:
    """Fetch pull requests through the authenticated GitHub CLI."""
    result = subprocess.run(
        [
            "gh", "pr", "list", "--repo", repository, "--state", "all",
            "--limit", str(limit), "--json", "number,title,state,mergedAt,author,url",
        ],
        capture_output=True,
        check=True,
        text=True,
        encoding="utf-8",
    )
    return json.loads(result.stdout)

def load_pull_request_sync_state() -> dict:
    """Load local state used to avoid reposting already synchronized pull requests."""
    if os.path.exists(PULL_REQUEST_SYNC_FILE):
        try:
            with open(PULL_REQUEST_SYNC_FILE, "r", encoding="utf-8") as file:
                return json.load(file)
        except Exception:
            return {}
    return {}

def save_pull_request_sync_state(state: dict) -> None:
    """Persist pull-request sync state locally."""
    with open(PULL_REQUEST_SYNC_FILE, "w", encoding="utf-8") as file:
        json.dump(state, file, indent=2)

def get_registered_pull_request_numbers(channel_id: str, repository: str) -> set[int]:
    """Find pull-request links already present in the recent #git channel history."""
    response = httpx.get(
        f"{BASE_URL}/channels/{channel_id}/messages",
        headers=get_headers(),
        params={"limit": 100},
    )
    response.raise_for_status()
    pattern = re.compile(rf"github\.com/{re.escape(repository)}/pull/(\d+)", re.IGNORECASE)
    return {
        int(match.group(1))
        for message in response.json()
        for match in pattern.finditer(message.get("content", ""))
    }

def get_pull_request_status(pull_request: dict) -> tuple[str, str]:
    """Convert a GitHub pull-request state to Taskord status and Discord label."""
    if pull_request.get("mergedAt"):
        return "done", "✅ Successful"
    if pull_request.get("state") == "OPEN":
        return "testing", "🛠️ Open"
    return "planned", "🔴 Closed"

def get_roadmap_content(project_name: str) -> str | None:
    """Fetch the tracked roadmap once for use during a sync operation."""
    state = load_state()
    project = state.get(project_name.lower())
    if not project:
        return None
    response = httpx.get(
        f"{BASE_URL}/channels/{project['channel_id']}/messages/{project['message_id']}",
        headers=get_headers(),
    )
    response.raise_for_status()
    return response.json()["content"]

def get_roadmap_task_match(
    project_name: str,
    pull_request_title: str,
    roadmap_content: str | None = None,
) -> str | None:
    """Find a roadmap task only when its meaningful words clearly match a PR title."""
    if roadmap_content is None:
        roadmap_content = get_roadmap_content(project_name)
    if roadmap_content is None:
        return None
    task_lines = [
        line for line in roadmap_content.splitlines()
        if line.startswith(tuple(STATUS_ICONS.values()))
    ]
    ignored_words = {"add", "and", "feat", "for", "the", "to", "with"}

    def terms(value: str) -> set[str]:
        normalized = set()
        for word in re.findall(r"[a-zA-Z]{3,}", value.lower()):
            if word in ignored_words:
                continue
            if word.startswith("analy"):
                normalized.add("analysis")
            elif word.startswith("synchron") or word.startswith("sync"):
                normalized.add("sync")
            elif word.startswith("scaffold"):
                normalized.add("scaffold")
            else:
                normalized.add(word[:6])
        return normalized

    title_terms = terms(pull_request_title)
    scored_tasks = []
    for line in task_lines:
        task_name = line.lstrip("✅🔄🛠️⬜ ").strip()
        score = len(title_terms & terms(task_name))
        scored_tasks.append((score, task_name))
    if not scored_tasks:
        return None
    best_score = max(score for score, _ in scored_tasks)
    matches = [task for score, task in scored_tasks if score == best_score]
    return matches[0] if best_score >= 2 and len(matches) == 1 else None

def get_roadmap_task_status(
    project_name: str,
    task_name: str,
    roadmap_content: str | None = None,
) -> str | None:
    """Return the current Taskord status for one tracked roadmap task."""
    if roadmap_content is None:
        roadmap_content = get_roadmap_content(project_name)
    if roadmap_content is None:
        return None
    for line in roadmap_content.splitlines():
        if task_name.lower() in line.lower():
            for status, icon in STATUS_ICONS.items():
                if line.startswith(icon):
                    return status
    return None

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
def analyze_and_sync_project_work(
    project_name: str,
    repository_path: str = ".",
    pull_request_limit: int = 50,
) -> str:
    """Sync unregistered GitHub pull requests to #git and update matching roadmap tasks."""
    if not project_name.strip():
        return "Project name cannot be empty."
    if not 1 <= pull_request_limit <= 100:
        return "pull_request_limit must be between 1 and 100."

    try:
        repository = get_github_repository(repository_path)
        git_channel_id = get_channel_id("git", category_name=project_name)
        pull_requests = get_github_pull_requests(repository, pull_request_limit)
        registered_numbers = get_registered_pull_request_numbers(git_channel_id, repository)
        sync_state = load_pull_request_sync_state()
        project_state = sync_state.setdefault(project_name.lower(), {}).setdefault(repository, {})
        roadmap_content = get_roadmap_content(project_name)
        new_entries = []
        updated_tasks = []

        for pull_request in pull_requests:
            number = str(pull_request["number"])
            status, status_label = get_pull_request_status(pull_request)
            previous = project_state.get(number, {})
            if previous.get("status") != status and pull_request["number"] not in registered_numbers:
                author = pull_request.get("author") or {}
                new_entries.append("\n".join([
                    f"{status_label} **Pull Request {repository}#{number}**",
                    f"**Title:** {pull_request['title']}",
                    f"**Author:** {author.get('login', 'Unknown')}",
                    f"**Link:** {pull_request['url']}",
                ]))

            roadmap_task = get_roadmap_task_match(project_name, pull_request["title"], roadmap_content)
            roadmap_status = previous.get("roadmap_status")
            if roadmap_task and roadmap_status != status:
                current_status = get_roadmap_task_status(project_name, roadmap_task, roadmap_content)
                if current_status == status:
                    roadmap_status = status
                else:
                    result = update_roadmap_task(project_name, roadmap_task, status)
                    if result.startswith("Successfully updated"):
                        roadmap_status = status
                        updated_tasks.append(roadmap_task)
            project_state[number] = {
                "status": status,
                "roadmap_task": roadmap_task,
                "roadmap_status": roadmap_status,
            }

        if new_entries:
            messages = []
            current_message = ""
            for entry in new_entries:
                candidate = f"{current_message}\n\n{entry}" if current_message else entry
                if len(candidate) > 2000:
                    messages.append(current_message)
                    current_message = entry
                else:
                    current_message = candidate
            if current_message:
                messages.append(current_message)
            for message in messages:
                response = httpx.post(
                    f"{BASE_URL}/channels/{git_channel_id}/messages",
                    headers=get_headers(),
                    json={"content": message},
                )
                response.raise_for_status()

        save_pull_request_sync_state(sync_state)
        logger.info(
            "pull_request_sync project=%s repository=%s discovered=%s posted=%s roadmap_updated=%s",
            project_name.strip(), repository, len(pull_requests), len(new_entries), len(updated_tasks),
        )
        return f"Synchronized {len(pull_requests)} pull requests for {repository}: logged {len(new_entries)} new item(s) in #git and updated {len(updated_tasks)} roadmap task(s)."
    except subprocess.CalledProcessError as e:
        error = e.stderr.strip() or "Command failed."
        logger.warning("pull_request_sync_command_failure project=%s error=%s", project_name.strip(), error)
        return f"Failed to synchronize pull requests: {error}"
    except Exception as e:
        logger.exception("pull_request_sync_failure project=%s", project_name.strip())
        return f"Failed to synchronize pull requests: {str(e)}"

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
            "🛠️ In Testing",
            "⬜ Planned"
        ]
        
        # Combine everything and update Discordl
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
