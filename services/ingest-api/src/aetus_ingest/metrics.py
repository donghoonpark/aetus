from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from threading import Lock
import time
from typing import Iterable


LabelSet = tuple[tuple[str, str], ...]


def _labels(**labels: str | int | None) -> LabelSet:
    return tuple(sorted((key, str(value)) for key, value in labels.items() if value is not None))


@dataclass(frozen=True, slots=True)
class CounterSample:
    name: str
    labels: dict[str, str]
    value: int


class MetricsRegistry:
    def __init__(self) -> None:
        self._started_at = time.time()
        self._lock = Lock()
        self._counters: defaultdict[tuple[str, LabelSet], int] = defaultdict(int)

    @property
    def started_at_unix_s(self) -> float:
        return self._started_at

    def increment(self, name: str, amount: int = 1, **labels: str | int | None) -> None:
        if amount <= 0:
            return
        key = (name, _labels(**labels))
        with self._lock:
            self._counters[key] += amount

    def samples(self) -> list[CounterSample]:
        with self._lock:
            items = list(self._counters.items())
        return [
            CounterSample(
                name=name,
                labels=dict(labels),
                value=value,
            )
            for (name, labels), value in sorted(items)
        ]

    def to_json(self) -> dict[str, object]:
        counters = [
            {
                "name": sample.name,
                "labels": sample.labels,
                "value": sample.value,
            }
            for sample in self.samples()
        ]
        return {
            "started_at_unix_s": self._started_at,
            "uptime_seconds": max(0.0, time.time() - self._started_at),
            "counters": counters,
        }

    def to_prometheus_text(self) -> str:
        lines = [
            "# HELP aetus_process_start_time_seconds Unix timestamp when the ingest process metrics registry started.",
            "# TYPE aetus_process_start_time_seconds gauge",
            f"aetus_process_start_time_seconds {self._started_at}",
        ]
        seen_names: set[str] = set()
        for sample in self.samples():
            if sample.name not in seen_names:
                lines.append(f"# TYPE {sample.name} counter")
                seen_names.add(sample.name)
            lines.append(f"{sample.name}{_format_prometheus_labels(sample.labels.items())} {sample.value}")
        lines.append("")
        return "\n".join(lines)


def _format_prometheus_labels(labels: Iterable[tuple[str, str]]) -> str:
    items = list(labels)
    if not items:
        return ""
    rendered = ",".join(f'{key}="{_escape_label_value(value)}"' for key, value in sorted(items))
    return "{" + rendered + "}"


def _escape_label_value(value: str) -> str:
    return value.replace("\\", "\\\\").replace("\n", "\\n").replace('"', '\\"')
