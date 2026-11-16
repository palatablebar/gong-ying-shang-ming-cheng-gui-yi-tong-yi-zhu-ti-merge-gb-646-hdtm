"""约束合并计划：桥接准入、约束传播、决定失效与影响预览。

桥接关系何时可以进入主数据（本模块的唯一权威规则）：

1. 自动边（verdict=auto 且无人工决定）：仅在税号相容且无约束横跨时进入。
   - 税号相容：并入后簇内非空税号不超过一个；否则拦截记 C_BRIDGE_TAX。
   - 无约束横跨：两侧分量之间不存在人工 keep 或待核 review 边；否则记 C_BRIDGE_HOLD。
2. 人工 merge 边：可覆盖税号拦截（簇标 C_CLUSTER_CONFLICT 交复核），
   但不能绕过人工 keep 或待核 review 构成的 cannot-link 约束。
3. 人工 keep 与待核 review 是 cannot-link 约束：两端不得经任何路径同簇，
   已拒绝或待核的关系不会从另一条路径回到主数据。
4. 决定带依据快照（basis）：上游重算使依据不符时决定失效，不参与合并，
   需复核员按新依据重新确认。

整个计划只依赖 (records, pairs, decisions) 三份输入，内部按 pair_id 排序，
是确定性纯函数：同一批输入重算，成员与金额必然一致。
"""
from __future__ import annotations

import hashlib
import json
from decimal import Decimal

from ...manifest import canonical_json
from ...reasons import C_BRIDGE_HOLD, C_BRIDGE_TAX

# 边角色
ROLE_NONE = "none"            # 不参与合并（低分拒绝等）
ROLE_AUTO = "auto"            # 自动合并边
ROLE_MERGE = "merge"          # 人工确认合并边
ROLE_CONSTRAINT = "constraint"  # cannot-link 约束边（人工 keep / 待核 review）

# 边状态
APPLIED = "applied"           # 已并入（或本已同簇）
BLOCKED_TAX = "blocked_tax"   # 税号拦截：并入会产生多税号簇
BLOCKED_HOLD = "blocked_hold"  # 约束拦截：对端存在 keep / 待核约束
PENDING = "pending"           # 约束边自身，等待人工决定
SKIPPED = "skipped"           # 无角色边

STATUS_CODES = {BLOCKED_TAX: C_BRIDGE_TAX, BLOCKED_HOLD: C_BRIDGE_HOLD}


# ---- 决定依据快照与失效 ----

def basis_of(pair: dict, left: dict, right: dict) -> dict:
    """决定生效的前提：候选对结论与左右记录的关键判定字段。"""
    return {
        "verdict": pair["verdict"],
        "reason_code": pair["reason_code"],
        "score": str(pair["score"]),
        "left": {"norm_name": left.get("norm_name", ""), "tax_id": left.get("tax_id", ""),
                 "city": left.get("city", "")},
        "right": {"norm_name": right.get("norm_name", ""), "tax_id": right.get("tax_id", ""),
                  "city": right.get("city", "")},
    }


def basis_json_of(pair: dict, left: dict, right: dict) -> str:
    return canonical_json(basis_of(pair, left, right))


def decision_fresh(pair: dict, left: dict, right: dict, decision: dict) -> bool:
    """决定依据与当前上游一致才生效；无法校验（无快照）的一律失效。"""
    stored = decision.get("basis_json") or ""
    if not stored:
        return False
    try:
        snapshot = json.loads(stored)
    except ValueError:
        return False
    return snapshot == basis_of(pair, left, right)


# ---- 约束分量 ----

class _Components:
    """并查集 + 每分量非空税号集 + 每分量的 cannot-link 对端集。"""

    def __init__(self, records: dict):
        self.parent = {sid: sid for sid in records}
        self.taxes = {
            sid: ({records[sid]["tax_id"]} if records[sid].get("tax_id") else set())
            for sid in records
        }
        self.links = {sid: set() for sid in records}

    def find(self, item: str) -> str:
        root = item
        while self.parent[root] != root:
            root = self.parent[root]
        while self.parent[item] != root:
            self.parent[item], item = root, self.parent[item]
        return root

    def add_constraint(self, left: str, right: str) -> None:
        left_root, right_root = self.find(left), self.find(right)
        if left_root == right_root:
            return
        self.links[left_root].add(right_root)
        self.links[right_root].add(left_root)

    def constrained(self, left_root: str, right_root: str) -> bool:
        return right_root in self.links[left_root]

    def union(self, left: str, right: str) -> str:
        left_root, right_root = self.find(left), self.find(right)
        if left_root == right_root:
            return left_root
        if right_root < left_root:
            left_root, right_root = right_root, left_root
        self.parent[right_root] = left_root
        self.taxes[left_root] |= self.taxes.pop(right_root)
        merged = (self.links[left_root] | self.links[right_root]) - {left_root, right_root}
        for other in merged:
            self.links[other].discard(right_root)
            self.links[other].add(left_root)
        self.links[left_root] = merged
        self.links[right_root] = set()
        return left_root


# ---- 边角色分类 ----

def classify(pairs, records: dict, decisions: dict) -> dict:
    """每条候选对的角色；同时给出决定失效标记，供队列与页面展示。"""
    roles = {}
    for pair in pairs:
        left = records.get(pair["left_id"], {})
        right = records.get(pair["right_id"], {})
        decision = decisions.get(pair["pair_id"])
        fresh = bool(decision) and decision_fresh(pair, left, right, decision)
        effective = decision if fresh else None
        if effective and effective["decision"] == "keep":
            role = ROLE_CONSTRAINT
        elif effective and effective["decision"] == "merge":
            role = ROLE_MERGE
        elif pair["verdict"] == "review":
            role = ROLE_CONSTRAINT
        elif pair["verdict"] == "auto":
            role = ROLE_AUTO
        else:
            role = ROLE_NONE
        roles[pair["pair_id"]] = {
            "role": role,
            "decision": effective["decision"] if effective else "",
            "stale": bool(decision) and not fresh,
        }
    return roles


# ---- 主计划 ----

def compute(records: dict, pairs, decisions: dict) -> dict:
    """确定性约束合并。records: {supplier_id: row}；decisions: {pair_id: row}。"""
    ordered = sorted(pairs, key=lambda item: item["pair_id"])
    roles = classify(ordered, records, decisions)
    comp = _Components(records)

    for pair in ordered:
        if roles[pair["pair_id"]]["role"] == ROLE_CONSTRAINT:
            comp.add_constraint(pair["left_id"], pair["right_id"])

    edges, blocked, constraints = {}, [], []
    for pair in ordered:
        pair_id = pair["pair_id"]
        info = dict(roles[pair_id])
        role = info["role"]
        if role == ROLE_CONSTRAINT:
            constraints.append(pair_id)
            edges[pair_id] = {**info, "status": PENDING}
            continue
        if role not in (ROLE_AUTO, ROLE_MERGE):
            edges[pair_id] = {**info, "status": SKIPPED}
            continue
        left_root, right_root = comp.find(pair["left_id"]), comp.find(pair["right_id"])
        if left_root == right_root:
            edges[pair_id] = {**info, "status": APPLIED}
            continue
        if comp.constrained(left_root, right_root):
            edges[pair_id] = {**info, "status": BLOCKED_HOLD}
            blocked.append({"pair_id": pair_id, "left_id": pair["left_id"],
                            "right_id": pair["right_id"], "status": BLOCKED_HOLD,
                            "code": C_BRIDGE_HOLD})
            continue
        if role == ROLE_AUTO and len(comp.taxes[left_root] | comp.taxes[right_root]) > 1:
            edges[pair_id] = {**info, "status": BLOCKED_TAX}
            blocked.append({"pair_id": pair_id, "left_id": pair["left_id"],
                            "right_id": pair["right_id"], "status": BLOCKED_TAX,
                            "code": C_BRIDGE_TAX})
            continue
        comp.union(pair["left_id"], pair["right_id"])
        edges[pair_id] = {**info, "status": APPLIED}

    root_of = {sid: comp.find(sid) for sid in records}
    members_of = {}
    for sid, root in root_of.items():
        members_of.setdefault(root, []).append(sid)
    groups = [sorted(members) for _, members in sorted(members_of.items())]
    for item in blocked:
        item["left_members"] = sorted(members_of[root_of[item["left_id"]]])
        item["right_members"] = sorted(members_of[root_of[item["right_id"]]])
    return {
        "groups": groups,
        "edges": edges,
        "blocked": blocked,
        "constraints": constraints,
        "members_of": {root: sorted(members) for root, members in members_of.items()},
        "root_of": root_of,
    }


# ---- 影响预览 ----

def impact_hash(views) -> str:
    """该对左右两端各自所在簇的指纹：成员、税号、金额三者共同绑定。

    预览与正式决定共用同一公式；上游任何使成员/税号/金额变化的更新
    都会让指纹改变，旧预览无法用于新确认。
    """
    payload = canonical_json([
        {"members": sorted(view["members"]), "taxes": sorted(view["taxes"]),
         "total_amount": str(view["total_amount"])}
        for view in views
    ])
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def _cluster_view(member_ids, records: dict) -> dict:
    taxes = sorted({records[sid].get("tax_id", "") for sid in member_ids
                    if records[sid].get("tax_id")})
    total = sum((Decimal(records[sid]["ytd_amount"]) for sid in member_ids), Decimal("0"))
    return {
        "members": sorted(member_ids),
        "member_count": len(member_ids),
        "taxes": taxes,
        "conflict": len(taxes) > 1,
        "total_amount": str(total),
    }


def preview_action(records: dict, pairs, decisions: dict, pair: dict, action: str) -> dict:
    """假设性重算：把该对视为 action（merge/keep）后重跑同一计划。

    预览与正式决定走同一个 compute，因此复核员看到的成员、税号、金额
    就是确认后实际生效的结果。
    """
    left_id, right_id = pair["left_id"], pair["right_id"]
    trial_decision = {
        "pair_id": pair["pair_id"],
        "decision": action,
        "basis_json": basis_json_of(pair, records[left_id], records[right_id]),
    }
    trial = {**decisions, pair["pair_id"]: trial_decision}
    result = compute(records, pairs, trial)
    root_of = result["root_of"]
    members_of = result["members_of"]
    left_root, right_root = root_of[left_id], root_of[right_id]
    left_members = members_of[left_root]
    right_members = members_of[right_root]
    merged = left_root == right_root

    edge = result["edges"][pair["pair_id"]]
    block_pairs = []
    if not merged and action == "merge":
        left_set, right_set = set(left_members), set(right_members)
        for other in pairs:
            if result["edges"].get(other["pair_id"], {}).get("role") != ROLE_CONSTRAINT:
                continue
            if ((other["left_id"] in left_set and other["right_id"] in right_set)
                    or (other["left_id"] in right_set and other["right_id"] in left_set)):
                block_pairs.append(other["pair_id"])

    cluster_members = sorted(set(left_members) | set(right_members)) if merged else []
    left_view = _cluster_view(left_members, records)
    right_view = _cluster_view(right_members, records)
    evidence = []
    if merged:
        inside = set(cluster_members)
        for other in pairs:
            info = result["edges"].get(other["pair_id"], {})
            if info.get("status") != APPLIED:
                continue
            if other["left_id"] in inside and other["right_id"] in inside:
                evidence.append({
                    "pair_id": other["pair_id"],
                    "left_id": other["left_id"],
                    "right_id": other["right_id"],
                    "role": info["role"],
                    "score": other["score"],
                })

    sides = [left_view] if merged else [left_view, right_view]
    return {
        "action": action,
        "merged": merged,
        "applied": edge["status"] == APPLIED,
        "block_code": STATUS_CODES.get(edge["status"], ""),
        "block_pairs": sorted(block_pairs),
        "left_cluster": left_view,
        "right_cluster": right_view,
        "cluster": _cluster_view(cluster_members, records) if merged else None,
        "evidence": sorted(evidence, key=lambda item: item["pair_id"]),
        "impact_hash": impact_hash(sides),
    }
