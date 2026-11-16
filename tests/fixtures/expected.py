"""样例批次的期望值：由 sample_data/ 的六个脏文件固定。

改动样例数据时这份常量必须同步更新，否则测试失败是正确信号。
"""
from decimal import Decimal

RUN_ID = "MR-8FC3B362EC8F"

INPUT_ROWS = 73
ACCEPTED = 70
QUARANTINED = 2
CONFLICTS = 1

PAIRS = 106
PAIRS_AUTO = 46
PAIRS_REVIEW = 3
PAIRS_REJECT = 57

# 约束合并后：桥接 P-00102 被 C_BRIDGE_TAX 拦截，金鼎簇自动层只有 3 成员。
CLUSTERS = 39
MERGED_MEMBERS = 31
SINGLETONS = 20
CONFLICT_CLUSTERS = 0

AMOUNT_TOTAL = Decimal("1648046.17")
DISTINCT_NORMALIZED = 39

# 复核队列 = 3 对 review 档 + 1 条被拦截的桥接边。
QUEUE_TOTAL = 4
QUEUE_BLOCKED = 1

# 金鼎电子科技：自动层税号相容部分（S-0010/S-0028/S-0071，同一税号 + 空税号）。
BRIDGE_PAIR_ID = "P-00102"
BRIDGE_BLOCK_CODE = "C_BRIDGE_TAX"
BRIDGE_LEFT_MEMBERS = ("S-0010", "S-0028", "S-0071")
BRIDGE_RIGHT_MEMBERS = ("S-0072",)
AUTO_CLUSTER_ID = "C-0009"
AUTO_CLUSTER_TOTAL = Decimal("75800.00")
AUTO_CLUSTER_SHARES = (
    ("S-0010", Decimal("68000.00")),
    ("S-0028", Decimal("5200.00")),
    ("S-0071", Decimal("2600.00")),
)

# 人工显式放行桥接后：两个税号并入同一付款对象，簇标冲突交复核。
CONFLICT_CLUSTER_ID = "C-0009"
CONFLICT_CLUSTER_REP = "S-0010"
CONFLICT_CLUSTER_TOTAL = Decimal("79200.00")
CONFLICT_CLUSTER_SHARES = (
    ("S-0010", Decimal("68000.00")),
    ("S-0028", Decimal("5200.00")),
    ("S-0071", Decimal("2600.00")),
    ("S-0072", Decimal("3400.00")),
)

# 同税号但名称仅部分相同：税额匹配优先于名称得分。
PAIR_TAX_MATCH = "P-00042"
PAIR_TAX_MATCH_SCORE = Decimal("0.772500")

# 同名不同城、税号不同：硬冲突拒绝。
PAIR_TAX_CONFLICT = "P-00015"

# 名称相同、城市不同、一侧无税号：降级为复核。
PAIR_CITY_MISMATCH = "P-00044"

# 名称近似、无税号：落在复核带。
PAIR_REVIEW = "P-00105"
PAIR_REVIEW_SCORE = Decimal("0.832778")

# 盛世文化传播与盛世文化：同税号但名称得分低，合计 22000 + 1500。
CLUSTER_SEQUENCE_TOTAL = Decimal("23500.00")
TAX_MATCH_CLUSTER_REP = "S-0012"
