from pydantic_settings import BaseSettings
from pydantic import Field
from typing import List, Optional
import os


class Settings(BaseSettings):
    # LLM Configuration
    llm_provider: str = Field(default="openai", description="LLM provider: openai, anthropic, or mock")
    openai_api_key: Optional[str] = Field(default=None, description="OpenAI API key")
    openai_model: str = Field(default="gpt-4o", description="OpenAI model to use")
    anthropic_api_key: Optional[str] = Field(default=None, description="Anthropic API key")
    anthropic_model: str = Field(default="claude-3-5-sonnet-20241022", description="Anthropic model to use")

    # Agent Configuration
    max_steps: int = Field(default=20, description="Maximum steps per agent run")
    step_timeout_seconds: int = Field(default=30, description="Timeout per step")
    overall_timeout_seconds: int = Field(default=300, description="Overall run timeout")

    # Safety Configuration
    allowed_domains: List[str] = Field(
        default=["localhost", "127.0.0.1", "demo.interface.ai"],
        description="Allowed domains for automation"
    )
    allowed_action_types: List[str] = Field(
        default=["click", "type", "navigate", "scroll", "wait", "extract"],
        description="Allowed action types"
    )
    risky_action_types: List[str] = Field(
        default=["delete", "transfer", "approve", "confirm"],
        description="Action types requiring confirmation"
    )

    # Replay Configuration
    replay_timeout_seconds: int = Field(default=60, description="Timeout for replay execution")
    element_wait_timeout_ms: int = Field(default=10000, description="Element wait timeout in ms")
    max_retries: int = Field(default=3, description="Max retries for transient failures")

    # Evidence Configuration
    evidence_dir: str = Field(default="evidence", description="Directory for evidence storage")
    capture_screenshots: bool = Field(default=True, description="Capture screenshots on failure")
    capture_dom_snapshots: bool = Field(default=True, description="Capture DOM snapshots on failure")

    # Escalation Configuration
    escalation_enabled: bool = Field(default=True, description="Enable human escalation")
    escalation_timeout_seconds: int = Field(default=300, description="Escalation timeout")

    # Demo App Configuration
    demo_app_url: str = Field(default="http://localhost:5002", description="Demo app URL")
    demo_app_port: int = Field(default=5002, description="Demo app port")

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"
        case_sensitive = False


settings = Settings()