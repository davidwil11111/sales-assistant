#!/usr/bin/env python3
"""预处理报告数据：提取 → 温度计算 → 输出单个JSON供cron agent直接使用。
cron agent不再跑extract.py和detail，只读这个JSON然后按模板格式化。
"""
import json, sys, os, subprocess
from datetime import datetime

SKILL_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.chdir(SKILL_DIR)

# 加载配置
def load_config():
    config_path = os.path.join(SKILL_DIR, "config.json")
    with open(config_path) as f:
        return json.load(f)

CFG = load_config()
OWNER = CFG.get("owner_name", "owner")
IND = CFG.get("industry", {})


def compute_temperature(client):
    """基于对话信号计算机会温度 0-100"""
    temp = 50
    msgs = client.get("recent_conversation", [])
    all_text = " ".join([m.get("text", "") for m in msgs]).lower()
    cust_text = " ".join([m.get("text", "") for m in msgs if m.get("role") == "客户"]).lower()

    deal_signals = ["invoice", "pi", "payment", "deposit", "delivery", "confirm", "order", "account", "transfer", "swift"]
    for s in deal_signals:
        if s in all_text:
            temp += 15
            break

    demand_signals = IND.get("temperature_demand_signals", ["spec", "price", "quote", "quotation"])
    for s in demand_signals:
        if s in all_text:
            temp += 10
            break

    days = client.get("days_silent", 0)
    if days > 7:
        temp -= 10
    if days > 14:
        temp -= 10
    if days > 30:
        temp -= 20

    wait_signals = ["month", "later", "wait", "next year", "approval", "boss", "manager"]
    for s in wait_signals:
        if s in cust_text:
            temp -= 15
            break

    consecutive_owner = 0
    for m in reversed(msgs):
        if m.get("role") == OWNER:
            consecutive_owner += 1
        else:
            break
    if consecutive_owner >= 5:
        temp -= 10
    elif consecutive_owner >= 3:
        temp -= 5

    last_3_roles = [m.get("role") for m in msgs[-3:]]
    if "客户" in last_3_roles:
        temp += 5

    return max(0, min(100, temp))


def extract_signals(client):
    msgs = client.get("recent_conversation", [])
    all_text = " ".join([m.get("text", "") for m in msgs]).lower()
    cust_msgs = [m for m in msgs if m.get("role") == "客户"]
    owner_msgs = [m for m in msgs if m.get("role") == OWNER]

    signals = []
    if any(s in all_text for s in ["invoice", "pi", "payment", "deposit", "account", "transfer"]):
        signals.append("成交信号")
    if any(s in all_text for s in ["quote", "price", "quotation", "discount"]):
        signals.append("价格讨论")
    # 技术信号从配置文件读取
    tech_signals = IND.get("temperature_demand_signals", ["spec", "drawing"])
    if any(s in all_text for s in tech_signals):
        signals.append("技术澄清")
    if client.get("days_silent", 0) > 7:
        signals.append(f"沉默{client['days_silent']}天")
    if len(owner_msgs) > len(cust_msgs) * 2 and len(cust_msgs) > 0:
        signals.append(f"{OWNER}发送>客户2倍")

    return signals


# ── main ──

# 1. 跑extract.py
print(">>> 运行 extract.py...", file=sys.stderr)
ret = os.system(f"{sys.executable} extract.py >/dev/null 2>&1")
if ret != 0:
    print(json.dumps({"error": "extract.py failed", "exit_code": ret}))
    sys.exit(1)

# 2. 读summary
with open("data/summary.json") as f:
    summary = json.load(f)

# 3. 取hot pool + detail
with open("data/clients.json") as f:
    all_clients = json.load(f)

hot_jids = [c["jid"] for c in summary["hot_pool"]]
hot_clients = [c for c in all_clients if c["jid"] in hot_jids]

report_clients = []
for c in hot_clients:
    result = subprocess.run(
        [sys.executable, "extract.py", "--detail", c["jid"]],
        capture_output=True, text=True, timeout=30
    )
    if result.returncode == 0:
        detail = json.loads(result.stdout)
        c["recent_conversation"] = detail.get("recent_conversation", [])
    else:
        c["recent_conversation"] = []

    c["temperature"] = compute_temperature(c)
    c["key_signals"] = extract_signals(c)
    report_clients.append(c)

# 4. B池样本
b_clients = [c for c in all_clients if c.get("pool") == "B" and c.get("days_silent", 0) >= 3]
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

# 5. 行为统计
behavior = {}
if os.path.exists("data/behavior_stats.json"):
    with open("data/behavior_stats.json") as f:
        behavior = json.load(f)

# 6. 组装
report_input = {
    "generated_at": datetime.now().isoformat(),
    "summary": {
        "total": summary["total"],
        "hot_count": summary["pools"]["hot"],
        "b_count": summary["pools"]["B"],
        "new_messages": summary["new_customer_messages"],
        "pursuit_warnings": summary["pursuit_warnings"],
    },
    "hot_clients": [],
    "b_samples": b_samples,
    "behavior": behavior,
}

for c in report_clients:
    entry = {
        "jid": c["jid"],
        "name": c["name"],
        "country": c["country"],
        "utc_offset": c.get("utc_offset", 0),
        "local_hour": c.get("local_hour", 0),
        "days_silent": c.get("days_silent", 0),
        "temperature": c.get("temperature", 0),
        "key_signals": c.get("key_signals", []),
        "intent": c.get("intent"),
        "stage": c.get("stage"),
        "diagnosis": c.get("diagnosis"),
        "pool": c.get("pool"),
        "analyzed_at": c.get("analyzed_at"),
        "recent_conversation": [],
    }
    for msg in c.get("recent_conversation", []):
        entry["recent_conversation"].append({
            "role": msg.get("role", "unknown"),
            "text": msg.get("text", "")[:500],
        })
    report_input["hot_clients"].append(entry)

print(json.dumps(report_input, ensure_ascii=False, indent=2))
