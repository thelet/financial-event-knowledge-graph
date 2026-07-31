"""Human-readable rendering of resolution results."""

from __future__ import annotations

from collections import defaultdict

from ...core.models import ArtifactRecord, ResolutionAnomaly


def summarize(
    artifacts: list[ArtifactRecord], anomalies: list[ResolutionAnomaly]
) -> str:
    if not artifacts:
        return "No artifacts resolved."

    by_kind: dict[str, int] = defaultdict(int)
    bytes_by_kind: dict[str, int] = defaultdict(int)
    for artifact in artifacts:
        by_kind[artifact.artifact_kind] += 1
        bytes_by_kind[artifact.artifact_kind] += artifact.expected_size or 0

    lines = [
        f"{'KIND':<18} {'COUNT':>7} {'EXPECTED MiB':>14}",
        f"{'-' * 18} {'-' * 7} {'-' * 14}",
    ]
    for kind in sorted(by_kind):
        lines.append(
            f"{kind:<18} {by_kind[kind]:>7} {bytes_by_kind[kind] / 1_048_576:>14.1f}"
        )
    total = sum(bytes_by_kind.values())
    lines.extend(
        [
            f"{'-' * 18} {'-' * 7} {'-' * 14}",
            f"{'TOTAL':<18} {len(artifacts):>7} {total / 1_048_576:>14.1f}",
        ]
    )

    if anomalies:
        counts: dict[str, int] = defaultdict(int)
        for item in anomalies:
            counts[item.kind] += 1
        lines.append("")
        lines.append(f"Anomalies flagged for review ({len(anomalies)}):")
        for kind in sorted(counts):
            lines.append(f"  {kind:<38} {counts[kind]:>5}")
    else:
        lines.append("")
        lines.append("No anomalies.")

    return "\n".join(lines)
