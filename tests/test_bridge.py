"""桥接准入：税号相容才自动进入；拒绝/待核关系不得从另一路径回到主数据；
上游更新使旧决定失效必须重算；预览与确认同源，重跑结果一致。"""
from __future__ import annotations

import json
from decimal import Decimal

import pytest

from app import store
from app.errors import Conflict
from app.modules.m5_clusters import plan
from app.modules.m5_clusters import service as cluster_service
from app.modules.m6_reviews import service as review_service
from tests.fixtures import expected as EXP


def _record(sid, tax="", amount="100.00", name=""):
    return {
        "supplier_id": sid, "supplier_no": sid, "raw_name": name or f"供应商{sid}",
        "norm_name": name or f"供应商{sid}", "norm_codes": "[]", "tax_id": tax,
        "city": "南京", "bank_account": "", "erp": "ERP-A", "ref_master_id": "",
        "ytd_amount": amount, "flags": "[]", "source_file": "t.csv", "line_no": 1,
    }


def _pair(pid, left, right, verdict="auto", reason="P_AUTO", score="0.950000"):
    return {"pair_id": pid, "left_id": left, "right_id": right, "dice": "0.900000",
            "ratio": "0.900000", "score": score, "verdict": verdict,
            "reason_code": reason, "detail": ""}


def _decision(pair, records, decision):
    left, right = records[pair["left_id"]], records[pair["right_id"]]
    return {"pair_id": pair["pair_id"], "decision": decision,
            "basis_json": plan.basis_json_of(pair, left, right)}


def _records(*rows):
    return {row["supplier_id"]: row for row in rows}


# ---- 准入规则（合成数据，纯函数）----

def test_tax_compatible_bridge_auto_enters():
    """同一税号（或一侧无税号）的抬头链照常自动合并，不被误拦。"""
    records = _records(_record("S-A", "T1"), _record("S-B", ""), _record("S-C", "T1"))
    pairs = [_pair("P-1", "S-A", "S-B"), _pair("P-2", "S-B", "S-C")]
    result = plan.compute(records, pairs, {})
    assert result["groups"] == [["S-A", "S-B", "S-C"]]
    assert result["blocked"] == []


def test_tax_conflict_bridge_blocked():
    """两条关系各自可确认，连起来会并入两个税号：自动层拦截桥接边。"""
    records = _records(_record("S-A", "T1"), _record("S-B", ""), _record("S-C", "T2"))
    pairs = [_pair("P-1", "S-A", "S-B"), _pair("P-2", "S-B", "S-C")]
    result = plan.compute(records, pairs, {})
    assert result["groups"] == [["S-A", "S-B"], ["S-C"]]
    assert [item["pair_id"] for item in result["blocked"]] == ["P-2"]
    assert result["blocked"][0]["code"] == "C_BRIDGE_TAX"
    assert result["blocked"][0]["left_members"] == ["S-A", "S-B"]
    assert result["blocked"][0]["right_members"] == ["S-C"]


def test_manual_merge_releases_tax_bridge():
    """人工显式确认后桥接可以进入：簇标冲突，责任在确认人。"""
    records = _records(_record("S-A", "T1"), _record("S-B", ""), _record("S-C", "T2"))
    pairs = [_pair("P-1", "S-A", "S-B"), _pair("P-2", "S-B", "S-C")]
    decisions = {"P-2": _decision(pairs[1], records, "merge")}
    result = plan.compute(records, pairs, decisions)
    assert result["groups"] == [["S-A", "S-B", "S-C"]]
    assert result["edges"]["P-2"]["status"] == plan.APPLIED


def test_pending_review_blocks_alternate_path():
    """待核关系不得从另一条路径回到主数据：A-B 待核时 C 不得桥接两端。"""
    records = _records(_record("S-A", "T1"), _record("S-B", "T1"), _record("S-C", ""))
    pairs = [_pair("P-1", "S-A", "S-B", verdict="review", reason="P_REVIEW",
                   score="0.850000"),
             _pair("P-2", "S-A", "S-C"), _pair("P-3", "S-C", "S-B")]
    result = plan.compute(records, pairs, {})
    assert result["groups"] == [["S-A", "S-C"], ["S-B"]]
    assert result["edges"]["P-3"]["status"] == plan.BLOCKED_HOLD
    assert result["blocked"][0]["code"] == "C_BRIDGE_HOLD"


def test_keep_blocks_alternate_path_and_merge_cannot_bypass():
    """已拒绝关系不得从另一条路径回到主数据，人工 merge 也绕不过人工 keep。"""
    records = _records(_record("S-A", "T1"), _record("S-B", "T1"), _record("S-C", ""))
    pairs = [_pair("P-1", "S-A", "S-B", verdict="review", reason="P_REVIEW",
                   score="0.850000"),
             _pair("P-2", "S-A", "S-C"), _pair("P-3", "S-C", "S-B")]
    decisions = {"P-1": _decision(pairs[0], records, "keep"),
                 "P-3": _decision(pairs[2], records, "merge")}
    result = plan.compute(records, pairs, decisions)
    assert result["groups"] == [["S-A", "S-C"], ["S-B"]]
    assert result["edges"]["P-3"]["status"] == plan.BLOCKED_HOLD


def test_merge_decision_on_constraint_releases_path():
    """待核约束被人工 merge 解除后，传递路径照常成立。"""
    records = _records(_record("S-A", "T1"), _record("S-B", "T1"), _record("S-C", ""))
    pairs = [_pair("P-1", "S-A", "S-B", verdict="review", reason="P_REVIEW",
                   score="0.850000"),
             _pair("P-2", "S-A", "S-C"), _pair("P-3", "S-C", "S-B")]
    decisions = {"P-1": _decision(pairs[0], records, "merge")}
    result = plan.compute(records, pairs, decisions)
    assert result["groups"] == [["S-A", "S-B", "S-C"]]


def test_plan_deterministic_under_shuffle():
    """同一批输入无论候选对顺序如何，成员划分与拦截清单一致。"""
    records = _records(_record("S-A", "T1"), _record("S-B", ""), _record("S-C", "T2"),
                       _record("S-D", ""))
    pairs = [_pair("P-1", "S-A", "S-B"), _pair("P-2", "S-B", "S-C"),
             _pair("P-3", "S-C", "S-D")]
    first = plan.compute(records, pairs, {})
    second = plan.compute(records, list(reversed(pairs)), {})
    assert first["groups"] == second["groups"]
    assert [item["pair_id"] for item in first["blocked"]] == [
        item["pair_id"] for item in second["blocked"]]


def test_stale_decision_not_effective():
    """依据快照不符的决定失效：视同未决定，约束与合并都不再生效。"""
    records = _records(_record("S-A", "T1"), _record("S-B", ""), _record("S-C", "T2"))
    pairs = [_pair("P-1", "S-A", "S-B"), _pair("P-2", "S-B", "S-C")]
    stale = {"pair_id": "P-2", "decision": "merge", "basis_json": ""}
    result = plan.compute(records, pairs, {"P-2": stale})
    assert result["edges"]["P-2"]["stale"] is True
    assert result["edges"]["P-2"]["status"] == plan.BLOCKED_TAX
    assert result["groups"] == [["S-A", "S-B"], ["S-C"]]


def test_preview_matches_decided_outcome():
    """预览 = 假设性重算：预览的簇与正式决定后重算的簇完全一致。"""
    records = _records(_record("S-A", "T1", "10.00"), _record("S-B", "", "20.00"),
                       _record("S-C", "T2", "30.00"))
    pairs = [_pair("P-1", "S-A", "S-B"), _pair("P-2", "S-B", "S-C")]
    preview = plan.preview_action(records, pairs, {}, pairs[1], "merge")
    assert preview["cluster"]["members"] == ["S-A", "S-B", "S-C"]
    assert preview["cluster"]["taxes"] == ["T1", "T2"]
    assert preview["cluster"]["total_amount"] == "60.00"
    decisions = {"P-2": _decision(pairs[1], records, "merge")}
    result = plan.compute(records, pairs, decisions)
    assert result["groups"] == [preview["cluster"]["members"]]


# ---- 样例批次集成 ----

def test_bridge_blocked_in_queue(run):
    """被拦截的桥接边进入复核队列，并标出两侧各自已确认的成员数。"""
    items = {item["pair_id"]: item for item in review_service.queue_items(run["run_id"])}
    item = items[EXP.BRIDGE_PAIR_ID]
    assert item["bridge"] == plan.BLOCKED_TAX
    assert item["block_code"] == EXP.BRIDGE_BLOCK_CODE
    assert (item["left_span"], item["right_span"]) == (3, 1)


def test_impact_preview_endpoint(client):
    """确认前看清：merge 会带入的成员、税号、金额与既有证据边。"""
    client.post("/api/suppliers/import", json={})
    response = client.get(f"/api/reviews/{EXP.BRIDGE_PAIR_ID}/impact")
    assert response.status_code == 200
    body = response.json()
    merge = body["merge"]
    assert merge["merged"] is True
    assert merge["cluster"]["member_count"] == 4
    assert merge["cluster"]["taxes"] == ["91320105MA1N2P3Q4", "91320105MA9Z8Y7X6"]
    assert merge["cluster"]["conflict"] is True
    assert Decimal(merge["cluster"]["total_amount"]) == EXP.CONFLICT_CLUSTER_TOTAL
    detail_ids = [m["supplier_id"] for m in merge["cluster"]["members_detail"]]
    assert detail_ids == ["S-0010", "S-0028", "S-0071", "S-0072"]
    evidence_pairs = {edge["pair_id"] for edge in merge["evidence"]}
    assert EXP.BRIDGE_PAIR_ID in evidence_pairs
    assert merge["impact_hash"]
    keep = body["keep"]
    assert keep["merged"] is False
    assert keep["left_cluster"]["member_count"] == 3
    assert keep["right_cluster"]["member_count"] == 1


def test_decide_with_impact_hash(client):
    """带影响指纹确认：看到的成员、税号、金额即生效结果。"""
    client.post("/api/suppliers/import", json={})
    preview = client.get(f"/api/reviews/{EXP.BRIDGE_PAIR_ID}/impact").json()
    response = client.post(
        f"/api/reviews/{EXP.BRIDGE_PAIR_ID}/decision",
        json={"decision": "merge", "expected_version": 0,
              "expected_impact": preview["merge"]["impact_hash"]})
    assert response.status_code == 200
    summary = client.get("/api/clusters").json()["summary"]
    assert summary["conflict_clusters"] == 1
    assert summary["clusters"] == EXP.CLUSTERS - 1


def test_stale_impact_rejected(client):
    """预览后上游发生变化，旧影响指纹不得再用于确认。"""
    client.post("/api/suppliers/import", json={})
    preview = client.get(f"/api/reviews/{EXP.BRIDGE_PAIR_ID}/impact").json()
    run_id = preview["run_id"]
    with store.connect() as conn:
        conn.execute("UPDATE records SET ytd_amount = ? WHERE run_id = ? AND supplier_id = ?",
                     ("3400.01", run_id, "S-0072"))
        conn.commit()
    response = client.post(
        f"/api/reviews/{EXP.BRIDGE_PAIR_ID}/decision",
        json={"decision": "merge", "expected_version": 0,
              "expected_impact": preview["merge"]["impact_hash"]})
    assert response.status_code == 409
    assert response.json()["reason_code"] == "V_STALE_DECISION"


def test_upstream_change_invalidates_decision(run):
    """上游更新使旧判断失效：税号改正后旧 merge 决定不再生效，簇立即重算。"""
    run_id = run["run_id"]
    review_service.decide(run_id, EXP.BRIDGE_PAIR_ID, "merge", 0)
    summary = cluster_service.ensure_clusters(run_id)
    assert summary["conflict_clusters"] == 1

    with store.connect() as conn:
        conn.execute("UPDATE records SET tax_id = ? WHERE run_id = ? AND supplier_id = ?",
                     ("91320105MA1N2P3Q4", run_id, "S-0072"))
        conn.commit()
    summary = cluster_service.ensure_clusters(run_id)
    assert summary["conflict_clusters"] == 0
    assert summary["clusters"] == EXP.CLUSTERS - 1
    decision = store.get_decision(run_id, EXP.BRIDGE_PAIR_ID)
    assert decision["version"] == 1
    items = {item["pair_id"]: item for item in review_service.queue_items(run_id)}
    assert EXP.BRIDGE_PAIR_ID not in items


def test_stale_decision_visible_in_queue(run):
    """决定失效后回到待核状态：复核员必须按新依据重新确认。"""
    run_id = run["run_id"]
    review_service.decide(run_id, EXP.PAIR_REVIEW, "keep", 0)
    stats = review_service.queue_stats(run_id)
    assert stats["decided"] == 1

    pair = store.get_pair(run_id, EXP.PAIR_REVIEW)
    with store.connect() as conn:
        conn.execute("UPDATE records SET tax_id = ? WHERE run_id = ? AND supplier_id = ?",
                     ("91320105MA0NEW000", run_id, pair["left_id"]))
        conn.commit()
    items = {item["pair_id"]: item for item in review_service.queue_items(run_id)}
    item = items[EXP.PAIR_REVIEW]
    assert item["stale"] is True
    assert item["decision"] == ""
    stats = review_service.queue_stats(run_id)
    assert stats["decided"] == 0
    assert stats["pending"] == EXP.QUEUE_TOTAL


def test_rerun_same_batch_same_outcome(run):
    """同一批重跑：成员、金额与拦截清单逐次一致。"""
    run_id = run["run_id"]
    review_service.decide(run_id, EXP.BRIDGE_PAIR_ID, "merge", 0)
    first_members = store.list_cluster_members(run_id)
    first_clusters = store.list_clusters(run_id)
    first_blocked = cluster_service.build_plan(run_id)["blocked"]
    cluster_service.ensure_clusters(run_id)
    cluster_service.ensure_clusters(run_id)
    assert store.list_cluster_members(run_id) == first_members
    assert store.list_clusters(run_id) == first_clusters
    assert cluster_service.build_plan(run_id)["blocked"] == first_blocked


def test_page_export_review_consistent(client):
    """页面、复核记录与导出中的主体与金额一致。"""
    client.post("/api/suppliers/import", json={})
    preview = client.get(f"/api/reviews/{EXP.BRIDGE_PAIR_ID}/impact").json()
    decided = client.post(
        f"/api/reviews/{EXP.BRIDGE_PAIR_ID}/decision",
        json={"decision": "merge", "expected_version": 0,
              "expected_impact": preview["merge"]["impact_hash"]})
    assert decided.status_code == 200

    master = client.get("/api/export/master.csv")
    assert EXP.CONFLICT_CLUSTER_ID in master.text
    assert "79200.00" in master.text

    summary = client.get("/api/clusters").json()["summary"]
    assert summary["clusters"] == EXP.CLUSTERS - 1
    assert summary["amount_total"] == str(EXP.AMOUNT_TOTAL)

    impact = json.loads(decided.json()["decision"]["impact_json"])
    assert impact["left"]["total_amount"] == "79200.00"
    assert impact["left"]["member_count"] == 4

    page = client.get("/reviews")
    assert page.status_code == 200
    assert "79200.00" in page.text or "79,200.00" in page.text
