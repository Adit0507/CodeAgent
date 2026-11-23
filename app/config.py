from functools import lru_cache
from pathlib import Path
from pydantic_settings import BaseSettings, SettingsConfigDict

class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    github_token: str | None = None
    github_api_base: str = "https://api.github.com"
    http_timeout_s: float = 30.0

    anthropic_api_key: str | None = None
    model: str = "claude-sonnet-5-5"
    effort: str = "medium"  
    agent_max_tool_calls: int = 15
    agent_max_tokens: int = 16_000  
    agent_citation_repairs: int = 1  # times the agent may fix rejected citations
    agent_repair_extra_calls: int = 2  # extra tool calls granted for a repair

    # API server
    api_token: str | None = None 
    ask_timeout_s: float = 180.0
    max_concurrent_asks: int = 4  # agent runs in flight at once; more requests wait
    views_in_memory: int = 8  # recently used repos kept loaded

    cache_dir: Path = Path(".cache/repos")
    resolve_cache_ttl_s: float = 300.0 

    # repo size limits
    max_code_lines: int = 20_000
    max_total_lines: int = 60_000  # code +tests +docs
    max_repo_files: int = 2_000
    max_file_bytes: int = 1_000_000
    max_tarball_bytes: int = 100 * 1024 * 1024
    max_tar_members: int = 50_000

    # Tool output caps (keep each tool result small enough for the context window).
    read_max_lines: int = 300
    list_max_entries: int = 200
    search_max_matches: int = 50
    search_max_per_file: int = 15
    max_line_chars: int = 400  # longer lines are clipped in tool output
    def_max_bodies: int = 3  
    def_max_body_lines: int = 80

@lru_cache
def get_settings()-> Settings:
    return Settings