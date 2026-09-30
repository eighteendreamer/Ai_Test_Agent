"""Process-local Prometheus registry in text exposition format 0.0.4.

No upstream counterpart (checked: pyproject deps, src-wide grep for an existing
prometheus/metrics registry, official FastAPI docs): the project ships no metrics
client and adding one is out of scope, so this is a minimal dependency-free
implementation. Counter names are rendered verbatim (no automatic ``_total``
suffix) so call sites own the final metric name.
"""

from __future__ import annotations

import math
import re
import threading
from collections.abc import Iterable, Mapping, Sequence
from typing import Any

DEFAULT_BUCKETS: tuple[float, ...] = (
    0.05,
    0.1,
    0.25,
    0.5,
    1.0,
    2.5,
    5.0,
    10.0,
    30.0,
    60.0,
    120.0,
    300.0,
    600.0,
    1800.0,
    3600.0,
    math.inf,
)

_NAME_PATTERN = re.compile(r"^[a-zA-Z_:][a-zA-Z0-9_:]*$")
_LABEL_PATTERN = re.compile(r"^[a-zA-Z_][a-zA-Z0-9_]*$")


def _escape_label(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")


def _escape_help(value: str) -> str:
    return value.replace("\\", "\\\\").replace("\n", "\\n")


def _format_value(value: float) -> str:
    number = float(value)
    if math.isnan(number):
        return "NaN"
    if math.isinf(number):
        return "+Inf" if number > 0 else "-Inf"
    if number == int(number) and abs(number) < 1e15:
        return str(int(number))
    return repr(number)


def _label_block(pairs: Sequence[tuple[str, str]]) -> str:
    if not pairs:
        return ""
    return "{" + ",".join(f'{name}="{_escape_label(value)}"' for name, value in pairs) + "}"


def _normalize_buckets(buckets: Iterable[float] | None) -> tuple[float, ...]:
    values = sorted({float(bucket) for bucket in (buckets or DEFAULT_BUCKETS)})
    if not values or values[-1] != math.inf:
        values.append(math.inf)
    return tuple(values)


def _check_name(name: str) -> str:
    if not isinstance(name, str) or not _NAME_PATTERN.match(name):
        raise ValueError(f"invalid Prometheus metric name: {name!r}")
    return name


def _check_label_names(metric: str, label_names: Iterable[str]) -> tuple[str, ...]:
    names = tuple(label_names)
    if len(set(names)) != len(names):
        raise ValueError(f"duplicate label names on metric {metric!r}: {list(names)}")
    for label in names:
        if not _LABEL_PATTERN.match(label):
            raise ValueError(f"invalid label name {label!r} on metric {metric!r}")
    return names


class _Metric:
    metric_type = ""

    def __init__(self, name: str, help: str = "", label_names: Iterable[str] = ()) -> None:
        self.name = _check_name(name)
        self.help = help or ""
        self.label_names = _check_label_names(self.name, label_names)
        self._lock = threading.Lock()
        self._children: dict[tuple[str, ...], Any] = {}

    def _key(self, labels: Mapping[str, Any] | None) -> tuple[str, ...]:
        provided = dict(labels or {})
        if set(provided) != set(self.label_names):
            raise ValueError(
                f"metric {self.name} expects labels {list(self.label_names)}, got {list(provided)}"
            )
        return tuple(str(provided.get(name, "")) for name in self.label_names)

    def _pairs(self, key: tuple[str, ...]) -> list[tuple[str, str]]:
        return list(zip(self.label_names, key))

    def _header(self) -> list[str]:
        return [
            f"# HELP {self.name} {_escape_help(self.help)}".rstrip(),
            f"# TYPE {self.name} {self.metric_type}",
        ]

    def _baseline(self) -> list[str]:
        return []

    def _snapshot(self, sample: Any) -> Any:
        return sample

    def render(self) -> list[str]:
        with self._lock:
            lines = list(self._header())
            children = {key: self._snapshot(sample) for key, sample in self._children.items()}
        if not children and not self.label_names:
            lines.extend(self._baseline())
            return lines
        for key in sorted(children):
            lines.extend(self._render_key(key, children[key]))
        return lines

    def _render_key(self, key: tuple[str, ...], sample: Any) -> list[str]:
        return [f"{self.name}{_label_block(self._pairs(key))} {_format_value(sample)}"]


class Counter(_Metric):
    metric_type = "counter"

    def labels(self, **labels: Any) -> "LabeledCounter":
        return LabeledCounter(self, self._key(labels))

    def inc(self, value: float = 1.0) -> None:
        self._add(self._key(None), value)

    def _add(self, key: tuple[str, ...], value: float) -> None:
        amount = float(value)
        if amount < 0:
            raise ValueError(f"counter {self.name} cannot decrease by {amount}")
        with self._lock:
            self._children[key] = self._children.get(key, 0.0) + amount

    def value(self, **labels: Any) -> float:
        key = self._key(labels)
        with self._lock:
            return self._children.get(key, 0.0)

    def _baseline(self) -> list[str]:
        return [f"{self.name} 0"]


class LabeledCounter:
    __slots__ = ("_metric", "_key")

    def __init__(self, metric: Counter, key: tuple[str, ...]) -> None:
        self._metric = metric
        self._key = key

    def inc(self, value: float = 1.0) -> None:
        self._metric._add(self._key, value)


class Gauge(_Metric):
    metric_type = "gauge"

    def labels(self, **labels: Any) -> "LabeledGauge":
        return LabeledGauge(self, self._key(labels))

    def remove(self, **labels: Any) -> None:
        key = self._key(labels)
        with self._lock:
            self._children.pop(key, None)

    def replace_samples(self, samples: Mapping[tuple[str, ...], float]) -> None:
        """Publish one complete collector snapshot, removing absent label sets."""
        replacement: dict[tuple[str, ...], float] = {}
        for labels, value in samples.items():
            if not isinstance(labels, tuple) or len(labels) != len(self.label_names):
                raise ValueError(
                    f"gauge {self.name} expects {len(self.label_names)} label values, got {labels!r}"
                )
            replacement[tuple(str(label) for label in labels)] = float(value)
        with self._lock:
            self._children = replacement

    def set(self, value: float) -> None:
        self._write(self._key(None), value)

    def inc(self, value: float = 1.0) -> None:
        self._bump(self._key(None), float(value))

    def dec(self, value: float = 1.0) -> None:
        self._bump(self._key(None), -float(value))

    def _bump(self, key: tuple[str, ...], delta: float) -> None:
        with self._lock:
            self._children[key] = self._children.get(key, 0.0) + delta

    def _read(self, key: tuple[str, ...]) -> float:
        with self._lock:
            return self._children.get(key, 0.0)

    def _write(self, key: tuple[str, ...], value: float) -> None:
        with self._lock:
            self._children[key] = float(value)

    def value(self, **labels: Any) -> float:
        return self._read(self._key(labels))

    def _baseline(self) -> list[str]:
        return [f"{self.name} 0"]


class LabeledGauge:
    __slots__ = ("_metric", "_key")

    def __init__(self, metric: Gauge, key: tuple[str, ...]) -> None:
        self._metric = metric
        self._key = key

    def set(self, value: float) -> None:
        self._metric._write(self._key, value)

    def inc(self, value: float = 1.0) -> None:
        self._metric._bump(self._key, float(value))

    def dec(self, value: float = 1.0) -> None:
        self._metric._bump(self._key, -float(value))


class Histogram(_Metric):
    metric_type = "histogram"

    def __init__(
        self,
        name: str,
        help: str = "",
        label_names: Iterable[str] = (),
        buckets: Iterable[float] | None = None,
    ) -> None:
        super().__init__(name, help, label_names)
        self.buckets = _normalize_buckets(buckets)

    def labels(self, **labels: Any) -> "LabeledHistogram":
        return LabeledHistogram(self, self._key(labels))

    def observe(self, value: float) -> None:
        self._observe(self._key(None), value)

    def _observe(self, key: tuple[str, ...], value: float) -> None:
        sample = float(value)
        with self._lock:
            series = self._children.get(key)
            if series is None:
                series = [0, 0.0, *[0] * len(self.buckets)]
                self._children[key] = series
            series[0] += 1
            series[1] += sample
            for index, bound in enumerate(self.buckets):
                if sample <= bound:
                    series[2 + index] += 1

    def _snapshot(self, sample: Any) -> Any:
        return list(sample)

    def _baseline(self) -> list[str]:
        return self._samples((), [0, 0.0, *[0] * len(self.buckets)])

    def _render_key(self, key: tuple[str, ...], sample: Sequence[Any]) -> list[str]:
        return self._samples(key, list(sample))

    def _samples(self, key: tuple[str, ...], series: list[Any]) -> list[str]:
        pairs = self._pairs(key)
        lines = [
            f"{self.name}_bucket{_label_block([*pairs, ('le', _format_value(bound))])} {series[2 + index]}"
            for index, bound in enumerate(self.buckets)
        ]
        lines.append(f"{self.name}_sum{_label_block(pairs)} {_format_value(series[1])}")
        lines.append(f"{self.name}_count{_label_block(pairs)} {series[0]}")
        return lines


class LabeledHistogram:
    __slots__ = ("_metric", "_key")

    def __init__(self, metric: Histogram, key: tuple[str, ...]) -> None:
        self._metric = metric
        self._key = key

    def observe(self, value: float) -> None:
        self._metric._observe(self._key, value)


_METRIC_CLASSES: dict[str, type[_Metric]] = {
    "counter": Counter,
    "gauge": Gauge,
    "histogram": Histogram,
}


class Registry:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._metrics: dict[str, _Metric] = {}

    def get_or_create(
        self,
        metric_cls: type[_Metric],
        name: str,
        help: str = "",
        label_names: Iterable[str] = (),
        buckets: Iterable[float] | None = None,
    ) -> Any:
        if metric_cls not in _METRIC_CLASSES.values():
            raise ValueError(f"unsupported metric type: {metric_cls!r}")
        names = tuple(label_names)
        normalized = _normalize_buckets(buckets) if metric_cls is Histogram else None
        with self._lock:
            existing = self._metrics.get(name)
            if existing is not None:
                self._reject_conflict(name, existing, metric_cls, names, normalized)
                return existing
            if metric_cls is Histogram:
                created: _Metric = Histogram(name, help, names, buckets=normalized)
            else:
                created = metric_cls(name, help, names)
            self._metrics[name] = created
            return created

    @staticmethod
    def _reject_conflict(
        name: str,
        existing: _Metric,
        metric_cls: type[_Metric],
        label_names: tuple[str, ...],
        buckets: tuple[float, ...] | None,
    ) -> None:
        if type(existing) is not metric_cls:
            raise ValueError(
                f"metric {name!r} already registered as {existing.metric_type}, "
                f"not {metric_cls.metric_type}"
            )
        if existing.label_names != label_names:
            raise ValueError(
                f"metric {name!r} already registered with labels {list(existing.label_names)}, "
                f"not {list(label_names)}"
            )
        if buckets is not None and isinstance(existing, Histogram) and existing.buckets != buckets:
            raise ValueError(f"metric {name!r} already registered with buckets {list(existing.buckets)}")

    def render(self) -> str:
        with self._lock:
            metrics = list(self._metrics.values())
        return "".join(f"{line}\n" for metric in metrics for line in metric.render())

    def reset(self) -> None:
        with self._lock:
            self._metrics.clear()


REGISTRY = Registry()


def get_or_create_counter(name: str, help: str = "", label_names: Iterable[str] = ()) -> Counter:
    return REGISTRY.get_or_create(Counter, name, help, label_names)


def get_or_create_gauge(name: str, help: str = "", label_names: Iterable[str] = ()) -> Gauge:
    return REGISTRY.get_or_create(Gauge, name, help, label_names)


def get_or_create_histogram(
    name: str,
    help: str = "",
    label_names: Iterable[str] = (),
    buckets: Iterable[float] | None = None,
) -> Histogram:
    return REGISTRY.get_or_create(Histogram, name, help, label_names, buckets)


def render() -> str:
    return REGISTRY.render()


def reset_registry() -> None:
    REGISTRY.reset()


def incr(name: str, labels: Mapping[str, Any] | None = None, help: str = "", value: float = 1.0) -> None:
    metric = get_or_create_counter(name, help, tuple(labels or ()))
    if labels:
        metric.labels(**labels).inc(value)
    else:
        metric.inc(value)


def observe(
    name: str,
    value: float,
    labels: Mapping[str, Any] | None = None,
    help: str = "",
    buckets: Iterable[float] | None = None,
) -> None:
    metric = get_or_create_histogram(name, help, tuple(labels or ()), buckets)
    if labels:
        metric.labels(**labels).observe(value)
    else:
        metric.observe(value)


def set_gauge(
    name: str,
    value: float,
    labels: Mapping[str, Any] | None = None,
    help: str = "",
) -> None:
    metric = get_or_create_gauge(name, help, tuple(labels or ()))
    if labels:
        metric.labels(**labels).set(value)
    else:
        metric.set(value)
