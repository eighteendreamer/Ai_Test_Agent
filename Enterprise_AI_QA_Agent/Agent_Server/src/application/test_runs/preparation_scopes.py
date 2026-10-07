"""Freeze declared cleanup scope into the existing plan dependency graph."""
from graphlib import CycleError, TopologicalSorter
import logging

from src.schemas.case_planning import PreparationScopeRecord


logger = logging.getLogger(__name__)
PROTOCOL = "preparation-scope-v1"


def _ancestors(items):
    graph = {key: item.depends_on for key, item in items.items()}
    if any(parent not in items or parent == key for key, parents in graph.items() for parent in parents):
        raise ValueError("准备范围依赖不存在或引用自身")
    try:
        order = list(TopologicalSorter(graph).static_order())
    except CycleError:
        raise ValueError("准备消费或清理依赖存在循环") from None
    ancestors = {}
    for key in order:
        ancestors[key] = set(graph[key])
        for parent in graph[key]:
            ancestors[key].update(ancestors[parent])
    return ancestors


def normalize_preparation_scopes(decision, protocol):
    """Only explicit declarations change dependencies; never infer from methods."""
    if protocol == "preparation-scope-v2":
        return normalize_relation_scopes(decision)
    if any(getattr(item, "effect_schema", None) in {2, 3} for item in decision.items):
        raise ValueError("多效果执行项需要固定v2范围协议")
    cleanups = [item for item in decision.items if item.kind == "prepare" and item.cleanup_sources is not None]
    if protocol is None:
        if cleanups:
            raise ValueError("清理来源声明需要启用准备消费范围策略")
        return
    if protocol != PROTOCOL:
        raise ValueError("不支持的准备消费范围协议")
    items = {item.key: item for item in decision.items}
    if len(items) != len(decision.items):
        raise ValueError("准备范围执行项身份重复")
    ancestors = _ancestors(items)
    changes = {}
    # No upstream counterpart (checked: plan_mapping, planning_service,
    # Agent轨迹与协议鲁棒性): a declared shared source needs all consumers
    # frozen before cleanup ownership is assigned, not a runtime wait gate.
    for cleanup in cleanups:
        if len(set(cleanup.cleanup_sources)) != len(cleanup.cleanup_sources):
            raise ValueError("清理来源声明重复")
        required = []
        for source in cleanup.cleanup_sources:
            if source not in items or source == cleanup.key:
                raise ValueError("清理来源不存在或引用自身")
            if items[source].kind == "prepare" and items[source].cleanup_sources is not None:
                raise ValueError("清理来源不能引用另一个清理项")
            consumers = [key for key, item in items.items() if item.kind != "prepare"
                and (key == source or source in ancestors[key])]
            if not consumers:
                raise ValueError("清理来源没有正式消费者")
            required.extend([source, *consumers])
        parents = list(dict.fromkeys([*cleanup.depends_on, *required]))
        if parents != cleanup.depends_on:
            changes[cleanup.key] = (cleanup, parents)
    # Validate the complete candidate graph before mutating the decision.
    candidate = {key: item.model_copy(deep=True) for key, item in items.items()}
    for key, (item, parents) in changes.items():
        candidate[key].depends_on = parents
    _ancestors(candidate)
    for key, (item, parents) in changes.items():
        added = [parent for parent in parents if parent not in item.depends_on]
        item.depends_on = parents
        logger.info("preparation_cleanup_scope_frozen", extra={"cleanup_key": key,
            "source_keys": item.cleanup_sources, "added_dependency_keys": added, "protocol": protocol})


def frozen_preparation_scopes(plan, owners):
    protocol = plan.config_snapshot.policy.preparation_scope_protocol
    if plan.decision is None:
        raise ValueError("准备消费范围缺少固定计划决定")
    copy = plan.decision.model_copy(deep=True)
    normalize_preparation_scopes(copy, protocol)
    if copy != plan.decision:
        raise ValueError("固定清理范围与正式消费者依赖不一致")
    if protocol is None:
        return None
    items = {item.key: item for item in copy.items}
    ancestors = _ancestors(items)
    cleanups = {}
    for item in copy.items:
        if (item.kind == "prepare" or protocol == "preparation-scope-v2") and getattr(item, "cleanup_sources", None) is not None:
            for source in item.cleanup_sources:
                cleanups.setdefault(source, []).append(item.key)
    return {source: PreparationScopeRecord(source_key=source, source_owner_key=owners[source],
        consumer_keys=[key for key, item in items.items() if item.kind != "prepare"
            and (key == source or source in ancestors[key])], cleanup_keys=keys)
        for source, keys in cleanups.items()}


def normalize_relation_scopes(decision):
    items = {item.key: item for item in decision.items}
    if len(items) != len(decision.items) or any(getattr(item, "effect_schema", None) not in {2, 3} for item in items.values()):
        raise ValueError("多效果范围缺少唯一的v2固定执行项")
    ancestors = _ancestors(items)
    candidate = {key: item.model_copy(deep=True) for key, item in items.items()}
    for cleanup in candidate.values():
        sources = cleanup.cleanup_sources
        if sources is None:
            continue
        if len(set(sources)) != len(sources) or any(source not in items for source in sources):
            raise ValueError("多效果清理来源重复或不存在")
        required = []
        for source in sources:
            consumers = [key for key, item in items.items() if item.kind != "prepare"
                and (key == source or source in ancestors[key])]
            if not consumers:
                raise ValueError("多效果清理来源没有正式消费者")
            # No upstream counterpart (checked: v1 scopes and actual formal
            # cleanup failure): same-item effects are not item dependency edges.
            required.extend(key for key in [source, *consumers] if key != cleanup.key)
        cleanup.depends_on = list(dict.fromkeys([*cleanup.depends_on, *required]))
    _ancestors(candidate)
    for key, item in items.items():
        parents = candidate[key].depends_on
        if parents != item.depends_on:
            logger.info("case_effect_relation_scope_frozen", extra={"item_key": key,
                "source_keys": item.cleanup_sources,
                "added_dependency_keys": [parent for parent in parents if parent not in item.depends_on]})
            item.depends_on = parents
