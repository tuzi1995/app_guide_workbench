"""路径推导与校验。

「路径」= 画布上一条有顺序的节点链。要成为合法路径，相邻两个节点之间
必须真的有连线（source→target），否则拼出来的长图在操作逻辑上是断的。

另外提供「从节点 A 到节点 B 自动推导路径」：沿连线方向做 BFS，
找到所有 A→B 的路径，供前端一键生成路径用。
"""
from __future__ import annotations

from collections import deque
from typing import Iterable


def edge_index(edges: Iterable[dict]) -> dict[tuple[str, str], dict]:
    """(source, target) → edge，方便快速判断两节点之间有没有连线。"""
    return {(e.get("source"), e.get("target")): e for e in edges}


def validate_path(path: dict, edges: Iterable[dict]) -> list[str]:
    """校验一条路径的连线是否完整，返回问题列表（空列表 = 合法）。

    返回的问题里带节点标题，方便前端直接告诉用户「哪一步断了」。
    """
    problems: list[str] = []
    node_ids = path.get("node_ids") or []
    if not node_ids:
        return ["路径里还没有节点"]

    idx = edge_index(edges)
    for i in range(len(node_ids) - 1):
        src, dst = node_ids[i], node_ids[i + 1]
        if (src, dst) not in idx:
            problems.append(
                f"第 {i + 1} 步 → 第 {i + 2} 步 之间缺少连线（{src} → {dst}）"
            )
    # 同一个节点重复出现会导致长图重复粘贴同一张截图
    seen = set()
    for nid in node_ids:
        if nid in seen:
            problems.append(f"节点 {nid} 在路径中重复出现")
        seen.add(nid)
    return problems


def validate_graph(graph: dict) -> dict[str, list[str]]:
    """校验整个图：返回 {path_id: [问题, ...]}，只包含有问题的路径。"""
    result: dict[str, list[str]] = {}
    for path in graph.get("paths", []):
        problems = validate_path(path, graph.get("edges", []))
        if problems:
            result[path.get("id", "")] = problems
    return result


def derive_paths(graph: dict, source_id: str, target_id: str) -> list[list[str]]:
    """从 source_id 到 target_id，沿连线方向找出所有简单路径。

    用 BFS 逐层展开，限制每条路径最多 12 个节点，避免环状连线造成无限增长。
    """
    adjacency: dict[str, list[str]] = {}
    for edge in graph.get("edges", []):
        adjacency.setdefault(edge.get("source"), []).append(edge.get("target"))

    if source_id == target_id:
        return [[source_id]]

    results: list[list[str]] = []
    queue: deque[list[str]] = deque([[source_id]])
    while queue:
        chain = queue.popleft()
        if len(chain) > 12:
            continue
        last = chain[-1]
        for nxt in adjacency.get(last, []):
            if nxt in chain:      # 不走回头路
                continue
            new_chain = chain + [nxt]
            if nxt == target_id:
                results.append(new_chain)
            else:
                queue.append(new_chain)
    return results


def reachable_from(graph: dict, start_id: str) -> set[str]:
    """从某节点出发能到达的所有节点（含自己）。"""
    adjacency: dict[str, list[str]] = {}
    for edge in graph.get("edges", []):
        adjacency.setdefault(edge.get("source"), []).append(edge.get("target"))
    seen = {start_id}
    queue = deque([start_id])
    while queue:
        cur = queue.popleft()
        for nxt in adjacency.get(cur, []):
            if nxt not in seen:
                seen.add(nxt)
                queue.append(nxt)
    return seen


def next_id(existing: Iterable[str], prefix: str) -> str:
    """生成不与现有 id 冲突的新 id。"""
    used = set(existing)
    i = 1
    while f"{prefix}{i}" in used:
        i += 1
    return f"{prefix}{i}"
