#!/usr/bin/env python3
"""
Configuration management module.
Centralized management of API configuration, proxy settings, and path configuration.
"""

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Optional


@dataclass
class LLMConfig:
    """LLM API configuration."""
    api_key: str = ""
    base_url: str = "http://58.210.177.113:8888/v1"
    model: str = "mimo-v2-flash"
    max_tokens: int = 2200
    temperature: float = 0.2
    timeout: int = 300

    def __post_init__(self):
        # Read from environment variables first
        self.api_key = os.getenv("OPENAI_API_KEY", self.api_key)
        self.base_url = os.getenv("LLM_BASE_URL", self.base_url)
        self.model = os.getenv("LLM_MODEL", self.model)

    def validate(self) -> bool:
        """Validate configuration."""
        return bool(self.api_key)


@dataclass
class ProxyConfig:
    """Proxy configuration."""
    http_proxy: str = ""
    https_proxy: str = ""
    proxy_mode: str = "auto"  # off, on, auto

    def __post_init__(self):
        # Read from environment variables if not specified
        if not self.http_proxy:
            self.http_proxy = (
                os.getenv("PIPELINE_HTTP_PROXY")
                or os.getenv("HTTP_PROXY")
                or os.getenv("http_proxy")
                or ""
            )
        if not self.https_proxy:
            self.https_proxy = (
                os.getenv("PIPELINE_HTTPS_PROXY")
                or os.getenv("HTTPS_PROXY")
                or os.getenv("https_proxy")
                or ""
            )
        if not self.proxy_mode:
            self.proxy_mode = os.getenv("PROXY_MODE", "auto")

    def to_dict(self) -> Dict[str, str]:
        """Convert to dictionary format."""
        proxies = {}
        if self.http_proxy:
            proxies["http"] = self.http_proxy
        if self.https_proxy:
            proxies["https"] = self.https_proxy
        return proxies

    def to_args(self) -> list:
        """Convert to command-line arguments format."""
        args = []
        if self.http_proxy:
            args.extend(["--http-proxy", self.http_proxy])
        if self.https_proxy:
            args.extend(["--https-proxy", self.https_proxy])
        return args

    def apply_to_env(self):
        """Apply to environment variables."""
        if self.http_proxy:
            os.environ["HTTP_PROXY"] = self.http_proxy
        if self.https_proxy:
            os.environ["HTTPS_PROXY"] = self.https_proxy


@dataclass
class PathConfig:
    """Path configuration."""
    output_dir: Path
    project_root: Path = field(default_factory=lambda: Path(__file__).parent.resolve())
    input_dir: Path = field(default_factory=lambda: Path("input"))
    csv_path: Path = field(default_factory=lambda: Path("scholars.csv"))

    def __post_init__(self):
        # Convert to absolute paths
        if not self.input_dir.is_absolute():
            self.input_dir = self.project_root / self.input_dir
        if not self.csv_path.is_absolute():
            self.csv_path = self.project_root / self.csv_path

    def get_scholar_output_dir(self, author_name: str) -> Path:
        """Get scholar output directory."""
        from utils import safe_name
        return self.output_dir / safe_name(author_name)

    def ensure_dirs(self):
        """Ensure directories exist."""
        self.input_dir.mkdir(parents=True, exist_ok=True)
        self.output_dir.mkdir(parents=True, exist_ok=True)


@dataclass
class PipelineConfig:
    """Pipeline configuration."""
    max_papers: int = 1000
    core_limit: int = 20
    var_limit: int = 8
    fallback_core_limit: int = 8
    strict_evidence: bool = False
    skip_existing: bool = True
    max_workers: int = 16

    def to_args(self) -> list:
        """Convert to command-line arguments format."""
        args = [
            "--max-papers", str(self.max_papers),
            "--core-limit", str(self.core_limit),
            "--var-limit", str(self.var_limit),
            "--fallback-core-limit", str(self.fallback_core_limit),
            "--strict-evidence", str(self.strict_evidence).lower(),
        ]
        return args


@dataclass
class AppConfig:
    """Application configuration."""
    llm: LLMConfig = field(default_factory=LLMConfig)
    proxy: ProxyConfig = field(default_factory=ProxyConfig)
    paths: PathConfig = field(default_factory=lambda: PathConfig(output_dir=Path("runs")))
    pipeline: PipelineConfig = field(default_factory=PipelineConfig)

    @classmethod
    def from_env(cls, output_dir: Path = None) -> "AppConfig":
        """Create configuration from environment variables."""
        return cls(
            llm=LLMConfig(),
            proxy=ProxyConfig(),
            paths=PathConfig(output_dir=output_dir or Path("runs")),
            pipeline=PipelineConfig(),
        )

    @classmethod
    def from_args(cls, args) -> "AppConfig":
        """Create configuration from command-line arguments."""
        output_dir = Path(getattr(args, 'output_dir', None) or "runs")
        config = cls.from_env(output_dir=output_dir)

        # Override LLM configuration
        if hasattr(args, 'api_key') and args.api_key:
            config.llm.api_key = args.api_key
        if hasattr(args, 'base_url') and args.base_url:
            config.llm.base_url = args.base_url
        if hasattr(args, 'model') and args.model:
            config.llm.model = args.model

        # Override proxy configuration
        if hasattr(args, 'http_proxy') and args.http_proxy:
            config.proxy.http_proxy = args.http_proxy
        if hasattr(args, 'https_proxy') and args.https_proxy:
            config.proxy.https_proxy = args.https_proxy

        # Override path configuration
        if hasattr(args, 'project_root') and args.project_root:
            config.paths.project_root = Path(args.project_root).expanduser().resolve()
        if hasattr(args, 'csv') and args.csv:
            config.paths.csv_path = Path(args.csv)

        # Override pipeline configuration
        if hasattr(args, 'max_papers') and args.max_papers:
            config.pipeline.max_papers = args.max_papers
        if hasattr(args, 'core_limit') and args.core_limit:
            config.pipeline.core_limit = args.core_limit
        if hasattr(args, 'var_limit') and args.var_limit:
            config.pipeline.var_limit = args.var_limit
        if hasattr(args, 'fallback_core_limit') and args.fallback_core_limit:
            config.pipeline.fallback_core_limit = args.fallback_core_limit
        if hasattr(args, 'strict_evidence') and args.strict_evidence:
            config.pipeline.strict_evidence = args.strict_evidence.lower() in {"1", "true", "yes", "y", "on"}
        if hasattr(args, 'skip_existing') and args.skip_existing:
            config.pipeline.skip_existing = args.skip_existing.lower() in {"1", "true", "yes", "y", "on"}
        if hasattr(args, 'max_workers') and args.max_workers:
            config.pipeline.max_workers = args.max_workers

        return config

    def validate(self) -> list:
        """Validate configuration, return list of errors."""
        errors = []

        if not self.llm.validate():
            errors.append("Missing API Key. Provide via --api-key or OPENAI_API_KEY environment variable.")

        if not self.paths.csv_path.exists():
            errors.append(f"CSV file not found: {self.paths.csv_path}")

        return errors

    def print_summary(self):
        """Print configuration summary."""
        print(f"[INFO] Project root: {self.paths.project_root}")
        print(f"[INFO] CSV file: {self.paths.csv_path}")
        print(f"[INFO] Output dir: {self.paths.output_dir}")
        print(f"[INFO] LLM Base URL: {self.llm.base_url}")
        print(f"[INFO] LLM Model: {self.llm.model}")
        print(f"[INFO] Proxy mode: {self.proxy.proxy_mode}")
        print(f"[INFO] Proxy config: {'set' if self.proxy.to_dict() else 'none'}")
        print(f"[INFO] Max papers: {self.pipeline.max_papers}")
        print(f"[INFO] Parallel workers: {self.pipeline.max_workers}")


# ============ Convenience functions ============

def get_config(output_dir: Path = None) -> AppConfig:
    """Get application configuration (from environment variables)."""
    return AppConfig.from_env(output_dir=output_dir)


def get_llm_config() -> LLMConfig:
    """Get LLM configuration."""
    return LLMConfig()


def get_proxy_config() -> ProxyConfig:
    """Get proxy configuration."""
    return ProxyConfig()


def get_path_config(output_dir: Path = None) -> PathConfig:
    """Get path configuration."""
    return PathConfig(output_dir=output_dir or Path("runs"))


# ============ Tests ============

if __name__ == "__main__":
    # Test configuration loading
    config = AppConfig.from_env(output_dir=Path("runs"))
    config.print_summary()

    errors = config.validate()
    if errors:
        print("\nConfiguration errors:")
        for error in errors:
            print(f"  - {error}")
    else:
        print("\nConfiguration validation passed!")
