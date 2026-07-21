#!/usr/bin/env python3
"""预处理报告数据：extract → 读 clients 已算好的温度 → 输出 data/report_input.json。

温度算法以 extract.compute_temperature 为唯一实现（SSOT）。
本脚本不再重复实现温度，只组装 cron Agent 可读的轻量 JSON。
"""
import json
import os
import subprocess
import sys
from datetime import datetime

SKILL_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.chdir(SKILL_DIR)

REPORT_INPUT_PATH = os.path.join(SKILL_DIR, "data", "report_input.json")


def load_config():
    with open(os.path.join(SKILL_DIR, "config.json"), encoding="utf-8") as f:
        return json.load(f)


CFG = load_config()
OWNER = CFG.get("owner_name") or "owner"
IND = CFG.get("industry", {})


def extract_signals(client):
    """从已加载的对话文本提炼关键信号标签（展示用，非温度计算）。"""
    msgs = client.get("recent_conversation", [])
    all_text = " ".join([m.get("text", "") for m in msgs]).lower()
    cust_msgs = [m for m in msgs if m.get("role") == "客户"]
    owner_msgs = [m for m in msgs if m.get("role") == OWNER]

    signals = []
    if any(s in all_text for s in ["invoice", "pi", "payment", "deposit", "account", "transfer"]):
        signals.append("成交信号")
    if any(s in all_text for s in ["quote", "price", "quotation", "discount"]):
        signals.append("价格讨论")
    tech_signals = IND.get("temperature_demand_signals") or ["spec", "drawing"]
    if any(s in all_text for s in tech_signals):
        signals.append("技术澄清")
    if client.get("days_silent", 0) > 7:
        signals.append(f"沉默{client['days_silent']}天")
    if len(owner_msgs) > len(cust_msgs) * 2 and len(cust_msgs) > 0:
        signals.append(f"{OWNER}发送>客户2倍")
    temp = client.get("temperature") or {}
    if isinstance(temp, dict) and temp.get("value") is not None:
        signals.append(f"温度{temp['value']}")
    return signals


def temp_value(client):
    """读取 extract.py 写入的 temperature（SSOT），不做二次计算。"""
    t = client.get("temperature")
    if isinstance(t, dict):
        return t.get("value", 0), t.get("trend", "→")
    if isinstance(t, (int, float)):
        return int(t), "→"
    return 0, "→"


def main():
    print(">>> 运行 extract.py...", file=sys.stderr)
    ret = subprocess.run([sys.executable, "extract.py"], capture_output=True, text=True)
    if ret.returncode != 0:
        err = {
            "error": "extract.py failed",
            "exit_code": ret.returncode,
            "stderr": (ret.stderr or "")[-500:],
        }
        print(json.dumps(err, ensure_ascii=False))
        sys.exit(1)

    with open("data/summary.json", encoding="utf-8") as f:
        summary = json.load(f)

    with open("data/clients.json", encoding="utf-8") as f:
        all_clients = json.load(f)

    hot_jids = [c["jid"] for c in summary.get("hot_pool", [])]
    hot_clients = [c for c in all_clients if c["jid"] in hot_jids]

    report_clients = []
    for c in hot_clients:
        result = subprocess.run(
            [sys.executable, "extract.py", "--detail", c["jid"]],
            capture_output=True, text=True, timeout=30,
        )
        if result.returncode == 0:
            try:
                detail = json.loads(result.stdout)
                c["recent_conversation"] = detail.get("recent_conversation", [])
            except json.JSONDecodeError:
                c["recent_conversation"] = []
        else:
            c["recent_conversation"] = []

        value, trend = temp_value(c)
        c["_temp_value"] = value
        c["_temp_trend"] = trend
        c["key_signals"] = extract_signals(c)
        report_clients.append(c)

    b_clients = [
        c for c in all_clients
        if c.get("pool") == "B" and c.get("days_silent", 0) >= 3
    ]
    b_clients.sort(key=lambda x: -x.get("days_silent", 0))
    b_samples = []
    for c in b_clients[:3]:
        b_samples.append({
            "jid": c["jid"],
            "name": c["name"],
            "country": c["country"],
            "days_silent": c.get("days_silent", 0),
            "cust_msg_count": c.get("cust_msg_count", 0),
            "products": c.get("products", []),
        })

    behavior = {}
    if os.path.exists("data/behavior_stats.json"):
        with open("data/behavior_stats.json", encoding="utf-8") as f:
            behavior = json.load(f)

    # 昨日报告快照（复盘底座）
    last_report = {}
    if os.path.exists("data/last_report.json"):
        with open("data/last_report.json", encoding="utf-8") as f:
            last_report = json.load(f)

    # 全库 open 承诺（供承诺与兑现板块）
    promises_open = []
    for c in all_clients:
        for p in c.get("promises") or []:
            if isinstance(p, dict) and p.get("status") == "open":
                promises_open.append({
                    "jid": c.get("jid"),
                    "name": c.get("name"),
                    "id": p.get("id"),
                    "text": p.get("text"),
                    "due": p.get("due"),
                })

    report_input = {
        "generated_at": datetime.now().isoformat(),
        "owner_name": OWNER,
        "wacli_account": CFG.get("wacli_account", "test"),
        "summary": {
            "total": summary.get("total", 0),
            "hot_count": summary.get("pools", {}).get("hot", 0),
            "b_count": summary.get("pools", {}).get("B", 0),
            "new_messages": summary.get("new_customer_messages", 0),
            "pursuit_warnings": summary.get("pursuit_warnings", 0),
        },
        "hot_clients": [],
        "b_samples": b_samples,
        "behavior": behavior,
        "last_report": {
            "date": last_report.get("date"),
            "top3": last_report.get("top3") or [],
            "actions": last_report.get("actions") or [],
            "one_liner": last_report.get("one_liner") or "",
        },
        "promises_open": promises_open,
    }

    for c in report_clients:
        open_ps = [
            p for p in (c.get("promises") or [])
            if isinstance(p, dict) and p.get("status") == "open"
        ]
        entry = {
            "jid": c["jid"],
            "name": c["name"],
            "country": c["country"],
            "utc_offset": c.get("utc_offset", 0),
            "local_hour": c.get("local_hour", 0),
            "days_silent": c.get("days_silent", 0),
            "temperature": c.get("_temp_value", 0),
            "temperature_trend": c.get("_temp_trend", "→"),
            "key_signals": c.get("key_signals", []),
            "intent": c.get("intent"),
            "stage": c.get("stage"),
            "diagnosis": c.get("diagnosis"),
            "pool": c.get("pool"),
            "analyzed_at": c.get("analyzed_at"),
            "analyzed_stale": c.get("analyzed_stale"),
            "promises_open": open_ps,
            "recent_conversation": [],
        }
        for msg in c.get("recent_conversation", []):
            entry["recent_conversation"].append({
                "role": msg.get("role", "unknown"),
                "text": (msg.get("text") or "")[:500],
            })
        report_input["hot_clients"].append(entry)

    os.makedirs(os.path.dirname(REPORT_INPUT_PATH), exist_ok=True)
    with open(REPORT_INPUT_PATH, "w", encoding="utf-8") as f:
        json.dump(report_input, f, ensure_ascii=False, indent=2)

    print(f">>> wrote {REPORT_INPUT_PATH}", file=sys.stderr)
    print(json.dumps(report_input, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
