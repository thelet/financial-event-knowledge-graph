"""Configuration loading and hashing.

All normalization scope lives in config/. Any change to the effective configuration
changes config_hash, and therefore derivation_id on every output.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field

from .identity import canonical_hash


class PathsConfig(BaseModel):
    data_root: str = "data"
    manifests_root: str = "normalization_manifests"
    acquisition_catalog: str = "data/catalog"


class ParserConfig(BaseModel):
    default: str = "sec_html"
    fallback: str = "lxml"
    mode: str = "normal"
    min_source_coverage: float = 0.60


class HeadingsConfig(BaseModel):
    filing_label_patterns: list[str] = Field(default_factory=list)
    min_font_weight: int = 600
    max_heading_chars: int = 200
    min_mean_confidence: float = 0.35


class TablesConfig(BaseModel):
    wide_table_columns: int = 12
    layout_max_rows: int = 1
    layout_max_cols: int = 1


class PassagesConfig(BaseModel):
    target_chars: int = 1500
    max_chars: int = 4000
    min_chars: int = 200
    excluded_block_types: list[str] = Field(
        default_factory=lambda: ["page_header", "page_number", "heading", "image"]
    )


class SelectionConfig(BaseModel):
    policy_file: str = "config/selection_policy.yaml"
    min_text_chars: int = 400


class ImageHeavyConfig(BaseModel):
    min_text_chars: int = 500
    min_chars_per_image: int = 200


class NormalizationConfig(BaseModel):
    version: str = "v1"
    paths: PathsConfig = Field(default_factory=PathsConfig)
    parser: ParserConfig = Field(default_factory=ParserConfig)
    headings: HeadingsConfig = Field(default_factory=HeadingsConfig)
    tables: TablesConfig = Field(default_factory=TablesConfig)
    passages: PassagesConfig = Field(default_factory=PassagesConfig)
    selection: SelectionConfig = Field(default_factory=SelectionConfig)
    image_heavy: ImageHeavyConfig = Field(default_factory=ImageHeavyConfig)


class SelectionRule(BaseModel):
    id: str
    when: dict[str, Any] = Field(default_factory=dict)
    decision: str
    reason: str


class SelectionPolicy(BaseModel):
    version: str
    rules: list[SelectionRule] = Field(default_factory=list)
    default_decision: str = "exclude"
    default_reason: str = "NEEDS_REVIEW"


class AppConfig(BaseModel):
    """Effective configuration plus the raw documents it was built from.

    The raw documents are hashed so config_hash covers exactly what was on disk,
    including keys this version of the code does not read.
    """

    root: Path
    normalization: NormalizationConfig
    policy: SelectionPolicy
    raw_normalization: dict[str, Any]
    raw_policy: dict[str, Any]

    model_config = {"arbitrary_types_allowed": True}

    def config_hash(self) -> str:
        return canonical_hash(
            {"normalization": self.raw_normalization, "policy": self.raw_policy}
        )

    # -- derived paths -----------------------------------------------------------------

    @property
    def data_root(self) -> Path:
        return self.root / self.normalization.paths.data_root

    @property
    def manifests_root(self) -> Path:
        return self.root / self.normalization.paths.manifests_root

    @property
    def acquisition_catalog_root(self) -> Path:
        return self.root / self.normalization.paths.acquisition_catalog

    @property
    def acquisition_raw_root(self) -> Path:
        return self.data_root / "raw"

    @property
    def normalized_root(self) -> Path:
        return self.data_root / "normalized"

    @property
    def catalog_root(self) -> Path:
        return self.data_root / "normalization_catalog"

    @property
    def runs_root(self) -> Path:
        return self.data_root / "normalization_runs"

    @property
    def reports_root(self) -> Path:
        return self.data_root / "normalization_reports"

    @property
    def tmp_root(self) -> Path:
        return self.data_root / "normalization_tmp"


def find_repo_root(start: Path | None = None) -> Path:
    current = (start or Path(__file__).resolve().parent).resolve()
    for candidate in [current, *current.parents]:
        if (candidate / "config" / "normalization.yaml").is_file():
            return candidate
    raise FileNotFoundError("Could not locate config/normalization.yaml")


def load_config(root: Path | None = None) -> AppConfig:
    resolved = Path(root).resolve() if root else find_repo_root()
    raw_norm = _read_yaml(resolved / "config" / "normalization.yaml")
    normalization = NormalizationConfig(**raw_norm)
    raw_policy = _read_yaml(resolved / normalization.selection.policy_file)

    default = raw_policy.get("default", {}) or {}
    policy = SelectionPolicy(
        version=raw_policy.get("version", "v0"),
        rules=[SelectionRule(**r) for r in raw_policy.get("rules", [])],
        default_decision=default.get("decision", "exclude"),
        default_reason=default.get("reason", "NEEDS_REVIEW"),
    )
    return AppConfig(
        root=resolved,
        normalization=normalization,
        policy=policy,
        raw_normalization=raw_norm,
        raw_policy=raw_policy,
    )


def _read_yaml(path: Path) -> dict[str, Any]:
    path = Path(path)
    if not path.is_file():
        raise FileNotFoundError(f"Missing configuration file: {path}")
    loaded = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(loaded, dict):
        raise ValueError(f"{path} must contain a YAML mapping")
    return loaded
