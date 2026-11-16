"""M6 编排：复核队列、决定写入、影响预览、引用异常、批次历史。"""
from __future__ import annotations

import json

from ... import store
from ...errors import NotFound
from ...reasons import V_UNKNOWN_PAIR
from ..m4_pairs import service as pair_service
from ..m5_clusters import plan
from ..m5_clusters import service as cluster_service
from . import history, queue, version


def prepare(run_id: str) -> dict:
    pair_service.ensure_pairs(run_id)
    records = {record["supplier_id"]: record for record in store.list_records(run_id)}
    decisions = {item["pair_id"]: item for item in store.list_decisions(run_id)}
    pairs = store.list_pairs(run_id)
    result = plan.compute(records, pairs, decisions)
    return {"records": records, "decisions": decisions, "pairs": pairs, "plan": result}


def queue_items(run_id: str) -> list:
    prepared = prepare(run_id)
    return queue.build(prepared["pairs"], prepared["records"], prepared["decisions"],
                       prepared["plan"])


def queue_stats(run_id: str) -> dict:
    items = queue_items(run_id)
    decided = sum(1 for item in items if item["decision"])
    blocked = sum(1 for item in items if item["bridge"])
    references = queue.reference_issues(list(prepare(run_id)["records"].values()))
    return {
        "pending": len(items) - decided,
        "decided": decided,
        "total": len(items),
        "blocked_bridges": blocked,
        "references": len(references),
        "reference_codes": sorted({item["reason_code"] for item in references}),
    }


def reference_issues(run_id: str) -> list:
    return queue.reference_issues(store.list_records(run_id))


def _member_evidence(record: dict) -> dict:
    return {
        "supplier_id": record["supplier_id"],
        "raw_name": record["raw_name"],
        "tax_id": record["tax_id"],
        "city": record["city"],
        "ytd_amount": record["ytd_amount"],
        "erp": record["erp"],
        "source_file": record["source_file"],
        "line_no": record["line_no"],
    }


def _expand(view: dict, records: dict) -> dict:
    """簇视图保留成员号清单，并展开证据行：确认前看清每个成员的来源与金额。"""
    if view is None:
        return None
    expanded = dict(view)
    expanded["members_detail"] = [_member_evidence(records[sid]) for sid in view["members"]]
    return expanded


def impact_preview(run_id: str, pair_id: str) -> dict:
    """确认前的影响：该对 merge / keep 后各自带入的成员、税号、金额与既有证据。"""
    prepared = prepare(run_id)
    pair = store.get_pair(run_id, pair_id)
    if pair is None:
        raise NotFound(V_UNKNOWN_PAIR, pair_id)
    records = prepared["records"]
    left = records.get(pair["left_id"], {})
    right = records.get(pair["right_id"], {})
    decision = prepared["decisions"].get(pair_id)
    edge = prepared["plan"]["edges"].get(pair_id, {})

    merge_view = plan.preview_action(records, prepared["pairs"], prepared["decisions"],
                                     pair, "merge")
    keep_view = plan.preview_action(records, prepared["pairs"], prepared["decisions"],
                                    pair, "keep")
    for view in (merge_view, keep_view):
        view["left_cluster"] = _expand(view["left_cluster"], records)
        view["right_cluster"] = _expand(view["right_cluster"], records)
        view["cluster"] = _expand(view["cluster"], records)
    return {
        "run_id": run_id,
        "pair_id": pair_id,
        "pair": {
            "left_id": pair["left_id"],
            "right_id": pair["right_id"],
            "left_name": left.get("raw_name", ""),
            "right_name": right.get("raw_name", ""),
            "left_tax": left.get("tax_id", ""),
            "right_tax": right.get("tax_id", ""),
            "score": pair["score"],
            "verdict": pair["verdict"],
            "reason_code": pair["reason_code"],
        },
        "decision": {
            "decision": decision["decision"] if decision else "",
            "version": decision["version"] if decision else 0,
            "stale": bool(edge.get("stale")),
        },
        "bridge": edge.get("status", ""),
        "merge": merge_view,
        "keep": keep_view,
    }


def _impact_snapshot(run_id: str, pair_id: str) -> str:
    """决定落库时刻的审计快照：该对两端各自所在簇的成员、税号与金额。"""
    pair = store.get_pair(run_id, pair_id)
    records = {record["supplier_id"]: record for record in store.list_records(run_id)}
    clusters = {row["cluster_id"]: row for row in store.list_clusters(run_id)}
    cluster_of = {row["supplier_id"]: row["cluster_id"]
                  for row in store.list_cluster_members(run_id)}
    members_of = {}
    for row in store.list_cluster_members(run_id):
        members_of.setdefault(row["cluster_id"], []).append(row["supplier_id"])

    def view(supplier_id: str) -> dict:
        cluster_id = cluster_of.get(supplier_id, "")
        cluster = clusters.get(cluster_id, {})
        member_ids = sorted(members_of.get(cluster_id, [supplier_id]))
        taxes = sorted({records[sid]["tax_id"] for sid in member_ids
                        if records[sid]["tax_id"]})
        return {
            "cluster_id": cluster_id,
            "member_count": cluster.get("member_count", 1),
            "total_amount": cluster.get("total_amount", records[supplier_id]["ytd_amount"]),
            "taxes": taxes,
            "conflict": len(taxes) > 1,
            "members": member_ids,
        }

    left = view(pair["left_id"])
    right = view(pair["right_id"])
    return json.dumps({"left": left, "right": right,
                       "merged": left["cluster_id"] == right["cluster_id"]},
                      ensure_ascii=False, sort_keys=True)


def decide(run_id: str, pair_id: str, decision: str, expected_version: int,
           operator: str = "采购主数据组", expected_impact: str = "") -> dict:
    pair_service.ensure_pairs(run_id)
    record = version.decide(run_id, pair_id, decision, expected_version, operator,
                            expected_impact)
    cluster_service.ensure_clusters(run_id)
    record["impact_json"] = _impact_snapshot(run_id, pair_id)
    store.save_decision(record)
    return record


def decide_windows(run_id: str, since: str = "", until: str = "") -> dict:
    return history.window_summary(run_id, since, until)


def batch_history(limit: int = 20) -> list:
    return history.runs(limit)
