#!/usr/bin/env python3
"""稳定性自检：风格统计、安全写回、报告快照、池建议、原子写。不依赖 wacli.db。"""
import json
import os
import sys
import tempfile
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)

# 在临时 data 目录隔离测试
import extract as ex


def section(title):
    print(f"\n=== {title} ===")


def main():
    fails = []
    tmp = Path(tempfile.mkdtemp(prefix="sarah_test_"))
    try:
        # 指向临时 clients
        clients_path = tmp / "clients.json"
        backup_dir = tmp / "backups"
        last_report = tmp / "last_report.json"
        report_hist = tmp / "report_history"
        backup_dir.mkdir()
        report_hist.mkdir()

        ex.CLIENTS_JSON = clients_path
        ex.BACKUP_DIR = backup_dir
        ex.LAST_REPORT = last_report
        ex.REPORT_HISTORY_DIR = report_hist

        # 1) customer_style_stats
        section("customer_style_stats")
        msgs = [
            {"text": "Ok", "from_me": False},
            {"text": "Thanks", "from_me": False},
            {"text": "New invoice please", "from_me": False},
            {"text": "Hello 😊 how are you?", "from_me": True},
            {"text": "Please confirm", "from_me": True},
        ]
        stats = ex.compute_customer_style_stats(msgs)
        assert stats["customer"]["msg_count"] == 3, stats
        assert stats["customer"]["emoji_count"] == 0, stats
        assert stats["owner_recent"]["emoji_count"] >= 1, stats
        assert stats["confidence"] == "low", stats  # <8 customer msgs
        print("ok", stats["confidence"], "cust_avg", stats["customer"]["avg_len"])

        # 2) pool_suggestion archive + tightened upgrade
        section("pool_suggestion")
        cfg = {"pools": {
            "hot_max_silent_days": 30,
            "B_max_silent_days": 90,
            "dormant_max_silent_days": 365,
            "hot_entry": {
                "auto_upgrade_enabled": True,
                "require_priority": "high",
                "require_new_customer_message": True,
                "min_cust_msgs": 2,
                "max_days_silent": 14,
                "min_temperature": 35,
                "require_no_pursuit": True,
            },
        }}
        assert ex.pool_suggestion("low", 400, "dormant", False, cfg) == "archive"
        assert ex.pool_suggestion("high", 1, "B", True, cfg) == "upgrade_to_hot"
        # medium + 沉默 20 天：不再建议升热
        assert ex.pool_suggestion("medium", 20, "B", True, cfg) is None
        assert ex.pool_suggestion("medium", 3, "B", True, cfg) == "upgrade_to_hot"
        assert ex.can_auto_upgrade_to_hot("high", 1, True, 5, False, 50, cfg)
        assert not ex.can_auto_upgrade_to_hot("high", 1, True, 5, False, 10, cfg)
        assert not ex.can_auto_upgrade_to_hot("high", 1, False, 5, False, 50, cfg)
        print("ok archive + tightened upgrade + hot_entry")

        # 2b) temperature signals from industry config
        section("temperature signals config")
        msgs = [{"text": "please send invoice for payment", "from_me": False}]
        t1 = ex.compute_temperature(msgs, 0, False, {"industry": {}})
        t2 = ex.compute_temperature(msgs, 0, False, {
            "industry": {
                "temperature_hot_signals": ["nomatch"],
                "temperature_mid_signals": ["nomatch"],
                "temperature_demand_signals": [],
            }
        })
        assert t1 > t2, (t1, t2)
        t3 = ex.compute_temperature(
            [{"text": "need lumen and cri details", "from_me": False}],
            0, False,
            {"industry": {"temperature_demand_signals": ["lumen", "cri"]}},
        )
        assert t3 >= 20, t3
        print("ok configurable hot/mid signals")

        # 3) write_analysis merge + protect history
        section("write_analysis")
        seed = [{
            "jid": "111@s.whatsapp.net",
            "name": "TestCo",
            "pool": "B",
            "priority": "high",
            "intent": None,
            "script_history": [{"script": "old hello", "at": "t0"}],
            "coach_log": [],
            "pool_history": [],
            "promises": [],
            "detection": {"pursuit_warning": False},
            "temperature": {"value": 40, "trend": "→"},
        }]
        ex.atomic_write_json(clients_path, seed)

        # 禁止写 protected
        code = ex.write_analysis({
            "jid": "111@s.whatsapp.net",
            "priority": "low",
        })
        assert code == 1, "should reject protected field"
        print("ok reject protected")

        code = ex.write_analysis({
            "jid": "111@s.whatsapp.net",
            "intent": "高",
            "stage": "临门一脚→成交前确认",
            "script": "Invoice ready. Please confirm.",
            "pool": "hot",
            "diagnosis": "customer pushing",
            "coach_log": [{
                "type": "style_mismatch",
                "severity": "medium",
                "summary": "emoji vs zero",
                "at": "2026-07-14",
            }],
            "promises": [{
                "text": "send updated invoice",
                "due": "2026-07-16",
                "status": "open",
            }],
        })
        assert code == 0
        data = json.loads(clients_path.read_text(encoding="utf-8"))
        c = data[0]
        assert c["intent"] == "高"
        assert c["pool"] == "hot"
        assert c["priority"] == "high", "extract field must survive"
        assert c["temperature"]["value"] == 40
        assert len(c["script_history"]) == 2, c["script_history"]
        assert c["script_history"][0]["script"] == "old hello"
        assert len(c["pool_history"]) == 1
        assert c["pool_history"][0]["from"] == "B" and c["pool_history"][0]["to"] == "hot"
        assert len(c["promises"]) == 1 and c["promises"][0]["status"] == "open"
        assert c["detection"]["pursuit_warning"] is False
        assert list(backup_dir.glob("clients_write_*.json")), "backup missing"
        print("ok merge, history append, promises, backup")

        # 重复 coach 不刷屏
        code = ex.write_analysis({
            "jid": "111@s.whatsapp.net",
            "coach_log": [{
                "type": "style_mismatch",
                "severity": "medium",
                "summary": "emoji vs zero",
                "at": "2026-07-15",
            }],
        })
        assert code == 0
        data = json.loads(clients_path.read_text(encoding="utf-8"))
        assert len(data[0]["coach_log"]) == 1, "dedupe coach_log"
        print("ok coach_log dedupe")

        # promise update
        pid = data[0]["promises"][0]["id"]
        code = ex.write_analysis({
            "jid": "111@s.whatsapp.net",
            "promise_updates": [{"id": pid, "status": "done"}],
        })
        assert code == 0
        data = json.loads(clients_path.read_text(encoding="utf-8"))
        assert data[0]["promises"][0]["status"] == "done"
        print("ok promise_updates")

        # 4) save_report
        section("save_report")
        code = ex.save_report({
            "top3": [{"name": "TestCo", "action": "send invoice", "jid": "111@s.whatsapp.net"}],
            "actions": [{"jid": "111@s.whatsapp.net", "name": "TestCo", "action": "send invoice", "when": "now"}],
            "temperatures": {"TestCo": 40},
            "one_liner": "Ship the invoice today.",
        })
        assert code == 0
        assert last_report.exists()
        lr = json.loads(last_report.read_text(encoding="utf-8"))
        assert lr["top3"][0]["name"] == "TestCo"
        assert list(report_hist.glob("*.json"))
        print("ok last_report + history")

        # 5) atomic_write roundtrip
        section("atomic_write_json")
        p = tmp / "a.json"
        ex.atomic_write_json(p, {"x": 1})
        assert json.loads(p.read_text(encoding="utf-8"))["x"] == 1
        print("ok")

        # 6) prepare_report_data has no local temperature recompute
        section("prepare_report_data SSOT")
        prep = (ROOT / "scripts" / "prepare_report_data.py").read_text(encoding="utf-8")
        assert "def compute_temperature" not in prep, "duplicate temperature must stay removed"
        assert "report_input.json" in prep
        print("ok no duplicate temperature")

        # 7) SKILL slim
        section("SKILL size")
        skill = (ROOT / "SKILL.md").read_text(encoding="utf-8")
        chars = len(skill)
        assert chars < 14000, f"SKILL still too large: {chars}"
        assert "--write-analysis" in skill
        assert "--save-report" in skill
        assert "customer_style_stats" in skill
        assert "onboarding-flow.md" in skill
        assert (ROOT / "references" / "onboarding-flow.md").exists(), "missing onboarding-flow.md"
        assert (ROOT / "references" / "report-template.md").exists(), "missing report-template.md"
        print(f"ok SKILL chars={chars} lines={skill.count(chr(10))+1}")

    except AssertionError as e:
        fails.append(str(e))
        print("FAIL:", e)
    except Exception as e:
        fails.append(repr(e))
        print("ERROR:", e)
        raise
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    print("\n" + ("ALL PASS" if not fails else f"FAILED: {fails}"))
    return 0 if not fails else 1


if __name__ == "__main__":
    sys.exit(main())
