"""
OASIS dual-platform parallel simulation preset script
Runs Twitter and Reddit simulations simultaneously, reading the same config file

Features:
- Dual-platform (Twitter + Reddit) parallel simulation
- Does not close the environment immediately after simulation completes; enters wait-for-command mode
- Supports receiving Interview commands via IPC
- Supports single-agent interviews and batch interviews
- Supports remote environment shutdown command

Usage:
    python run_parallel_simulation.py --config simulation_config.json
    python run_parallel_simulation.py --config simulation_config.json --no-wait  # close immediately after completion
    python run_parallel_simulation.py --config simulation_config.json --twitter-only
    python run_parallel_simulation.py --config simulation_config.json --reddit-only

Log structure:
    sim_xxx/
    ├── twitter/
    │   └── actions.jsonl    # Twitter platform action log
    ├── reddit/
    │   └── actions.jsonl    # Reddit platform action log
    ├── simulation.log       # Main simulation process log
    └── run_state.json       # Run state (for API queries)
"""

# ============================================================
# Fix Windows encoding issues: set UTF-8 encoding before any imports
# This fixes the OASIS third-party library reading files without specifying encoding
# ============================================================
import sys
import os

if sys.platform == 'win32':
    # Set Python's default I/O encoding to UTF-8
    # This affects all open() calls that don't specify an encoding
    os.environ.setdefault('PYTHONUTF8', '1')
    os.environ.setdefault('PYTHONIOENCODING', 'utf-8')

    # Reconfigure standard output streams to UTF-8 (fixes garbled console output)
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    if hasattr(sys.stderr, 'reconfigure'):
        sys.stderr.reconfigure(encoding='utf-8', errors='replace')

    # Force the default encoding (affects the default encoding for open())
    # Note: this needs to be set at Python startup; runtime configuration may not take effect
    # So we also need to monkey-patch the built-in open function
    import builtins
    _original_open = builtins.open

    def _utf8_open(file, mode='r', buffering=-1, encoding=None, errors=None,
                   newline=None, closefd=True, opener=None):
        """
        Wrapper for open() that defaults to UTF-8 encoding for text mode.
        Fixes third-party libraries (such as OASIS) that read files without specifying encoding.
        """
        # Only set the default encoding for text mode (non-binary) when no encoding is given
        if encoding is None and 'b' not in mode:
            encoding = 'utf-8'
        return _original_open(file, mode, buffering, encoding, errors,
                              newline, closefd, opener)

    builtins.open = _utf8_open

import argparse
import asyncio
import json
import logging
import random
import signal
import sqlite3
from datetime import datetime
from typing import Dict, Any, List, Optional, Tuple


# Global variables: used by signal handlers
_shutdown_event = None
_cleanup_done = False

# C4: simulation timeouts (seconds). Read from environment variables (the child process inherits the Flask parent's env).
# The per-round env.step timeout prevents a single round from wedging forever due to LLM/network hangs;
# the run timeout is a hard wall-clock cap for the entire simulation.
_ROUND_TIMEOUT_SEC = int(os.environ.get("OASIS_ROUND_TIMEOUT_SEC", "600"))
_RUN_TIMEOUT_SEC = int(os.environ.get("OASIS_RUN_TIMEOUT_SEC", "7200"))

# Add the backend directory to the path
# The script is always located under backend/scripts/
_scripts_dir = os.path.dirname(os.path.abspath(__file__))
_backend_dir = os.path.abspath(os.path.join(_scripts_dir, '..'))
_project_root = os.path.abspath(os.path.join(_backend_dir, '..'))
sys.path.insert(0, _scripts_dir)
sys.path.insert(0, _backend_dir)

# Load the .env file from the project root (contains LLM_API_KEY and other config)
from dotenv import load_dotenv
_env_file = os.path.join(_project_root, '.env')
if os.path.exists(_env_file):
    load_dotenv(_env_file)
    print(f"Loaded environment config: {_env_file}")
else:
    # Try loading backend/.env as a fallback
    _backend_env = os.path.join(_backend_dir, '.env')
    if os.path.exists(_backend_env):
        load_dotenv(_backend_env)
        print(f"Loaded environment config: {_backend_env}")


class MaxTokensWarningFilter(logging.Filter):
    """Filter out camel-ai warnings about max_tokens (we intentionally leave max_tokens unset and let the model decide)."""

    def filter(self, record):
        # Drop log records containing the max_tokens warning
        if "max_tokens" in record.getMessage() and "Invalid or missing" in record.getMessage():
            return False
        return True


# Add the filter at module load time to ensure it's active before any camel code runs
logging.getLogger().addFilter(MaxTokensWarningFilter())


def disable_oasis_logging():
    """
    Disable OASIS library's verbose logging output.
    OASIS logs are too noisy (recording every agent observation and action); we use our own action_logger instead.
    """
    # Disable every OASIS logger
    oasis_loggers = [
        "social.agent",
        "social.twitter",
        "social.rec",
        "oasis.env",
        "table",
    ]

    for logger_name in oasis_loggers:
        logger = logging.getLogger(logger_name)
        logger.setLevel(logging.CRITICAL)  # Only log critical errors
        logger.handlers.clear()
        logger.propagate = False


def init_logging_for_simulation(simulation_dir: str):
    """
    Initialize logging configuration for the simulation.

    Args:
        simulation_dir: Path to the simulation directory.
    """
    # Disable OASIS verbose logging
    disable_oasis_logging()

    # Clean up the old log directory if it exists
    old_log_dir = os.path.join(simulation_dir, "log")
    if os.path.exists(old_log_dir):
        import shutil
        shutil.rmtree(old_log_dir, ignore_errors=True)


from action_logger import SimulationLogManager, PlatformActionLogger

try:
    from camel.models import ModelFactory
    from camel.types import ModelPlatformType
    import oasis
    from oasis import (
        ActionType,
        LLMAction,
        ManualAction,
        generate_twitter_agent_graph,
        generate_reddit_agent_graph
    )
except ImportError as e:
    print(f"Error: missing dependency {e}")
    print("Install first: pip install oasis-ai camel-ai")
    sys.exit(1)


# OASIS action lists are sourced from app.config.Config (single SSoT).
# We import the canonical string names and resolve them to ActionType members
# here (subprocess context: oasis is importable; backend can't import oasis
# safely at module-load time, hence the split). The names list is authoritative;
# if a name disappears from the upstream oasis ActionType enum we fail loudly
# at import (KeyError below) rather than silently dropping the action.
from app.config import Config

# Available Twitter actions (does not include INTERVIEW; INTERVIEW can only be triggered manually via ManualAction)
TWITTER_ACTIONS = [ActionType[name] for name in Config.OASIS_TWITTER_ACTIONS]

# Available Reddit actions (does not include INTERVIEW; INTERVIEW can only be triggered manually via ManualAction)
REDDIT_ACTIONS = [ActionType[name] for name in Config.OASIS_REDDIT_ACTIONS]


# IPC-related constants
IPC_COMMANDS_DIR = "ipc_commands"
IPC_RESPONSES_DIR = "ipc_responses"
ENV_STATUS_FILE = "env_status.json"

class CommandType:
    """Command type constants"""
    INTERVIEW = "interview"
    BATCH_INTERVIEW = "batch_interview"
    CLOSE_ENV = "close_env"


class ParallelIPCHandler:
    """
    Dual-platform IPC command handler.

    Manages environments for both platforms and processes Interview commands.
    """
    
    def __init__(
        self,
        simulation_dir: str,
        twitter_env=None,
        twitter_agent_graph=None,
        reddit_env=None,
        reddit_agent_graph=None
    ):
        self.simulation_dir = simulation_dir
        self.twitter_env = twitter_env
        self.twitter_agent_graph = twitter_agent_graph
        self.reddit_env = reddit_env
        self.reddit_agent_graph = reddit_agent_graph
        
        self.commands_dir = os.path.join(simulation_dir, IPC_COMMANDS_DIR)
        self.responses_dir = os.path.join(simulation_dir, IPC_RESPONSES_DIR)
        self.status_file = os.path.join(simulation_dir, ENV_STATUS_FILE)
        
        # Ensure directories exist
        os.makedirs(self.commands_dir, exist_ok=True)
        os.makedirs(self.responses_dir, exist_ok=True)

    def update_status(self, status: str):
        """Update the environment status."""
        with open(self.status_file, 'w', encoding='utf-8') as f:
            json.dump({
                "status": status,
                "twitter_available": self.twitter_env is not None,
                "reddit_available": self.reddit_env is not None,
                "timestamp": datetime.now().isoformat()
            }, f, ensure_ascii=False, indent=2)
    
    def poll_command(self) -> Optional[Dict[str, Any]]:
        """Poll for pending commands."""
        if not os.path.exists(self.commands_dir):
            return None

        # Collect command files (sorted by modification time)
        command_files = []
        for filename in os.listdir(self.commands_dir):
            if filename.endswith('.json'):
                filepath = os.path.join(self.commands_dir, filename)
                command_files.append((filepath, os.path.getmtime(filepath)))
        
        command_files.sort(key=lambda x: x[1])
        
        for filepath, _ in command_files:
            try:
                with open(filepath, 'r', encoding='utf-8') as f:
                    return json.load(f)
            except (json.JSONDecodeError, OSError):
                continue
        
        return None
    
    def send_response(self, command_id: str, status: str, result: Optional[Dict[str, Any]] = None, error: Optional[str] = None):
        """Send a response."""
        response = {
            "command_id": command_id,
            "status": status,
            "result": result,
            "error": error,
            "timestamp": datetime.now().isoformat()
        }
        
        response_file = os.path.join(self.responses_dir, f"{command_id}.json")
        with open(response_file, 'w', encoding='utf-8') as f:
            json.dump(response, f, ensure_ascii=False, indent=2)
        
        # Delete the command file
        command_file = os.path.join(self.commands_dir, f"{command_id}.json")
        try:
            os.remove(command_file)
        except OSError:
            pass

    def _get_env_and_graph(self, platform: str):
        """
        Get the environment and agent_graph for the specified platform.

        Args:
            platform: Platform name ("twitter" or "reddit").

        Returns:
            (env, agent_graph, platform_name) or (None, None, None).
        """
        if platform == "twitter" and self.twitter_env:
            return self.twitter_env, self.twitter_agent_graph, "twitter"
        elif platform == "reddit" and self.reddit_env:
            return self.reddit_env, self.reddit_agent_graph, "reddit"
        else:
            return None, None, None
    
    async def _interview_single_platform(self, agent_id: int, prompt: str, platform: str) -> Dict[str, Any]:
        """
        Execute an Interview on a single platform.

        Returns:
            A dict containing the result, or a dict containing an error.
        """
        env, agent_graph, actual_platform = self._get_env_and_graph(platform)

        if not env or not agent_graph or actual_platform is None:
            return {"platform": platform, "error": f"{platform} platform unavailable"}

        try:
            agent = agent_graph.get_agent(agent_id)
            interview_action = ManualAction(
                action_type=ActionType.INTERVIEW,
                action_args={"prompt": prompt}
            )
            actions = {agent: interview_action}
            # C4: add a timeout to the interview env.step; TimeoutError is caught by this method's except Exception
            # and returned as an error response, so the persistent command loop never gets stuck forever.
            await asyncio.wait_for(env.step(actions), timeout=_ROUND_TIMEOUT_SEC)

            result = self._get_interview_result(agent_id, actual_platform)
            result["platform"] = actual_platform
            return result

        except Exception as e:
            return {"platform": platform, "error": str(e)}

    async def handle_interview(self, command_id: str, agent_id: int, prompt: str, platform: Optional[str] = None) -> bool:
        """
        Handle a single-agent interview command.

        Args:
            command_id: Command ID.
            agent_id: Agent ID.
            prompt: Interview question.
            platform: Specific platform (optional)
                - "twitter": interview on the Twitter platform only
                - "reddit": interview on the Reddit platform only
                - None / unspecified: interview on both platforms and return a combined result

        Returns:
            True on success, False on failure.
        """
        # If a platform is specified, only interview on that platform
        if platform in ("twitter", "reddit"):
            result = await self._interview_single_platform(agent_id, prompt, platform)

            if "error" in result:
                self.send_response(command_id, "failed", error=result["error"])
                print(f"  Interview failed: agent_id={agent_id}, platform={platform}, error={result['error']}")
                return False
            else:
                self.send_response(command_id, "completed", result=result)
                print(f"  Interview completed: agent_id={agent_id}, platform={platform}")
                return True

        # No platform specified: interview on both platforms
        if not self.twitter_env and not self.reddit_env:
            self.send_response(command_id, "failed", error="No simulation environment available")
            return False

        results = {
            "agent_id": agent_id,
            "prompt": prompt,
            "platforms": {}
        }
        success_count = 0

        # Run interviews on both platforms in parallel
        tasks = []
        platforms_to_interview = []

        if self.twitter_env:
            tasks.append(self._interview_single_platform(agent_id, prompt, "twitter"))
            platforms_to_interview.append("twitter")

        if self.reddit_env:
            tasks.append(self._interview_single_platform(agent_id, prompt, "reddit"))
            platforms_to_interview.append("reddit")

        # Execute in parallel
        platform_results = await asyncio.gather(*tasks)

        for platform_name, platform_result in zip(platforms_to_interview, platform_results):
            results["platforms"][platform_name] = platform_result
            if "error" not in platform_result:
                success_count += 1

        if success_count > 0:
            self.send_response(command_id, "completed", result=results)
            print(f"  Interview completed: agent_id={agent_id}, successful platforms={success_count}/{len(platforms_to_interview)}")
            return True
        else:
            errors = [f"{p}: {r.get('error', 'unknown error')}" for p, r in results["platforms"].items()]
            self.send_response(command_id, "failed", error="; ".join(errors))
            print(f"  Interview failed: agent_id={agent_id}, all platforms failed")
            return False
    
    async def handle_batch_interview(self, command_id: str, interviews: List[Dict], platform: Optional[str] = None) -> bool:
        """
        Handle a batch-interview command.

        Args:
            command_id: Command ID.
            interviews: [{"agent_id": int, "prompt": str, "platform": str(optional)}, ...]
            platform: Default platform (can be overridden by each interview item)
                - "twitter": interview on the Twitter platform only
                - "reddit": interview on the Reddit platform only
                - None / unspecified: interview each agent on both platforms
        """
        # Group by platform
        twitter_interviews = []
        reddit_interviews = []
        both_platforms_interviews = []  # Items that need both platforms

        for interview in interviews:
            item_platform = interview.get("platform", platform)
            if item_platform == "twitter":
                twitter_interviews.append(interview)
            elif item_platform == "reddit":
                reddit_interviews.append(interview)
            else:
                # No platform specified: interview on both
                both_platforms_interviews.append(interview)

        # Distribute both_platforms_interviews to each platform
        if both_platforms_interviews:
            if self.twitter_env:
                twitter_interviews.extend(both_platforms_interviews)
            if self.reddit_env:
                reddit_interviews.extend(both_platforms_interviews)

        results = {}

        # Handle interviews on the Twitter platform
        if twitter_interviews and self.twitter_env and self.twitter_agent_graph:
            try:
                twitter_actions = {}
                for interview in twitter_interviews:
                    agent_id = interview.get("agent_id")
                    prompt = interview.get("prompt", "")
                    try:
                        agent = self.twitter_agent_graph.get_agent(agent_id)
                        twitter_actions[agent] = ManualAction(
                            action_type=ActionType.INTERVIEW,
                            action_args={"prompt": prompt}
                        )
                    except Exception as e:
                        print(f"  Warning: failed to get Twitter Agent {agent_id}: {e}")

                if twitter_actions:
                    await asyncio.wait_for(self.twitter_env.step(twitter_actions), timeout=_ROUND_TIMEOUT_SEC)  # C4: batch interview timeout

                    for interview in twitter_interviews:
                        agent_id = interview.get("agent_id")
                        result = self._get_interview_result(agent_id, "twitter")
                        result["platform"] = "twitter"
                        results[f"twitter_{agent_id}"] = result
            except Exception as e:
                print(f"  Twitter batch Interview failed: {e}")

        # Handle interviews on the Reddit platform
        if reddit_interviews and self.reddit_env and self.reddit_agent_graph:
            try:
                reddit_actions = {}
                for interview in reddit_interviews:
                    agent_id = interview.get("agent_id")
                    prompt = interview.get("prompt", "")
                    try:
                        agent = self.reddit_agent_graph.get_agent(agent_id)
                        reddit_actions[agent] = ManualAction(
                            action_type=ActionType.INTERVIEW,
                            action_args={"prompt": prompt}
                        )
                    except Exception as e:
                        print(f"  Warning: failed to get Reddit Agent {agent_id}: {e}")

                if reddit_actions:
                    await asyncio.wait_for(self.reddit_env.step(reddit_actions), timeout=_ROUND_TIMEOUT_SEC)  # C4: batch interview timeout

                    for interview in reddit_interviews:
                        agent_id = interview.get("agent_id")
                        result = self._get_interview_result(agent_id, "reddit")
                        result["platform"] = "reddit"
                        results[f"reddit_{agent_id}"] = result
            except Exception as e:
                print(f"  Reddit batch Interview failed: {e}")

        if results:
            self.send_response(command_id, "completed", result={
                "interviews_count": len(results),
                "results": results
            })
            print(f"  Batch Interview completed: {len(results)} agents")
            return True
        else:
            self.send_response(command_id, "failed", error="No successful interviews")
            return False

    def _get_interview_result(self, agent_id: int, platform: str) -> Dict[str, Any]:
        """Fetch the latest Interview result from the database."""
        db_path = os.path.join(self.simulation_dir, f"{platform}_simulation.db")

        result = {
            "agent_id": agent_id,
            "response": None,
            "timestamp": None
        }

        if not os.path.exists(db_path):
            return result

        try:
            conn = sqlite3.connect(db_path)
            cursor = conn.cursor()

            # Query the latest Interview record
            cursor.execute("""
                SELECT user_id, info, created_at
                FROM trace
                WHERE action = ? AND user_id = ?
                ORDER BY created_at DESC
                LIMIT 1
            """, (ActionType.INTERVIEW.value, agent_id))

            row = cursor.fetchone()
            if row:
                _user_id, info_json, created_at = row
                try:
                    info = json.loads(info_json) if info_json else {}
                    result["response"] = info.get("response", info)
                    result["timestamp"] = created_at
                except json.JSONDecodeError:
                    result["response"] = info_json

            conn.close()

        except Exception as e:
            print(f"  Failed to read Interview result: {e}")

        return result

    async def process_commands(self) -> bool:
        """
        Process all pending commands.

        Returns:
            True to continue running, False if the loop should exit.
        """
        command = self.poll_command()
        if not command:
            return True

        # Default to empty-string id when the writer omitted it — handlers
        # type command_id as str and we'd rather echo "" in any error
        # response than crash on a malformed command.
        command_id: str = str(command.get("command_id") or "")
        command_type = command.get("command_type")
        args = command.get("args", {})

        print(f"\nReceived IPC command: {command_type}, id={command_id}")

        if command_type == CommandType.INTERVIEW:
            await self.handle_interview(
                command_id,
                args.get("agent_id", 0),
                args.get("prompt", ""),
                args.get("platform")
            )
            return True

        elif command_type == CommandType.BATCH_INTERVIEW:
            await self.handle_batch_interview(
                command_id,
                args.get("interviews", []),
                args.get("platform")
            )
            return True

        elif command_type == CommandType.CLOSE_ENV:
            print("Received close-environment command")
            self.send_response(command_id, "completed", result={"message": "Environment is shutting down"})
            return False

        else:
            self.send_response(command_id, "failed", error=f"Unknown command type: {command_type}")
            return True


def load_config(config_path: str) -> Dict[str, Any]:
    """Load the configuration file."""
    with open(config_path, 'r', encoding='utf-8') as f:
        return json.load(f)


# Non-core action types to filter out (these actions have low analytical value)
FILTERED_ACTIONS = {'refresh', 'sign_up'}

# Action type mapping (name in the database -> standard name)
ACTION_TYPE_MAP = {
    'create_post': 'CREATE_POST',
    'like_post': 'LIKE_POST',
    'dislike_post': 'DISLIKE_POST',
    'repost': 'REPOST',
    'quote_post': 'QUOTE_POST',
    'follow': 'FOLLOW',
    'mute': 'MUTE',
    'create_comment': 'CREATE_COMMENT',
    'like_comment': 'LIKE_COMMENT',
    'dislike_comment': 'DISLIKE_COMMENT',
    'search_posts': 'SEARCH_POSTS',
    'search_user': 'SEARCH_USER',
    'trend': 'TREND',
    'do_nothing': 'DO_NOTHING',
    'interview': 'INTERVIEW',
}


def get_agent_names_from_config(config: Dict[str, Any]) -> Dict[int, str]:
    """
    Get a mapping of agent_id -> entity_name from simulation_config.

    This lets us display real entity names (instead of placeholders like "Agent_0") in actions.jsonl.

    Args:
        config: Contents of simulation_config.json.

    Returns:
        Dict mapping agent_id -> entity_name.
    """
    agent_names = {}
    agent_configs = config.get("agent_configs", [])
    
    for agent_config in agent_configs:
        agent_id = agent_config.get("agent_id")
        entity_name = agent_config.get("entity_name", f"Agent_{agent_id}")
        if agent_id is not None:
            agent_names[agent_id] = entity_name
    
    return agent_names


def fetch_new_actions_from_db(
    db_path: str,
    last_rowid: int,
    agent_names: Dict[int, str]
) -> Tuple[List[Dict[str, Any]], int]:
    """
    Fetch new action records from the database and enrich them with full contextual information.

    Args:
        db_path: Path to the database file.
        last_rowid: The largest rowid read previously (we use rowid instead of created_at because the
            created_at format differs between platforms).
        agent_names: Mapping of agent_id -> agent_name.

    Returns:
        (actions_list, new_last_rowid)
        - actions_list: List of actions; each item contains agent_id, agent_name, action_type, action_args (with context).
        - new_last_rowid: The new largest rowid.
    """
    actions = []
    new_last_rowid = last_rowid

    if not os.path.exists(db_path):
        return actions, new_last_rowid

    try:
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()

        # Track processed records via rowid (SQLite's built-in auto-incrementing field).
        # This sidesteps the created_at format differences (Twitter uses integers, Reddit uses datetime strings).
        cursor.execute("""
            SELECT rowid, user_id, action, info
            FROM trace
            WHERE rowid > ?
            ORDER BY rowid ASC
        """, (last_rowid,))

        for rowid, user_id, action, info_json in cursor.fetchall():
            # Update the max rowid
            new_last_rowid = rowid

            # Filter out non-core actions
            if action in FILTERED_ACTIONS:
                continue

            # Parse the action arguments
            try:
                action_args = json.loads(info_json) if info_json else {}
            except json.JSONDecodeError:
                action_args = {}

            # Trim action_args down to the key fields (keep full content, do not truncate)
            simplified_args = {}
            if 'content' in action_args:
                simplified_args['content'] = action_args['content']
            if 'post_id' in action_args:
                simplified_args['post_id'] = action_args['post_id']
            if 'comment_id' in action_args:
                simplified_args['comment_id'] = action_args['comment_id']
            if 'quoted_id' in action_args:
                simplified_args['quoted_id'] = action_args['quoted_id']
            if 'new_post_id' in action_args:
                simplified_args['new_post_id'] = action_args['new_post_id']
            if 'follow_id' in action_args:
                simplified_args['follow_id'] = action_args['follow_id']
            if 'query' in action_args:
                simplified_args['query'] = action_args['query']
            if 'like_id' in action_args:
                simplified_args['like_id'] = action_args['like_id']
            if 'dislike_id' in action_args:
                simplified_args['dislike_id'] = action_args['dislike_id']
            
            # Convert the action type name. `action` comes from a sqlite row
            # (Any-typed); we keep the str path explicit so the result is a
            # concrete str rather than ``str | None`` for the downstream
            # _enrich_action_context call.
            _action_str = str(action)
            action_type: str = ACTION_TYPE_MAP.get(_action_str, _action_str.upper())

            # Enrich with context (post content, user names, etc.)
            _enrich_action_context(cursor, action_type, simplified_args, agent_names)

            actions.append({
                'agent_id': user_id,
                'agent_name': agent_names.get(user_id, f'Agent_{user_id}'),
                'action_type': action_type,
                'action_args': simplified_args,
            })

        conn.close()
    except Exception as e:
        print(f"Failed to read actions from database: {e}")

    return actions, new_last_rowid


def _enrich_action_context(
    cursor,
    action_type: str,
    action_args: Dict[str, Any],
    agent_names: Dict[int, str]
) -> None:
    """
    Enrich the action with context (post content, user names, etc.).

    Args:
        cursor: Database cursor.
        action_type: Action type.
        action_args: Action arguments (will be mutated).
        agent_names: Mapping of agent_id -> agent_name.
    """
    try:
        # Like/dislike post: enrich with post content and author
        if action_type in ('LIKE_POST', 'DISLIKE_POST'):
            post_id = action_args.get('post_id')
            if post_id:
                post_info = _get_post_info(cursor, post_id, agent_names)
                if post_info:
                    action_args['post_content'] = post_info.get('content', '')
                    action_args['post_author_name'] = post_info.get('author_name', '')

        # Repost: enrich with the original post's content and author
        elif action_type == 'REPOST':
            new_post_id = action_args.get('new_post_id')
            if new_post_id:
                # For a repost, original_post_id points to the original post
                cursor.execute("""
                    SELECT original_post_id FROM post WHERE post_id = ?
                """, (new_post_id,))
                row = cursor.fetchone()
                if row and row[0]:
                    original_post_id = row[0]
                    original_info = _get_post_info(cursor, original_post_id, agent_names)
                    if original_info:
                        action_args['original_content'] = original_info.get('content', '')
                        action_args['original_author_name'] = original_info.get('author_name', '')

        # Quote post: enrich with original post content, author, and quote comment
        elif action_type == 'QUOTE_POST':
            quoted_id = action_args.get('quoted_id')
            new_post_id = action_args.get('new_post_id')

            if quoted_id:
                original_info = _get_post_info(cursor, quoted_id, agent_names)
                if original_info:
                    action_args['original_content'] = original_info.get('content', '')
                    action_args['original_author_name'] = original_info.get('author_name', '')

            # Fetch the comment content of the quote post (quote_content)
            if new_post_id:
                cursor.execute("""
                    SELECT quote_content FROM post WHERE post_id = ?
                """, (new_post_id,))
                row = cursor.fetchone()
                if row and row[0]:
                    action_args['quote_content'] = row[0]

        # Follow user: enrich with the followed user's name
        elif action_type == 'FOLLOW':
            follow_id = action_args.get('follow_id')
            if follow_id:
                # Get followee_id from the follow table
                cursor.execute("""
                    SELECT followee_id FROM follow WHERE follow_id = ?
                """, (follow_id,))
                row = cursor.fetchone()
                if row:
                    followee_id = row[0]
                    target_name = _get_user_name(cursor, followee_id, agent_names)
                    if target_name:
                        action_args['target_user_name'] = target_name

        # Mute user: enrich with the muted user's name
        elif action_type == 'MUTE':
            # Get user_id or target_id from action_args
            target_id = action_args.get('user_id') or action_args.get('target_id')
            if target_id:
                target_name = _get_user_name(cursor, target_id, agent_names)
                if target_name:
                    action_args['target_user_name'] = target_name

        # Like/dislike comment: enrich with comment content and author
        elif action_type in ('LIKE_COMMENT', 'DISLIKE_COMMENT'):
            comment_id = action_args.get('comment_id')
            if comment_id:
                comment_info = _get_comment_info(cursor, comment_id, agent_names)
                if comment_info:
                    action_args['comment_content'] = comment_info.get('content', '')
                    action_args['comment_author_name'] = comment_info.get('author_name', '')

        # Create comment: enrich with info about the post being commented on
        elif action_type == 'CREATE_COMMENT':
            post_id = action_args.get('post_id')
            if post_id:
                post_info = _get_post_info(cursor, post_id, agent_names)
                if post_info:
                    action_args['post_content'] = post_info.get('content', '')
                    action_args['post_author_name'] = post_info.get('author_name', '')

    except Exception as e:
        # A context enrichment failure must not break the main flow
        print(f"Failed to enrich action context: {e}")


def _get_post_info(
    cursor,
    post_id: int,
    agent_names: Dict[int, str]
) -> Optional[Dict[str, str]]:
    """
    Fetch post info.

    Args:
        cursor: Database cursor.
        post_id: Post ID.
        agent_names: Mapping of agent_id -> agent_name.

    Returns:
        Dict with content and author_name, or None.
    """
    try:
        cursor.execute("""
            SELECT p.content, p.user_id, u.agent_id
            FROM post p
            LEFT JOIN user u ON p.user_id = u.user_id
            WHERE p.post_id = ?
        """, (post_id,))
        row = cursor.fetchone()
        if row:
            content = row[0] or ''
            user_id = row[1]
            agent_id = row[2]

            # Prefer the name from agent_names
            author_name = ''
            if agent_id is not None and agent_id in agent_names:
                author_name = agent_names[agent_id]
            elif user_id:
                # Fall back to fetching the name from the user table
                cursor.execute("SELECT name, user_name FROM user WHERE user_id = ?", (user_id,))
                user_row = cursor.fetchone()
                if user_row:
                    author_name = user_row[0] or user_row[1] or ''

            return {'content': content, 'author_name': author_name}
    except Exception:
        pass
    return None


def _get_user_name(
    cursor,
    user_id: int,
    agent_names: Dict[int, str]
) -> Optional[str]:
    """
    Fetch a user's name.

    Args:
        cursor: Database cursor.
        user_id: User ID.
        agent_names: Mapping of agent_id -> agent_name.

    Returns:
        The user's name, or None.
    """
    try:
        cursor.execute("""
            SELECT agent_id, name, user_name FROM user WHERE user_id = ?
        """, (user_id,))
        row = cursor.fetchone()
        if row:
            agent_id = row[0]
            name = row[1]
            user_name = row[2]

            # Prefer the name from agent_names
            if agent_id is not None and agent_id in agent_names:
                return agent_names[agent_id]
            return name or user_name or ''
    except Exception:
        pass
    return None


def _get_comment_info(
    cursor,
    comment_id: int,
    agent_names: Dict[int, str]
) -> Optional[Dict[str, str]]:
    """
    Fetch comment info.

    Args:
        cursor: Database cursor.
        comment_id: Comment ID.
        agent_names: Mapping of agent_id -> agent_name.

    Returns:
        Dict with content and author_name, or None.
    """
    try:
        cursor.execute("""
            SELECT c.content, c.user_id, u.agent_id
            FROM comment c
            LEFT JOIN user u ON c.user_id = u.user_id
            WHERE c.comment_id = ?
        """, (comment_id,))
        row = cursor.fetchone()
        if row:
            content = row[0] or ''
            user_id = row[1]
            agent_id = row[2]

            # Prefer the name from agent_names
            author_name = ''
            if agent_id is not None and agent_id in agent_names:
                author_name = agent_names[agent_id]
            elif user_id:
                # Fall back to fetching the name from the user table
                cursor.execute("SELECT name, user_name FROM user WHERE user_id = ?", (user_id,))
                user_row = cursor.fetchone()
                if user_row:
                    author_name = user_row[0] or user_row[1] or ''

            return {'content': content, 'author_name': author_name}
    except Exception:
        pass
    return None


def create_model(config: Dict[str, Any], use_boost: bool = False):
    """
    Create an LLM model.

    Supports a dual-LLM configuration to accelerate parallel simulations:
    - Default config: LLM_API_KEY, LLM_BASE_URL, LLM_MODEL_NAME
    - Boost config (optional): LLM_BOOST_API_KEY, LLM_BOOST_BASE_URL, LLM_BOOST_MODEL_NAME

    When a boost LLM is configured, parallel simulations can use different API providers for each
    platform, improving overall concurrency.

    Args:
        config: Simulation config dict.
        use_boost: Whether to use the boost LLM configuration (when available).
    """
    # Check whether a boost configuration is present
    boost_api_key = os.environ.get("LLM_BOOST_API_KEY", "")
    boost_base_url = os.environ.get("LLM_BOOST_BASE_URL", "")
    boost_model = os.environ.get("LLM_BOOST_MODEL_NAME", "")
    has_boost_config = bool(boost_api_key)

    # Pick which LLM to use based on the argument and the configuration
    if use_boost and has_boost_config:
        # Use boost configuration
        llm_api_key = boost_api_key
        llm_base_url = boost_base_url
        llm_model = boost_model or os.environ.get("LLM_MODEL_NAME", "")
        config_label = "[Boost LLM]"
    else:
        # Use the default configuration
        llm_api_key = os.environ.get("LLM_API_KEY", "")
        llm_base_url = os.environ.get("LLM_BASE_URL", "")
        llm_model = os.environ.get("LLM_MODEL_NAME", "")
        config_label = "[Default LLM]"

    # Fall back to the model name from config if .env doesn't define one
    if not llm_model:
        llm_model = config.get("llm_model", "gpt-4o-mini")

    # Set the environment variables required by camel-ai
    if llm_api_key:
        os.environ["OPENAI_API_KEY"] = llm_api_key

    if not os.environ.get("OPENAI_API_KEY"):
        raise ValueError("Missing API key configuration: please set LLM_API_KEY in the project root .env file")

    if llm_base_url:
        os.environ["OPENAI_API_BASE_URL"] = llm_base_url

    print(f"{config_label} model={llm_model}, base_url={llm_base_url[:40] if llm_base_url else 'default'}...")

    return ModelFactory.create(
        model_platform=ModelPlatformType.OPENAI,
        model_type=llm_model,
    )


def get_active_agents_for_round(
    env,
    config: Dict[str, Any],
    current_hour: int,
    round_num: int
) -> List:
    """Decide which agents are active for this round based on the time of day and config."""
    time_config = config.get("time_config", {})
    agent_configs = config.get("agent_configs", [])
    
    base_min = time_config.get("agents_per_hour_min", 5)
    base_max = time_config.get("agents_per_hour_max", 20)
    
    peak_hours = time_config.get("peak_hours", [9, 10, 11, 14, 15, 20, 21, 22])
    off_peak_hours = time_config.get("off_peak_hours", [0, 1, 2, 3, 4, 5])
    
    if current_hour in peak_hours:
        multiplier = time_config.get("peak_activity_multiplier", 1.5)
    elif current_hour in off_peak_hours:
        multiplier = time_config.get("off_peak_activity_multiplier", 0.3)
    else:
        multiplier = 1.0
    
    target_count = int(random.uniform(base_min, base_max) * multiplier)
    
    candidates = []
    for cfg in agent_configs:
        agent_id = cfg.get("agent_id", 0)
        active_hours = cfg.get("active_hours", list(range(8, 23)))
        activity_level = cfg.get("activity_level", 0.5)
        
        if current_hour not in active_hours:
            continue
        
        if random.random() < activity_level:
            candidates.append(agent_id)
    
    selected_ids = random.sample(
        candidates, 
        min(target_count, len(candidates))
    ) if candidates else []
    
    active_agents = []
    for agent_id in selected_ids:
        try:
            agent = env.agent_graph.get_agent(agent_id)
            active_agents.append((agent_id, agent))
        except Exception:
            pass
    
    return active_agents


class PlatformSimulation:
    """Container for the result of a platform simulation.

    ``env`` and ``agent_graph`` carry concrete types from the third-party
    ``oasis`` package which is imported conditionally (try/except above) and
    ships no Pyright stubs. We type them as ``Any`` rather than introduce
    a stub for an upstream module we don't own — the runtime call sites
    assign concrete OasisEnv / AgentGraph instances and use them directly.
    """

    env: Any = None
    agent_graph: Any = None
    total_actions: int = 0

    def __init__(self):
        self.env = None
        self.agent_graph = None
        self.total_actions = 0


async def run_twitter_simulation(
    config: Dict[str, Any],
    simulation_dir: str,
    action_logger: Optional[PlatformActionLogger] = None,
    main_logger: Optional[SimulationLogManager] = None,
    max_rounds: Optional[int] = None
) -> PlatformSimulation:
    """Run the Twitter simulation.

    Args:
        config: Simulation config.
        simulation_dir: Simulation directory.
        action_logger: Action logger.
        main_logger: Main log manager.
        max_rounds: Maximum number of simulation rounds (optional; used to truncate long simulations).

    Returns:
        PlatformSimulation: Result object containing env and agent_graph.
    """
    result = PlatformSimulation()

    def log_info(msg):
        if main_logger:
            main_logger.info(f"[Twitter] {msg}")
        print(f"[Twitter] {msg}")

    log_info("Initializing...")

    # Twitter uses the default LLM configuration
    model = create_model(config, use_boost=False)

    # OASIS Twitter uses CSV format
    profile_path = os.path.join(simulation_dir, "twitter_profiles.csv")
    if not os.path.exists(profile_path):
        log_info(f"Error: profile file does not exist: {profile_path}")
        return result

    result.agent_graph = await generate_twitter_agent_graph(
        profile_path=profile_path,
        model=model,
        available_actions=TWITTER_ACTIONS,
    )

    # Get the real agent-name mapping from the config (uses entity_name instead of the default Agent_X)
    agent_names = get_agent_names_from_config(config)
    # Fall back to OASIS's default name for any agent missing from the config
    for agent_id, agent in result.agent_graph.get_agents():
        if agent_id not in agent_names:
            agent_names[agent_id] = getattr(agent, 'name', f'Agent_{agent_id}')

    db_path = os.path.join(simulation_dir, "twitter_simulation.db")
    if os.path.exists(db_path):
        os.remove(db_path)

    result.env = oasis.make(
        agent_graph=result.agent_graph,
        platform=oasis.DefaultPlatformType.TWITTER,
        database_path=db_path,
        semaphore=30,  # Cap the max number of concurrent LLM requests to avoid API overload
    )

    await result.env.reset()
    log_info("Environment started")

    if action_logger:
        action_logger.log_simulation_start(config)

    total_actions = 0
    last_rowid = 0  # Track the last row processed in the database (use rowid to avoid created_at format differences)

    # Execute initial events
    event_config = config.get("event_config", {})
    initial_posts = event_config.get("initial_posts", [])

    # Record the start of round 0 (the initial-event phase)
    if action_logger:
        action_logger.log_round_start(0, 0)  # round 0, simulated_hour 0
    
    initial_action_count = 0
    if initial_posts:
        initial_actions = {}
        for post in initial_posts:
            agent_id = post.get("poster_agent_id", 0)
            content = post.get("content", "")
            try:
                agent = result.env.agent_graph.get_agent(agent_id)
                initial_actions[agent] = ManualAction(
                    action_type=ActionType.CREATE_POST,
                    action_args={"content": content}
                )
                
                if action_logger:
                    action_logger.log_action(
                        round_num=0,
                        agent_id=agent_id,
                        agent_name=agent_names.get(agent_id, f"Agent_{agent_id}"),
                        action_type="CREATE_POST",
                        action_args={"content": content}
                    )
                    total_actions += 1
                    initial_action_count += 1
            except Exception:
                pass
        
        if initial_actions:
            # C4: also add a timeout to the initial-post env.step. Without it, an LLM/network hang before the
            # protected main loop could wedge the process indefinitely.
            try:
                await asyncio.wait_for(result.env.step(initial_actions), timeout=_ROUND_TIMEOUT_SEC)
                log_info(f"Published {len(initial_actions)} initial posts")
            except asyncio.TimeoutError:
                log_info(f"[timeout] Initial-post env.step exceeded {_ROUND_TIMEOUT_SEC}s; skipping initial posts and continuing")

    # Record the end of round 0
    if action_logger:
        action_logger.log_round_end(0, initial_action_count)

    # Main simulation loop
    time_config = config.get("time_config", {})
    total_hours = time_config.get("total_simulation_hours", 72)
    minutes_per_round = time_config.get("minutes_per_round", 30)
    total_rounds = (total_hours * 60) // minutes_per_round

    # Truncate to max_rounds if specified
    if max_rounds is not None and max_rounds > 0:
        original_rounds = total_rounds
        total_rounds = min(total_rounds, max_rounds)
        if total_rounds < original_rounds:
            log_info(f"Round count truncated: {original_rounds} -> {total_rounds} (max_rounds={max_rounds})")

    start_time = datetime.now()

    for round_num in range(total_rounds):
        # Check whether a shutdown signal was received
        if _shutdown_event and _shutdown_event.is_set():
            if main_logger:
                main_logger.info(f"Received shutdown signal; stopping simulation at round {round_num + 1}")
            break

        simulated_minutes = round_num * minutes_per_round
        simulated_hour = (simulated_minutes // 60) % 24
        simulated_day = simulated_minutes // (60 * 24) + 1

        active_agents = get_active_agents_for_round(
            result.env, config, simulated_hour, round_num
        )

        # Record the round start whether or not there are active agents
        if action_logger:
            action_logger.log_round_start(round_num + 1, simulated_hour)

        if not active_agents:
            # When there are no active agents, still record the round end (actions_count=0)
            if action_logger:
                action_logger.log_round_end(round_num + 1, 0)
            continue

        # C4: total-runtime cap. If exceeded, gracefully stop (keep env so subsequent close/interview still work)
        if (datetime.now() - start_time).total_seconds() > _RUN_TIMEOUT_SEC:
            log_info(f"[timeout] Total simulation time exceeded {_RUN_TIMEOUT_SEC}s; stopping at round {round_num + 1}")
            break

        actions = {agent: LLMAction() for _, agent in active_agents}
        # C4: per-round timeout. Prevents env.step from wedging forever due to LLM/network hangs
        try:
            await asyncio.wait_for(result.env.step(actions), timeout=_ROUND_TIMEOUT_SEC)
        except asyncio.TimeoutError:
            log_info(f"[timeout] Round {round_num + 1} env.step exceeded {_ROUND_TIMEOUT_SEC}s; skipping and stopping the loop")
            if action_logger:
                action_logger.log_round_end(round_num + 1, 0)
            break

        # Fetch the actually executed actions from the database and log them
        actual_actions, last_rowid = fetch_new_actions_from_db(
            db_path, last_rowid, agent_names
        )

        round_action_count = 0
        for action_data in actual_actions:
            if action_logger:
                action_logger.log_action(
                    round_num=round_num + 1,
                    agent_id=action_data['agent_id'],
                    agent_name=action_data['agent_name'],
                    action_type=action_data['action_type'],
                    action_args=action_data['action_args']
                )
                total_actions += 1
                round_action_count += 1

        if action_logger:
            action_logger.log_round_end(round_num + 1, round_action_count)

        if (round_num + 1) % 20 == 0:
            progress = (round_num + 1) / total_rounds * 100
            log_info(f"Day {simulated_day}, {simulated_hour:02d}:00 - Round {round_num + 1}/{total_rounds} ({progress:.1f}%)")

    # Note: do not close the environment; keep it available for Interview commands

    if action_logger:
        action_logger.log_simulation_end(total_rounds, total_actions)

    result.total_actions = total_actions
    elapsed = (datetime.now() - start_time).total_seconds()
    log_info(f"Simulation loop completed! Elapsed: {elapsed:.1f}s, total actions: {total_actions}")

    return result


async def run_reddit_simulation(
    config: Dict[str, Any],
    simulation_dir: str,
    action_logger: Optional[PlatformActionLogger] = None,
    main_logger: Optional[SimulationLogManager] = None,
    max_rounds: Optional[int] = None
) -> PlatformSimulation:
    """Run the Reddit simulation.

    Args:
        config: Simulation config.
        simulation_dir: Simulation directory.
        action_logger: Action logger.
        main_logger: Main log manager.
        max_rounds: Maximum number of simulation rounds (optional; used to truncate long simulations).

    Returns:
        PlatformSimulation: Result object containing env and agent_graph.
    """
    result = PlatformSimulation()

    def log_info(msg):
        if main_logger:
            main_logger.info(f"[Reddit] {msg}")
        print(f"[Reddit] {msg}")

    log_info("Initializing...")

    # Reddit uses the boost LLM configuration when available, falling back to the default
    model = create_model(config, use_boost=True)

    profile_path = os.path.join(simulation_dir, "reddit_profiles.json")
    if not os.path.exists(profile_path):
        log_info(f"Error: profile file does not exist: {profile_path}")
        return result

    result.agent_graph = await generate_reddit_agent_graph(
        profile_path=profile_path,
        model=model,
        available_actions=REDDIT_ACTIONS,
    )

    # Get the real agent-name mapping from the config (uses entity_name instead of the default Agent_X)
    agent_names = get_agent_names_from_config(config)
    # Fall back to OASIS's default name for any agent missing from the config
    for agent_id, agent in result.agent_graph.get_agents():
        if agent_id not in agent_names:
            agent_names[agent_id] = getattr(agent, 'name', f'Agent_{agent_id}')

    db_path = os.path.join(simulation_dir, "reddit_simulation.db")
    if os.path.exists(db_path):
        os.remove(db_path)

    result.env = oasis.make(
        agent_graph=result.agent_graph,
        platform=oasis.DefaultPlatformType.REDDIT,
        database_path=db_path,
        semaphore=30,  # Cap the max number of concurrent LLM requests to avoid API overload
    )

    await result.env.reset()
    log_info("Environment started")

    if action_logger:
        action_logger.log_simulation_start(config)

    total_actions = 0
    last_rowid = 0  # Track the last row processed in the database (use rowid to avoid created_at format differences)

    # Execute initial events
    event_config = config.get("event_config", {})
    initial_posts = event_config.get("initial_posts", [])

    # Record the start of round 0 (the initial-event phase)
    if action_logger:
        action_logger.log_round_start(0, 0)  # round 0, simulated_hour 0
    
    initial_action_count = 0
    if initial_posts:
        initial_actions = {}
        for post in initial_posts:
            agent_id = post.get("poster_agent_id", 0)
            content = post.get("content", "")
            try:
                agent = result.env.agent_graph.get_agent(agent_id)
                if agent in initial_actions:
                    if not isinstance(initial_actions[agent], list):
                        initial_actions[agent] = [initial_actions[agent]]
                    initial_actions[agent].append(ManualAction(
                        action_type=ActionType.CREATE_POST,
                        action_args={"content": content}
                    ))
                else:
                    initial_actions[agent] = ManualAction(
                        action_type=ActionType.CREATE_POST,
                        action_args={"content": content}
                    )
                
                if action_logger:
                    action_logger.log_action(
                        round_num=0,
                        agent_id=agent_id,
                        agent_name=agent_names.get(agent_id, f"Agent_{agent_id}"),
                        action_type="CREATE_POST",
                        action_args={"content": content}
                    )
                    total_actions += 1
                    initial_action_count += 1
            except Exception:
                pass
        
        if initial_actions:
            # C4: also add a timeout to the initial-post env.step. Without it, an LLM/network hang before the
            # protected main loop could wedge the process indefinitely.
            try:
                await asyncio.wait_for(result.env.step(initial_actions), timeout=_ROUND_TIMEOUT_SEC)
                log_info(f"Published {len(initial_actions)} initial posts")
            except asyncio.TimeoutError:
                log_info(f"[timeout] Initial-post env.step exceeded {_ROUND_TIMEOUT_SEC}s; skipping initial posts and continuing")

    # Record the end of round 0
    if action_logger:
        action_logger.log_round_end(0, initial_action_count)

    # Main simulation loop
    time_config = config.get("time_config", {})
    total_hours = time_config.get("total_simulation_hours", 72)
    minutes_per_round = time_config.get("minutes_per_round", 30)
    total_rounds = (total_hours * 60) // minutes_per_round

    # Truncate to max_rounds if specified
    if max_rounds is not None and max_rounds > 0:
        original_rounds = total_rounds
        total_rounds = min(total_rounds, max_rounds)
        if total_rounds < original_rounds:
            log_info(f"Round count truncated: {original_rounds} -> {total_rounds} (max_rounds={max_rounds})")

    start_time = datetime.now()

    for round_num in range(total_rounds):
        # Check whether a shutdown signal was received
        if _shutdown_event and _shutdown_event.is_set():
            if main_logger:
                main_logger.info(f"Received shutdown signal; stopping simulation at round {round_num + 1}")
            break

        simulated_minutes = round_num * minutes_per_round
        simulated_hour = (simulated_minutes // 60) % 24
        simulated_day = simulated_minutes // (60 * 24) + 1

        active_agents = get_active_agents_for_round(
            result.env, config, simulated_hour, round_num
        )

        # Record the round start whether or not there are active agents
        if action_logger:
            action_logger.log_round_start(round_num + 1, simulated_hour)

        if not active_agents:
            # When there are no active agents, still record the round end (actions_count=0)
            if action_logger:
                action_logger.log_round_end(round_num + 1, 0)
            continue

        # C4: total-runtime cap. If exceeded, gracefully stop (keep env so subsequent close/interview still work)
        if (datetime.now() - start_time).total_seconds() > _RUN_TIMEOUT_SEC:
            log_info(f"[timeout] Total simulation time exceeded {_RUN_TIMEOUT_SEC}s; stopping at round {round_num + 1}")
            break

        actions = {agent: LLMAction() for _, agent in active_agents}
        # C4: per-round timeout. Prevents env.step from wedging forever due to LLM/network hangs
        try:
            await asyncio.wait_for(result.env.step(actions), timeout=_ROUND_TIMEOUT_SEC)
        except asyncio.TimeoutError:
            log_info(f"[timeout] Round {round_num + 1} env.step exceeded {_ROUND_TIMEOUT_SEC}s; skipping and stopping the loop")
            if action_logger:
                action_logger.log_round_end(round_num + 1, 0)
            break

        # Fetch the actually executed actions from the database and log them
        actual_actions, last_rowid = fetch_new_actions_from_db(
            db_path, last_rowid, agent_names
        )

        round_action_count = 0
        for action_data in actual_actions:
            if action_logger:
                action_logger.log_action(
                    round_num=round_num + 1,
                    agent_id=action_data['agent_id'],
                    agent_name=action_data['agent_name'],
                    action_type=action_data['action_type'],
                    action_args=action_data['action_args']
                )
                total_actions += 1
                round_action_count += 1

        if action_logger:
            action_logger.log_round_end(round_num + 1, round_action_count)

        if (round_num + 1) % 20 == 0:
            progress = (round_num + 1) / total_rounds * 100
            log_info(f"Day {simulated_day}, {simulated_hour:02d}:00 - Round {round_num + 1}/{total_rounds} ({progress:.1f}%)")

    # Note: do not close the environment; keep it available for Interview commands

    if action_logger:
        action_logger.log_simulation_end(total_rounds, total_actions)

    result.total_actions = total_actions
    elapsed = (datetime.now() - start_time).total_seconds()
    log_info(f"Simulation loop completed! Elapsed: {elapsed:.1f}s, total actions: {total_actions}")

    return result


async def main():
    parser = argparse.ArgumentParser(description='OASIS dual-platform parallel simulation')
    parser.add_argument(
        '--config',
        type=str,
        required=True,
        help='Path to the config file (simulation_config.json)'
    )
    parser.add_argument(
        '--twitter-only',
        action='store_true',
        help='Run only the Twitter simulation'
    )
    parser.add_argument(
        '--reddit-only',
        action='store_true',
        help='Run only the Reddit simulation'
    )
    parser.add_argument(
        '--max-rounds',
        type=int,
        default=None,
        help='Maximum number of simulation rounds (optional; used to truncate long simulations)'
    )
    parser.add_argument(
        '--no-wait',
        action='store_true',
        default=False,
        help='Close the environment immediately after the simulation completes, without entering wait-for-command mode'
    )

    args = parser.parse_args()

    # Create the shutdown event at the start of main() so the whole program can respond to shutdown signals
    global _shutdown_event
    _shutdown_event = asyncio.Event()

    if not os.path.exists(args.config):
        print(f"Error: config file does not exist: {args.config}")
        sys.exit(1)

    config = load_config(args.config)
    simulation_dir = os.path.dirname(args.config) or "."
    wait_for_commands = not args.no_wait

    # Initialize logging (disable OASIS logging, clean up old files)
    init_logging_for_simulation(simulation_dir)

    # Create the log manager
    log_manager = SimulationLogManager(simulation_dir)
    twitter_logger = log_manager.get_twitter_logger()
    reddit_logger = log_manager.get_reddit_logger()

    log_manager.info("=" * 60)
    log_manager.info("OASIS dual-platform parallel simulation")
    log_manager.info(f"Config file: {args.config}")
    log_manager.info(f"Simulation ID: {config.get('simulation_id', 'unknown')}")
    log_manager.info(f"Wait-for-command mode: {'enabled' if wait_for_commands else 'disabled'}")
    log_manager.info("=" * 60)

    time_config = config.get("time_config", {})
    total_hours = time_config.get('total_simulation_hours', 72)
    minutes_per_round = time_config.get('minutes_per_round', 30)
    config_total_rounds = (total_hours * 60) // minutes_per_round

    log_manager.info(f"Simulation parameters:")
    log_manager.info(f"  - Total simulated hours: {total_hours}h")
    log_manager.info(f"  - Minutes per round: {minutes_per_round}m")
    log_manager.info(f"  - Total rounds from config: {config_total_rounds}")
    if args.max_rounds:
        log_manager.info(f"  - Max-rounds limit: {args.max_rounds}")
        if args.max_rounds < config_total_rounds:
            log_manager.info(f"  - Actual rounds to execute: {args.max_rounds} (truncated)")
    log_manager.info(f"  - Agent count: {len(config.get('agent_configs', []))}")

    log_manager.info("Log structure:")
    log_manager.info(f"  - Main log: simulation.log")
    log_manager.info(f"  - Twitter actions: twitter/actions.jsonl")
    log_manager.info(f"  - Reddit actions: reddit/actions.jsonl")
    log_manager.info("=" * 60)

    start_time = datetime.now()

    # Store the simulation results for both platforms
    twitter_result: Optional[PlatformSimulation] = None
    reddit_result: Optional[PlatformSimulation] = None

    if args.twitter_only:
        # C4: symmetric with the parallel path. Isolate non-timeout single-platform exceptions to None and log them,
        # to ensure the subsequent env.close block remains reachable.
        try:
            twitter_result = await run_twitter_simulation(config, simulation_dir, twitter_logger, log_manager, args.max_rounds)
        except Exception as e:
            log_manager.error(f"[Twitter] Simulation exception isolated: {e}")
            twitter_result = None
    elif args.reddit_only:
        try:
            reddit_result = await run_reddit_simulation(config, simulation_dir, reddit_logger, log_manager, args.max_rounds)
        except Exception as e:
            log_manager.error(f"[Reddit] Simulation exception isolated: {e}")
            reddit_result = None
    else:
        # Run in parallel (each platform uses its own logger)
        # C4: return_exceptions=True. An exception in one platform (e.g. LLM 401/429/quota) will no longer cancel the
        # other, nor bypass the env.close block below (which would leak environments, violating the
        # "graceful stop, keep environment" design).
        results = await asyncio.gather(
            run_twitter_simulation(config, simulation_dir, twitter_logger, log_manager, args.max_rounds),
            run_reddit_simulation(config, simulation_dir, reddit_logger, log_manager, args.max_rounds),
            return_exceptions=True,
        )
        # gather(return_exceptions=True) yields PlatformSimulation | BaseException
        # per item; narrow to Optional[PlatformSimulation] before reassigning
        # the prior-declared variables.
        twitter_raw, reddit_raw = results
        if isinstance(twitter_raw, BaseException):
            log_manager.error(f"[Twitter] Simulation exception isolated: {twitter_raw}")
            twitter_result = None
        else:
            twitter_result = twitter_raw
        if isinstance(reddit_raw, BaseException):
            log_manager.error(f"[Reddit] Simulation exception isolated: {reddit_raw}")
            reddit_result = None
        else:
            reddit_result = reddit_raw

    total_elapsed = (datetime.now() - start_time).total_seconds()
    log_manager.info("=" * 60)
    log_manager.info(f"Simulation loop completed! Total elapsed: {total_elapsed:.1f}s")

    # Whether to enter wait-for-command mode
    if wait_for_commands:
        log_manager.info("")
        log_manager.info("=" * 60)
        log_manager.info("Entering wait-for-command mode - environment kept running")
        log_manager.info("Supported commands: interview, batch_interview, close_env")
        log_manager.info("=" * 60)

        # Create the IPC handler
        ipc_handler = ParallelIPCHandler(
            simulation_dir=simulation_dir,
            twitter_env=twitter_result.env if twitter_result else None,
            twitter_agent_graph=twitter_result.agent_graph if twitter_result else None,
            reddit_env=reddit_result.env if reddit_result else None,
            reddit_agent_graph=reddit_result.agent_graph if reddit_result else None
        )
        ipc_handler.update_status("alive")

        # Wait-for-command loop (uses the global _shutdown_event)
        try:
            while not _shutdown_event.is_set():
                should_continue = await ipc_handler.process_commands()
                if not should_continue:
                    break
                # Use wait_for instead of sleep so we can respond to shutdown_event
                try:
                    await asyncio.wait_for(_shutdown_event.wait(), timeout=0.5)
                    break  # Received shutdown signal
                except asyncio.TimeoutError:
                    pass  # Timeout: continue the loop
        except KeyboardInterrupt:
            print("\nReceived interrupt signal")
        except asyncio.CancelledError:
            print("\nTask cancelled")
        except Exception as e:
            print(f"\nCommand processing error: {e}")

        log_manager.info("\nShutting down environment...")
        ipc_handler.update_status("stopped")

    # Close the environments
    if twitter_result and twitter_result.env:
        await twitter_result.env.close()
        log_manager.info("[Twitter] Environment closed")

    if reddit_result and reddit_result.env:
        await reddit_result.env.close()
        log_manager.info("[Reddit] Environment closed")

    log_manager.info("=" * 60)
    log_manager.info(f"All done!")
    log_manager.info(f"Log files:")
    log_manager.info(f"  - {os.path.join(simulation_dir, 'simulation.log')}")
    log_manager.info(f"  - {os.path.join(simulation_dir, 'twitter', 'actions.jsonl')}")
    log_manager.info(f"  - {os.path.join(simulation_dir, 'reddit', 'actions.jsonl')}")
    log_manager.info("=" * 60)


def setup_signal_handlers(loop=None):
    """
    Set up signal handlers to ensure the process exits cleanly on SIGTERM/SIGINT.

    Persistent-simulation scenario: the process does not exit after the simulation completes; it waits for
    interview commands. When a termination signal arrives we need to:
    1. Notify the asyncio loop to stop waiting.
    2. Give the program a chance to clean up resources (close the database, environments, etc.).
    3. Then exit.
    """
    def signal_handler(signum, frame):
        global _cleanup_done
        sig_name = "SIGTERM" if signum == signal.SIGTERM else "SIGINT"
        print(f"\nReceived {sig_name} signal, exiting...")

        if not _cleanup_done:
            _cleanup_done = True
            # Set the event to notify the asyncio loop to exit (giving the loop a chance to clean up)
            if _shutdown_event:
                _shutdown_event.set()

        # Do not call sys.exit() directly; let the asyncio loop exit cleanly and clean up resources.
        # Only force-exit on a repeated signal.
        else:
            print("Forcing exit...")
            sys.exit(1)

    signal.signal(signal.SIGTERM, signal_handler)
    signal.signal(signal.SIGINT, signal_handler)


if __name__ == "__main__":
    setup_signal_handlers()
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nProgram interrupted")
    except SystemExit:
        pass
    finally:
        # Clean up the multiprocessing resource tracker (avoids warnings at exit)
        try:
            from multiprocessing import resource_tracker
            # Private CPython API: ResourceTracker._stop is undocumented but
            # is the canonical way to silence the at-exit resource-tracker
            # warnings. Reach for it via getattr so static analysis sees a
            # single, intentional dynamic dispatch.
            _tracker = resource_tracker._resource_tracker
            _stop = getattr(_tracker, "_stop", None)
            if _stop is not None:
                _stop()
        except Exception:
            pass
        print("Simulation process exited")
