"""复核决定的乐观锁与依据快照：版本不符拒绝，依据失效的决定不再生效。"""
from __future__ import annotations

from ... import store
from ...errors import Conflict, DomainError, NotFound
from ...manifest import utc_iso, utc_now
from ...reasons import P_TAX_CONFLICT, V_BAD_DECISION, V_STALE_DECISION, V_UNKNOWN_PAIR
from ..m5_clusters import plan

ALLOWED = ("merge", "keep")


def current_version(run_id: str, pair_id: str) -> int:
    decision = store.get_decision(run_id, pair_id)
    return decision["version"] if decision else 0


def decide(run_id: str, pair_id: str, decision: str, expected_version: int,
           operator: str = "采购主数据组", expected_impact: str = "") -> dict:
    if decision not in ALLOWED:
        raise DomainError(V_BAD_DECISION, decision)
    pair = store.get_pair(run_id, pair_id)
    if pair is None:
        raise NotFound(V_UNKNOWN_PAIR, pair_id)
    if decision == "merge" and pair["reason_code"] == P_TAX_CONFLICT:
        raise DomainError(V_BAD_DECISION, "税号冲突不可合并")
    current = current_version(run_id, pair_id)
    if expected_version != current:
        raise Conflict(V_STALE_DECISION, f"当前版本 {current}")

    records = {record["supplier_id"]: record for record in store.list_records(run_id)}
    left = records.get(pair["left_id"], {})
    right = records.get(pair["right_id"], {})
    basis_json = plan.basis_json_of(pair, left, right)

    if expected_impact:
        decisions = {item["pair_id"]: item for item in store.list_decisions(run_id)}
        preview = plan.preview_action(records, store.list_pairs(run_id), decisions,
                                      pair, decision)
        if preview["impact_hash"] != expected_impact:
            raise Conflict(V_STALE_DECISION, "影响已变化，请重新预览后再确认")

    record = {
        "run_id": run_id,
        "pair_id": pair_id,
        "decision": decision,
        "expected_version": expected_version,
        "version": current + 1,
        "operator": operator,
        "decided_at": utc_iso(utc_now()),
        "basis_json": basis_json,
        "impact_json": "",
    }
    store.save_decision(record)
    return record
