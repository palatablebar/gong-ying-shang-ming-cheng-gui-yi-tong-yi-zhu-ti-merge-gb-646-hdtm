# 运行手册 供应商主数据台账

## 启动

```bash
docker compose up -d --build
curl -s http://127.0.0.1:18205/api/health
```

首次打开任意页面会按内置样例自动采信一次，随后逐级计算归一名、分块、候选对与合并簇。

## 重建数据库

```bash
docker compose down -v
docker compose up -d --build
```

`-v` 会删除命名卷 `merge-data`，批次表与复核决定一并清空。

## 重跑与幂等

- `POST /api/suppliers/import` 带 `force: true` 时按同一批文件重算。
- 批次号由输入摘要（按文件名排序的 sha256 拼接）与规则版本共同决定，同一批文件得到同一个 `run_id`；
  命中已有批次时直接复用结果，不新增记录。
- 复核决定写入后，簇与金额会立即重算，不需要手工触发。
- 上游数据变化（重采信、修数）使决定的依据快照不符时，旧决定自动失效：
  不参与合并、不构成约束，复核队列重新出现该项并标"已失效"。

## 桥接复核

- 自动合并只放行税号相容的桥接；会产生多税号簇的桥接边被拦截并进复核队列。
- `GET /api/reviews/{pair_id}/impact` 预览 merge / keep 各自带入的成员、税号、
  金额与既有证据边；页面"影响"按钮展示同一数据。
- 确认时带 `expected_impact` 指纹可保证"看到的影响即生效结果"，中间状态变化即 409。

## 常用请求

```bash
curl -s -X POST http://127.0.0.1:18205/api/suppliers/import -H 'Content-Type: application/json' -d '{}'
curl -s http://127.0.0.1:18205/api/reconcile
curl -s http://127.0.0.1:18205/api/export/master.csv -o master.csv
curl -s http://127.0.0.1:18205/api/export/manifest.json -o manifest.json
curl -s http://127.0.0.1:18205/api/reviews/P-00102/impact
```

判定一张复核单：

```bash
curl -s -X POST http://127.0.0.1:18205/api/reviews/P-00105/decision \
  -H 'Content-Type: application/json' \
  -d '{"decision":"merge","expected_version":0}'
```

## 失败码处理

| 原因码 | 处理 |
|---|---|
| `E_FILE_MISSING` | 检查 `sample_data/` 下的文件名与大小写 |
| `E_MISSING_COLUMN` | 表头缺必填列，按列契约补列后重试 |
| `E_DECODE_FAILED` | 文件既非 UTF-8 也非 GB18030，需转码 |
| `Q_ROW_SHORT` / `Q_ROW_LONG` | 该行进隔离区，修正分隔符后重跑 |
| `V_STALE_DECISION` | 页面停留过久或影响已变化，刷新后重新预览再提交 |
| `V_BAD_DECISION` | 税号冲突的候选对不允许合并 |
| `V_UNKNOWN_PAIR` | 候选对编号不属于当前批次，刷新页面 |
| `V_DECISION_STALE` | 旧决定依据已失效（仅页面标记），按当前数据重新判定即可 |
| `C_BRIDGE_TAX` | 桥接并入会产生多税号簇，需在复核队列显式确认后才进入 |
| `C_BRIDGE_HOLD` | 桥接对端存在人工保留或待复核约束，先判定那条约束对 |

## 测试

```bash
docker compose exec -T app python -m pytest -q
```
