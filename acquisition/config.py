"""Configuration loading and hashing.

All acquisition scope lives in config/ (v0 plan section 13). Any change to the effective
configuration changes config_hash, which produces a new immutable manifest.
"""

from __future__ import annotations

import hashlib
import json
from datetime import date
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field, field_validator

from .identity import cik10


class CompanyConfig(BaseModel):
    cik: int
    name: str
    wave: str = "W0"
    tickers: list[str] = Field(default_factory=list)
    aliases: list[str] = Field(default_factory=list)

    @property
    def cik10(self) -> str:
        return cik10(self.cik)


class HttpConfig(BaseModel):
    user_agent: str
    requests_per_second: float = 5.0
    max_concurrency: int = 4
    timeout_seconds: float = 60.0
    max_retries: int = 5
    backoff_base_seconds: float = 1.0
    backoff_max_seconds: float = 60.0

    @field_validator("user_agent")
    @classmethod
    def _require_contact(cls, value: str) -> str:
        if "@" not in value:
            raise ValueError(
                "SEC requires a User-Agent containing a contact email address"
            )
        return value


class IncludeConfig(BaseModel):
    full_submission: bool = True
    index_header: bool = True
    assets: bool = True
    # SEC-generated rendering output (R*.htm, FilingSummary.xml, MetaLinks.json,
    # *-xbrl.zip, *_htm.xml). Derived from the filing, not part of it.
    render_artifacts: bool = False


class PathsConfig(BaseModel):
    data_root: str = "data"
    manifests_root: str = "manifests"


class FetchConfig(BaseModel):
    source: str = "sec"
    forms: list[str]
    date_from: str
    date_to: str | None = None
    http: HttpConfig
    include: IncludeConfig = Field(default_factory=IncludeConfig)
    paths: PathsConfig = Field(default_factory=PathsConfig)

    @field_validator("forms")
    @classmethod
    def _non_empty(cls, value: list[str]) -> list[str]:
        if not value:
            raise ValueError("fetch.forms must list at least one form")
        return value

    def effective_date_to(self) -> str:
        return self.date_to or date.today().isoformat()


class AppConfig(BaseModel):
    """Effective configuration plus the raw documents it was built from.

    The raw documents are retained so config_hash covers exactly what was on disk,
    including keys this version of the code does not yet read.
    """

    companies: list[CompanyConfig]
    fetch: FetchConfig
    root: Path
    raw_companies: dict[str, Any]
    raw_fetch: dict[str, Any]

    model_config = {"arbitrary_types_allowed": True}

    @property
    def data_root(self) -> Path:
        return self.root / self.fetch.paths.data_root

    @property
    def manifests_root(self) -> Path:
        return self.root / self.fetch.paths.manifests_root

    @property
    def raw_root(self) -> Path:
        return self.data_root / "raw"

    @property
    def catalog_root(self) -> Path:
        return self.data_root / "catalog"

    @property
    def runs_root(self) -> Path:
        return self.data_root / "runs"

    @property
    def reports_root(self) -> Path:
        return self.data_root / "reports"

    @property
    def tmp_root(self) -> Path:
        return self.data_root / "tmp"

    def company(self, cik: int | str | None = None) -> CompanyConfig:
        """Return the named company, or the single configured one."""
        if cik is None:
            if len(self.companies) != 1:
                raise ValueError(
                    f"{len(self.companies)} companies configured; specify one explicitly"
                )
            return self.companies[0]
        wanted = cik10(cik)
        for company in self.companies:
            if company.cik10 == wanted:
                return company
        raise KeyError(f"No company configured with CIK {wanted}")

    def config_hash(self) -> str:
        return canonical_hash(
            {"companies": self.raw_companies, "fetch": self.raw_fetch}
        )


def canonical_hash(payload: Any) -> str:
    """SHA-256 over a canonical JSON rendering.

    Key order and whitespace are normalized so semantically identical input always hashes
    identically.
    """
    encoded = json.dumps(
        payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True, default=str
    )
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def find_repo_root(start: Path | None = None) -> Path:
    """Walk upward for a directory containing config/fetch.yaml."""
    current = (start or Path(__file__).resolve().parent).resolve()
    for candidate in [current, *current.parents]:
        if (candidate / "config" / "fetch.yaml").is_file():
            return candidate
    raise FileNotFoundError(
        "Could not locate config/fetch.yaml in any parent directory"
    )


def load_config(root: Path | None = None) -> AppConfig:
    resolved_root = Path(root).resolve() if root else find_repo_root()
    config_dir = resolved_root / "config"

    raw_fetch = _read_yaml(config_dir / "fetch.yaml")
    raw_companies = _read_yaml(config_dir / "companies.yaml")

    companies = [CompanyConfig(**entry) for entry in raw_companies.get("companies", [])]
    if not companies:
        raise ValueError("config/companies.yaml lists no companies")

    return AppConfig(
        companies=companies,
        fetch=FetchConfig(**raw_fetch),
        root=resolved_root,
        raw_companies=raw_companies,
        raw_fetch=raw_fetch,
    )


def _read_yaml(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise FileNotFoundError(f"Missing configuration file: {path}")
    with path.open("r", encoding="utf-8") as handle:
        loaded = yaml.safe_load(handle)
    if not isinstance(loaded, dict):
        raise ValueError(f"{path} must contain a YAML mapping")
    return loaded
