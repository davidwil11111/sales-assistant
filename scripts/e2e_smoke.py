#!/usr/bin/env python3
"""端到端冒烟：使用真实 wacli.db（或 data/backups 备份）跑 extract → detail → write → save-report。

数据写入隔离目录，不污染工作区 data/clients.json。

用法:
  python3 scripts/e2e_smoke.py
  python3 scripts/e2e_smoke.py --db data/backups/wacli_backup_20260611_0902.db
  SARAH_DB_PATH=/path/to/wacli.db python3 scripts/e2e_smoke.py
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)


def find_db(explicit: str | None) -> Path:
    if explicit:
        p = Path(explicit).expanduser()
        if not p.exists():
            raise SystemExit(f"指定数据库不存在: {p}")
        return p
    env = os.environ.get("SARAH_DB_PATH")
    if env:
        p = Path(env).expanduser()
        if p.exists():
            return p
    cfg_path = ROOT / "config.json"
    if cfg_path.exists():
        cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
        p = Path(cfg.get("db_path", "")).expanduser()
        if p.exists():
            return p
    backups = sorted((ROOT / "data" / "backups").glob("wacli_backup_*.db"), reverse=True)
    if backups:
        return backups[0]
    # edgar recovery etc.
    for name in ("edgar_recovery.db",):
        p = ROOT / "data" / "backups" / name
        if p.exists():
            return p
    raise SystemExit(
        "未找到 wacli.db。请用 --db 指定，或设置 SARAH_DB_PATH，"
        "或把备份放到 data/backups/wacli_backup_*.db"
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Sarah E2E smoke with real DB")
    parser.add_argument("--db", help="wacli.db 路径")
    parser.add_argument("--keep", action="store_true", help="保留临时目录")
    args = parser.parse_args()

    db_path = find_db(args.db)
    print(f">>> DB: {db_path} ({db_path.stat().st_size // 1024} KB)")

    tmp = Path(tempfile.mkdtemp(prefix="sarah_e2e_"))
    data_dir = tmp / "data"
    data_dir.mkdir()
    (data_dir / "backups").mkdir()

    # 隔离 config：指向真实库 + 临时 data
    base_cfg = json.loads((ROOT / "config.json").read_text(encoding="utf-8"))
    base_cfg["db_path"] = str(db_path.resolve())
    base_cfg["owner_phone"] = base_cfg.get("owner_phone") or "8619836278909"
    base_cfg["owner_name"] = base_cfg.get("owner_name") or "Owner"
    # 给一点行业中温词，验证配置路径
    ind = base_cfg.setdefault("industry", {})
    if not ind.get("temperature_demand_signals"):
        ind["temperature_demand_signals"] = ["crane", "ton", "span", "hoist"]
    cfg_path = tmp / "config.json"
    cfg_path.write_text(json.dumps(base_cfg, ensure_ascii=False, indent=2), encoding="utf-8")

    import extract as ex

    # 重定向所有数据路径
    ex.SCRIPT_DIR = tmp
    ex.CONFIG = cfg_path
    ex.CLIENTS_JSON = data_dir / "clients.json"
    ex.BACKUP_DIR = data_dir / "backups"
    ex.STYLE_PROFILE = data_dir / "style_profile.json"
    ex.REPLY_STATS = data_dir / "reply_stats.json"
    ex.CANDIDATES = data_dir / "candidates.json"
    ex.PATTERNS_ACTIVE = data_dir / "patterns_active.json"
    ex.PATTERNS_HISTORY = data_dir / "patterns_history.json"
    ex.LAST_REPORT = data_dir / "last_report.json"
    ex.REPORT_HISTORY_DIR = data_dir / "report_history"

    fails = []

    def check(cond, msg):
        if not cond:
            fails.append(msg)
            print(f"  FAIL: {msg}")
        else:
            print(f"  ok: {msg}")

    # 1) extract
    print("\n=== extract ===")
    try:
        ex.extract()
    except Exception as e:
        print(f"  FAIL: extract exception {e}")
        fails.append(f"extract: {e}")
        if not args.keep:
            shutil.rmtree(tmp, ignore_errors=True)
        return 1

    check(ex.CLIENTS_JSON.exists(), "clients.json written")
    clients = json.loads(ex.CLIENTS_JSON.read_text(encoding="utf-8"))
    check(isinstance(clients, list) and len(clients) > 0, f"clients count={len(clients)}")
    summary_path = data_dir / "summary.json"
    check(summary_path.exists(), "summary.json written")
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    check(summary.get("total", 0) == len(clients), "summary.total matches")

    # 温度字段存在
    with_temp = sum(1 for c in clients if isinstance(c.get("temperature"), dict))
    check(with_temp == len(clients), f"all have temperature ({with_temp})")

    # 热池门槛：自动 hot 应 ≤ hot_max_size
    hot = [c for c in clients if c.get("pool") == "hot"]
    hot_max = base_cfg["pools"]["hot_max_size"]
    check(len(hot) <= hot_max, f"hot pool size {len(hot)} <= {hot_max}")
    print(f"  info: hot={len(hot)} B={sum(1 for c in clients if c.get('pool')=='B')} "
          f"dormant={sum(1 for c in clients if c.get('pool')=='dormant')}")

    # 2) detail 第一个有消息的客户
    print("\n=== detail + style_stats ===")
    target = next((c for c in clients if c.get("cust_msg_count", 0) > 0), clients[0])
    jid = target["jid"]
    print(f"  target: {target.get('name')} {jid}")
    # capture stdout
    import io
    from contextlib import redirect_stdout
    buf = io.StringIO()
    with redirect_stdout(buf):
        ex.detail(jid)
    out = buf.getvalue()
    try:
        detail = json.loads(out)
    except json.JSONDecodeError:
        check(False, "detail JSON parse")
        detail = {}
    check("error" not in detail, f"detail no error ({detail.get('error')})")
    stats = detail.get("customer_style_stats") or {}
    check("customer" in stats and "confidence" in stats, f"style_stats confidence={stats.get('confidence')}")
    check("recent_conversation" in detail, "recent_conversation present")

    # 3) write-analysis
    print("\n=== write-analysis ===")
    code = ex.write_analysis({
        "jid": jid,
        "intent": "中",
        "stage": "深度了解→技术澄清",
        "diagnosis": "e2e smoke",
        "script": "Hello, following up on the specs. Please confirm.",
        "promises": [{"text": "e2e follow-up", "due": "2099-01-01", "status": "open"}],
    })
    check(code == 0, "write-analysis exit 0")
    clients2 = json.loads(ex.CLIENTS_JSON.read_text(encoding="utf-8"))
    row = next(c for c in clients2 if c["jid"] == jid)
    check(row.get("intent") == "中", "intent persisted")
    check(row.get("priority") == target.get("priority"), "priority not clobbered")
    check(any(p.get("text") == "e2e follow-up" for p in (row.get("promises") or [])), "promise saved")
    check(list(ex.BACKUP_DIR.glob("clients_write_*.json")), "write backup exists")

    # 拒绝 protected
    code_bad = ex.write_analysis({"jid": jid, "temperature": {"value": 99}})
    check(code_bad == 1, "reject protected temperature")

    # 4) save-report
    print("\n=== save-report ===")
    code = ex.save_report({
        "top3": [{"name": row.get("name"), "action": "follow up", "jid": jid}],
        "actions": [{"jid": jid, "name": row.get("name"), "action": "follow up", "when": "now"}],
        "temperatures": {row.get("name") or jid: (row.get("temperature") or {}).get("value", 0)},
        "one_liner": "e2e smoke ok",
    })
    check(code == 0, "save-report exit 0")
    check(ex.LAST_REPORT.exists(), "last_report.json exists")
    lr = json.loads(ex.LAST_REPORT.read_text(encoding="utf-8"))
    check(lr.get("one_liner") == "e2e smoke ok", "last_report content")

    # 5) 可配置温度信号：切换 hot 词后重算应变化（单元级）
    print("\n=== temperature signals config ===")
    msgs = [{"text": "please send invoice and payment details", "from_me": False}]
    t_default = ex.compute_temperature(msgs, 0, False, base_cfg)
    cfg_empty_hot = json.loads(json.dumps(base_cfg))
    cfg_empty_hot["industry"]["temperature_hot_signals"] = ["zzznomatchzzz"]
    cfg_empty_hot["industry"]["temperature_mid_signals"] = ["zzznomatchzzz"]
    cfg_empty_hot["industry"]["temperature_demand_signals"] = []
    t_muted = ex.compute_temperature(msgs, 0, False, cfg_empty_hot)
    check(t_default > t_muted, f"config hot signals affect score ({t_default} > {t_muted})")

    # 6) hot_entry gate unit
    print("\n=== hot_entry gate ===")
    ok_gate = ex.can_auto_upgrade_to_hot(
        "high", 1, True, 5, False, 50, base_cfg
    )
    bad_gate = ex.can_auto_upgrade_to_hot(
        "high", 1, True, 5, False, 10, base_cfg  # temp too low
    )
    check(ok_gate and not bad_gate, "min_temperature gate works")

    print("\n" + ("E2E ALL PASS" if not fails else f"E2E FAILED ({len(fails)}): {fails}"))
    print(f">>> temp dir: {tmp}" + (" (kept)" if args.keep else " (cleaning)"))
    if not args.keep:
        shutil.rmtree(tmp, ignore_errors=True)
    return 0 if not fails else 1


if __name__ == "__main__":
    sys.exit(main())
