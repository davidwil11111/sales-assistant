#!/usr/bin/env python3
"""
extract.py — Sarah销售系统唯一数据脚本

默认模式: SQLite → clients.json（元数据，不含对话原文，轻量文件）
详情模式: --detail JID → 输出单个客户完整对话 + customer_style_stats
写回模式: --write-analysis '<json>' → 安全合并写回 Agent 分析字段
快照模式: --save-report '<json>' → 写入 data/last_report.json（昨日复盘底座）

用法:
  python3 extract.py
  python3 extract.py --detail <JID>
  python3 extract.py --write-analysis '{"jid":"...","intent":"高",...}'
  python3 extract.py --save-report '{"top3":[...],"actions":[...]}'
"""
import sqlite3, json, re, sys, argparse, os, tempfile
from pathlib import Path
from datetime import datetime, timezone, timedelta
import shutil

SCRIPT_DIR = Path(__file__).resolve().parent
CONFIG = SCRIPT_DIR / "config.json"
CLIENTS_JSON = SCRIPT_DIR / "data" / "clients.json"
BACKUP_DIR = SCRIPT_DIR / "data" / "backups"
STYLE_PROFILE = SCRIPT_DIR / "data" / "style_profile.json"
REPLY_STATS = SCRIPT_DIR / "data" / "reply_stats.json"
CANDIDATES = SCRIPT_DIR / "data" / "candidates.json"
PATTERNS_ACTIVE = SCRIPT_DIR / "data" / "patterns_active.json"
PATTERNS_HISTORY = SCRIPT_DIR / "data" / "patterns_history.json"
LAST_REPORT = SCRIPT_DIR / "data" / "last_report.json"
REPORT_HISTORY_DIR = SCRIPT_DIR / "data" / "report_history"

# Agent 可覆盖的标量字段（写回时合并）
AGENT_SCALAR_FIELDS = (
    "intent", "stage", "diagnosis", "script", "send_time",
    "risk", "coach_note", "analyzed_at", "pool",
)
# 历史层：只追加
AGENT_APPEND_FIELDS = ("script_history", "coach_log", "pool_history", "promises")
# extract 每次覆盖，write_analysis 禁止改动
EXTRACT_PROTECTED = {
    "jid", "name", "country", "utc_offset", "local_hour", "priority",
    "days_silent", "cust_msg_count", "my_msg_count", "products",
    "last_message_ts", "last_customer_message_ts", "is_new_message",
    "is_new_customer_message", "detection", "script_tracking",
    "pool_suggestion", "analyzed_stale", "temperature", "temperature_history",
}

_EMOJI_RE = re.compile(
    "["
    "\U0001F300-\U0001F9FF"
    "\U00002600-\U000027BF"
    "\U0001F600-\U0001F64F"
    "\U0001F680-\U0001F6FF"
    "]+",
    flags=re.UNICODE,
)


def load_config():
    with open(CONFIG, encoding="utf-8") as f:
        return json.load(f)


def atomic_write_json(path, data):
    """原子写 JSON：先写临时文件再 replace，防止半写损坏。"""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
            f.write("\n")
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def backup_clients_json():
    """写回前备份 clients.json，保留最近 20 份。"""
    if not CLIENTS_JSON.exists():
        return None
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    dest = BACKUP_DIR / f"clients_write_{ts}.json"
    shutil.copy2(CLIENTS_JSON, dest)
    backups = sorted(BACKUP_DIR.glob("clients_write_*.json"))
    for old in backups[:-20]:
        try:
            old.unlink()
        except OSError:
            pass
    return str(dest)


def load_clients_list():
    if not CLIENTS_JSON.exists():
        return []
    with open(CLIENTS_JSON, encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, list):
        raise ValueError("clients.json 必须是数组")
    return data


def compute_customer_style_stats(msgs, owner_name="owner"):
    """从消息列表统计单客户沟通风格（可证伪，供 Agent 风格匹配使用）。"""
    cust_texts = []
    owner_texts = []
    for m in msgs:
        text = (m.get("text") or m.get("display_text") or "").strip()
        if not text:
            continue
        if m.get("from_me"):
            owner_texts.append(text)
        else:
            cust_texts.append(text)

    def _stats(texts):
        if not texts:
            return {
                "msg_count": 0,
                "avg_len": 0,
                "emoji_count": 0,
                "emoji_rate": 0.0,
                "msgs_with_emoji": 0,
                "question_rate": 0.0,
                "sample_phrases": [],
            }
        lengths = [len(t) for t in texts]
        emoji_hits = [_EMOJI_RE.findall(t) for t in texts]
        emoji_count = sum(len(e) for e in emoji_hits)
        with_emoji = sum(1 for e in emoji_hits if e)
        questions = sum(1 for t in texts if "?" in t or "？" in t)
        # 极短消息样本（风格指纹）
        short = [t for t in texts if len(t) <= 40][:8]
        return {
            "msg_count": len(texts),
            "avg_len": round(sum(lengths) / len(lengths), 1),
            "emoji_count": emoji_count,
            "emoji_rate": round(with_emoji / len(texts), 3),
            "msgs_with_emoji": with_emoji,
            "question_rate": round(questions / len(texts), 3),
            "sample_phrases": short,
        }

    cust = _stats(cust_texts)
    owner = _stats(owner_texts[-5:] if owner_texts else [])  # 最近所有者消息
    confidence = "high" if cust["msg_count"] >= 20 else ("medium" if cust["msg_count"] >= 8 else "low")
    return {
        "customer": cust,
        "owner_recent": owner,
        "confidence": confidence,
        "note": "customer 统计来自近30条内客户消息；样本不足时 confidence=low，禁止编造历史风格",
    }

def get_country(jid, tz_map):
    phone = jid.split("@")[0]
    for length in [4, 3, 2, 1]:
        if phone[:length] in tz_map:
            name, offset = tz_map[phone[:length]]
            return name, offset
    return "未知", 0

def get_local_hour(offset):
    return (datetime.now(timezone.utc).hour + int(offset)) % 24

def compute_priority(cust_text, cust_count, age_hours, cfg):
    sc = cfg["scoring"]
    high_kw = [k.lower() for k in sc["high_keywords"]]
    med_kw = [k.lower() for k in sc["medium_keywords"]]
    high_win = sc["high_window_days"] * 24
    med_win = sc["medium_window_days"] * 24
    if cust_count == 0:
        return "low"
    if age_hours <= high_win and sum(cust_text.count(k) for k in high_kw) >= 2:
        return "high"
    if age_hours <= med_win and sum(cust_text.count(k) for k in med_kw) >= 2:
        return "medium"
    if cust_count >= sc["min_cust_msgs_for_medium"]:
        return "medium"
    return "low"

def extract_products(name, cust_text, my_msgs, cfg):
    """从客户名和对话中提取产品标签，关键词来源于 config.json industry.product_keywords"""
    ind = cfg.get("industry", {})
    kw_map = ind.get("product_keywords", {})
    context_kws = ind.get("context_keywords", [])
    label_hints = ind.get("context_label_hints", [])

    products = []
    name_lower = name.lower()

    # 1. 从客户名匹配关键词
    for kw, label in kw_map.items():
        if kw in name_lower and label not in products:
            products.append(label)

    # 2. 容量/规格提取（如重量/功率/尺寸），使用可配置的正则
    cap_pattern = ind.get("capacity_pattern", r"(\d+)\s*[tT吨]")
    cap_label = ind.get("capacity_label", "吨")
    cap_match = re.findall(cap_pattern, name)
    if cap_match:
        products.insert(0, f"{','.join(cap_match)}{cap_label}")

    # 3. 从客户消息中扫描关键词
    for kw in context_kws:
        if kw in cust_text and kw not in str(products):
            products.append(kw)

    # 4. 如果仍没有产品，从我方消息中扫描
    if not products:
        my_text = " ".join((m["text"] or "") for m in my_msgs).lower()
        for kw in label_hints:
            if kw in my_text and kw not in str(products):
                products.append(kw)

    return products[:3]

def auto_detect(msgs, cfg):
    det = cfg["detection"]
    consecutive = 0
    max_consecutive = 0
    for m in msgs:
        if m["from_me"]:
            consecutive += 1
        else:
            max_consecutive = max(max_consecutive, consecutive)
            consecutive = 0
    max_consecutive = max(max_consecutive, consecutive)
    pursuit = max_consecutive >= det["pursuit_threshold"]

    weak_count = 0
    weak_matches = []
    for m in msgs:
        if not m["from_me"]:
            continue
        text = (m["text"] or "").lower().replace("\u2019", "'").replace("\u2018", "'")
        for phrase in det["weak_phrases"]:
            if phrase.replace("\u2019", "'").replace("\u2018", "'") in text:
                weak_count += 1
                weak_matches.append(phrase[:30])
                break

    return {"pursuit_warning": pursuit, "max_consecutive_owner_msgs": max_consecutive,
            "weak_phrase_count": weak_count, "weak_phrase_samples": weak_matches[:5]}

def track_script(msgs, last_agent_script):
    if not last_agent_script:
        return {"script_sent": False, "script_result": None}
    script_key = last_agent_script.strip()[20:80] if len(last_agent_script) > 30 else last_agent_script.strip()[:60]
    sent = False
    sent_at = None
    for m in msgs:
        if m["from_me"] and script_key in (m["text"] or ""):
            sent = True
            sent_at = m["ts"]
            break
    replied = False
    if sent and sent_at:
        for m in msgs:
            if not m["from_me"] and m["ts"] > sent_at:
                replied = True
                break
    result = "replied" if replied else ("sent_no_reply" if sent else "not_sent")
    return {"script_sent": sent, "script_result": result}

def pool_suggestion(priority, days, old_pool, has_new_cust_msg, cfg):
    """池子建议（不直接改 pool）。升级建议已收紧：B→热需 high，或 medium 且沉默≤7天。"""
    pools = cfg["pools"]
    if not old_pool:
        old_pool = "B"
    if old_pool == "hot" and days > pools["hot_max_silent_days"]:
        return "downgrade_to_B"
    # 收紧：不再因 medium+任意新消息就建议升热
    if old_pool == "B" and has_new_cust_msg and priority == "high":
        return "upgrade_to_hot"
    if old_pool == "B" and has_new_cust_msg and priority == "medium" and days <= 7:
        return "upgrade_to_hot"
    if old_pool in ("dormant", "archive") and has_new_cust_msg:
        return "upgrade_to_hot"
    if old_pool == "B" and days > pools["B_max_silent_days"]:
        return "downgrade_to_dormant"
    if old_pool == "dormant" and days > pools.get("dormant_max_silent_days", 365):
        return "archive"
    return None


def can_auto_upgrade_to_hot(priority, days, has_new_cust_msg, cust_count,
                            pursuit_warning, temperature, cfg):
    """自动入热池门槛：全部满足才直接改 pool，否则只留 pool_suggestion。"""
    rules = (cfg.get("pools") or {}).get("hot_entry") or {}
    if not rules.get("auto_upgrade_enabled", True):
        return False
    need_pri = rules.get("require_priority", "high")
    if priority != need_pri:
        return False
    if rules.get("require_new_customer_message", True) and not has_new_cust_msg:
        return False
    if cust_count < int(rules.get("min_cust_msgs", 2)):
        return False
    if days > int(rules.get("max_days_silent", 14)):
        return False
    if rules.get("require_no_pursuit", True) and pursuit_warning:
        return False
    if temperature < int(rules.get("min_temperature", 35)):
        return False
    return True


# 通用外贸默认信号（行业未配置时使用；行业可在 config.industry 覆盖）
DEFAULT_HOT_SIGNALS = [
    "invoice", "pi", "proforma", "bank", "account", "payment",
    "deposit", "delivery", "ship", "packing", "ce certif",
    "visit factory", "come to china", "visit china",
    "swift", "iban", "transfer", "remit", "tt", "l/c", "lc ",
    "confirm order", "place order", "purchase order", "po ",
]
DEFAULT_MID_SIGNALS = [
    "quote", "quotation", "price", "fob", "cif",
    "warranty", "certif", "standard",
]
DEFAULT_WAIT_SIGNALS = [
    "wait", "later", "next month", "next year", "not now", "next week", "next time",
    "两个月", "2 month", "two month", "明年", "过段时间",
]


def resolve_temperature_signals(cfg=None):
    """从 industry 读取温度信号；空列表/缺省则回退默认。"""
    ind = (cfg or {}).get("industry") or {}
    hot = ind.get("temperature_hot_signals") or DEFAULT_HOT_SIGNALS
    mid = list(ind.get("temperature_mid_signals") or DEFAULT_MID_SIGNALS)
    for sig in ind.get("temperature_demand_signals") or []:
        if sig and sig not in mid:
            mid.append(sig)
    wait = ind.get("temperature_wait_signals") or DEFAULT_WAIT_SIGNALS
    return list(hot), mid, list(wait)

def compute_probability(stage, days_silent, has_new_cust_msg, pursuit_warning, priority, old_prob=None):
    """基于当前对话信号计算成交概率（不是科学精确值，是相对参考）"""
    base = {"临门一脚": 80, "价格谈判": 50, "深度了解": 40, "初次接触": 25, "已流失": 5}
    prob = base.get(stage, 20) if stage else 15
    prob -= min((days_silent // 3) * 5, 20)
    if has_new_cust_msg: prob += 10
    if pursuit_warning: prob -= 10
    if priority == "high": prob += 5
    prob = max(1, min(99, prob))
    trend = "\u2192"
    if old_prob is not None:
        diff = prob - old_prob
        if diff >= 5: trend = "\u2191"
        elif diff <= -5: trend = "\u2193"
    return {"value": prob, "trend": trend, "previous": old_prob}

def compute_temperature(cust_msgs, days_silent, pursuit_warning, cfg=None):
    """机会温度 0-100：信号词来自 config.industry（可配置），缺省用通用外贸默认。

    高温 +15 / 中温 +10 / 问句 +5(封顶15) / 沉默与轰炸降温 / wait 信号 -15
    """
    temp = 10  # 基础温度：有对话

    customer_texts = [(m["text"] or "").lower() for m in cust_msgs]
    all_text = " ".join(customer_texts)

    hot_signals, mid_signals, wait_signals = resolve_temperature_signals(cfg)

    for sig in hot_signals:
        if sig and str(sig).lower() in all_text:
            temp += 15

    for sig in mid_signals:
        if sig and str(sig).lower() in all_text:
            temp += 10

    question_markers = ["?", "how", "what", "when", "where", "can you", "do you", "is it", "could you", "would you"]
    q_count = sum(1 for t in customer_texts if any(m in t for m in question_markers))
    temp += min(q_count * 5, 15)

    if days_silent > 7:
        temp -= 10
    if days_silent > 14:
        temp -= 15
    if pursuit_warning:
        temp -= 10

    for sig in wait_signals:
        if sig and str(sig).lower() in all_text:
            temp -= 15
            break

    return max(0, min(100, temp))

def update_phrase_correlations(msgs, phrase_stats, cfg):
    """追踪所有者每条消息→客户48h内是否回复"""
    tracked = set(cfg["detection"]["weak_phrases"])
    msgs_sorted = sorted(msgs, key=lambda m: m["ts"])
    
    def norm(s):
        """统一Unicode引号→ASCII，消除WhatsApp智能引号导致的匹配失败"""
        return (s or "").lower().replace("\u2019", "'").replace("\u2018", "'")
    
    for i, m in enumerate(msgs_sorted):
        if not m["from_me"]:
            continue
        text = norm(m["text"])
        for phrase in tracked:
            if norm(phrase) in text:
                key = phrase
                if key not in phrase_stats:
                    phrase_stats[key] = {"count": 0, "replied": 0}
                phrase_stats[key]["count"] += 1
                for j in range(i + 1, len(msgs_sorted)):
                    if not msgs_sorted[j]["from_me"]:
                        gap_h = (msgs_sorted[j]["ts"] - m["ts"]) / 3600
                        if gap_h <= 48:
                            phrase_stats[key]["replied"] += 1
                        break
    return phrase_stats

def compute_correlations(phrase_stats, cfg, analyzed_count):
    """生成 style_profile.json — 个人风格指纹"""
    baseline = 0
    tracked = 0
    behaviors = {}
    for phrase, s in phrase_stats.items():
        rate = round(s["replied"] / s["count"], 2) if s["count"] else 0
        behaviors[phrase] = {"count": s["count"], "replied": s["replied"], "rate": rate}
        tracked += s["count"]
        baseline += s["replied"]
    baseline_rate = round(baseline / tracked, 2) if tracked else 0
    profile = {
        "user": cfg.get("owner_name", "owner"),
        "generated_at": datetime.now().isoformat(),
        "observed_clients": analyzed_count,
        "baseline_reply_rate": baseline_rate,
        "behaviors": behaviors,
        "onboarding_complete": tracked >= cfg.get("evolution", {}).get("onboarding_samples", 50),
        "hard_rules": {
            "online_tracking": "permanent_block",
            "emotional_manipulation": "permanent_block",
            "broken_promise": "permanent_warning",
            "pursuit_4plus": "permanent_warning",
            "heart_emoji_business": "permanent_warning"
        }
    }
    STYLE_PROFILE.parent.mkdir(parents=True, exist_ok=True)
    STYLE_PROFILE.write_text(json.dumps(profile, ensure_ascii=False, indent=2))
    return profile

def discover_patterns(phrase_stats, cfg, profile):
    """自动发现候选模式 → candidates.json"""
    evo = cfg.get("evolution", {})
    if not evo.get("enabled", True):
        return []
    min_samples = evo.get("min_samples", 5)
    baseline = profile.get("baseline_reply_rate", 0.3)
    auto_threshold = evo.get("auto_add_threshold", 0.0)
    candidate_threshold = evo.get("candidate_threshold", 0.5)
    known = set(cfg["detection"]["weak_phrases"])
    candidates = []
    for phrase, s in phrase_stats.items():
        if phrase in known:
            continue
        rate = s["replied"] / s["count"] if s["count"] else 1
        if s["count"] >= min_samples and rate <= auto_threshold:
            candidates.append({"phrase": phrase, "count": s["count"], "replied": s["replied"],
                               "reply_rate": rate, "baseline": baseline,
                               "verdict": "auto_add", "reason": f"回复率{rate}={auto_threshold}→自动加入"})
        elif s["count"] >= 3 and rate < baseline * candidate_threshold:
            candidates.append({"phrase": phrase, "count": s["count"], "replied": s["replied"],
                               "reply_rate": rate, "baseline": baseline,
                               "verdict": "candidate", "reason": f"数据暂不足，继续累积"})
    existing = {}
    if CANDIDATES.exists():
        existing = json.loads(CANDIDATES.read_text())
    pending = [c for c in candidates if c["verdict"] == "auto_add"]
    watch = [c for c in candidates if c["verdict"] == "candidate"]
    CANDIDATES.parent.mkdir(parents=True, exist_ok=True)
    CANDIDATES.write_text(json.dumps({
        "generated_at": datetime.now().isoformat(),
        "auto_added": pending, "watching": watch
    }, ensure_ascii=False, indent=2))
    return pending

def aggregate_stats(clients, cfg, profile):
    """生成 reply_stats.json — 月度对比数据"""
    now = datetime.now()
    month_key = now.strftime("%Y-%m")
    sent = sum(1 for c in clients if c["script_tracking"].get("script_sent"))
    replied = sum(1 for c in clients if c["script_tracking"].get("script_result") == "replied")
    pursuit = sum(1 for c in clients if c["detection"]["pursuit_warning"])
    weak = sum(c["detection"]["weak_phrase_count"] for c in clients)
    danger = sum(1 for c in clients 
                 if any(e in str(c.get("detection", {}).get("weak_phrase_samples", []))
                        for e in cfg.get("detection", {}).get("emoji_danger", [])))
    pattern_counts = {"pursuit": 0, "self_diminish": 0, "casual_drift": 0,
                      "boundary": 0, "broken_promise": 0, "no_close": 0}
    for c in clients:
        for entry in c.get("coach_log", []):
            if isinstance(entry, dict):
                p = entry.get("pattern", "")
                if p in pattern_counts:
                    pattern_counts[p] += 1
    prev = {}
    if REPLY_STATS.exists():
        prev = json.loads(REPLY_STATS.read_text())
    prev_beh = prev.get("behaviors", {})
    prev_pat = prev.get("patterns", {})
    current = {
        "month": month_key, "snapshot_at": now.isoformat(),
        "scripts": {"sent": sent, "replied": replied,
                     "reply_rate": round(replied / sent, 2) if sent else 0},
        "behaviors": {"pursuit_warnings": pursuit, "total_weak_phrases": weak,
                      "emoji_danger_count": danger},
        "patterns": pattern_counts,
        "previous_month": {"scripts": prev.get("scripts", {}),
                           "behaviors": prev_beh, "patterns": prev_pat}
    }
    def _delta(cur, prv):
        if prv == 0: return f"+{cur}" if cur else "0"
        d = cur - prv
        return f"{'+' if d >= 0 else ''}{d}"
    current["trend"] = {
        "reply_rate": _delta(current["scripts"]["reply_rate"], prev.get("scripts", {}).get("reply_rate", 0)),
        "pursuit": _delta(pursuit, prev_beh.get("pursuit_warnings", pursuit)),
        "weak_phrases": _delta(weak, prev_beh.get("total_weak_phrases", weak)),
    }
    for p in pattern_counts:
        current["trend"][p] = _delta(pattern_counts[p], prev_pat.get(p, pattern_counts[p]))
    REPLY_STATS.parent.mkdir(parents=True, exist_ok=True)
    REPLY_STATS.write_text(json.dumps(current, ensure_ascii=False, indent=2))

def apply_auto_rules(pending, cfg):
    """自动将候选写入 config.json weak_phrases"""
    if not pending:
        return
    new_phrases = [p["phrase"] for p in pending]
    existing_phrases = list(cfg["detection"]["weak_phrases"])
    added = []
    for phrase in new_phrases:
        if phrase not in existing_phrases:
            existing_phrases.append(phrase)
            added.append(phrase)
    if added:
        cfg["detection"]["weak_phrases"] = existing_phrases
        with open(CONFIG, "w") as f:
            json.dump(cfg, f, ensure_ascii=False, indent=2)
        now = datetime.now().isoformat()
        verify_dt = datetime.now() + timedelta(days=cfg.get("evolution", {}).get("verification_days", 30))
        verify_date = verify_dt.isoformat()
        active_entries = []
        for p in pending:
            p["added_at"] = now
            p["verify_at"] = verify_date
            p["before_reply_rate"] = p["reply_rate"]
            p["status"] = "verifying"
            active_entries.append(p)
        existing_active = []
        if PATTERNS_ACTIVE.exists():
            existing_active = json.loads(PATTERNS_ACTIVE.read_text()).get("active", [])
        PATTERNS_ACTIVE.parent.mkdir(parents=True, exist_ok=True)
        PATTERNS_ACTIVE.write_text(json.dumps({"active": existing_active + active_entries}, ensure_ascii=False, indent=2))
    return added

def _parse_payload(raw):
    """解析 CLI JSON：支持字符串或 @file 路径。"""
    if raw is None:
        raise ValueError("缺少 JSON payload")
    raw = raw.strip()
    if raw.startswith("@"):
        with open(raw[1:], encoding="utf-8") as f:
            return json.load(f)
    return json.loads(raw)


def _append_unique(existing, new_items, key_fields=None):
    """追加列表项；coach_log 按 type+summary 去重，其余直接 append dict。"""
    if not new_items:
        return list(existing or [])
    out = list(existing or [])
    if not isinstance(new_items, list):
        new_items = [new_items]
    for item in new_items:
        if not isinstance(item, dict):
            continue
        if key_fields:
            sig = tuple(item.get(k) for k in key_fields)
            if any(tuple(e.get(k) for k in key_fields) == sig for e in out if isinstance(e, dict)):
                continue
        out.append(item)
    return out[-50:]  # 防止无限膨胀


def write_analysis(payload):
    """安全合并写回 Agent 分析字段。禁止覆盖 extract 层与整文件重写 history。"""
    if isinstance(payload, str):
        payload = _parse_payload(payload)
    if not isinstance(payload, dict):
        print(json.dumps({"ok": False, "error": "payload 必须是对象"}, ensure_ascii=False))
        return 1
    jid = payload.get("jid")
    if not jid:
        print(json.dumps({"ok": False, "error": "缺少 jid"}, ensure_ascii=False))
        return 1

    # 拒绝保护字段
    forbidden = [k for k in payload if k in EXTRACT_PROTECTED and k != "jid"]
    if forbidden:
        print(json.dumps({
            "ok": False,
            "error": f"禁止写入 extract 层字段: {forbidden}",
            "hint": "只传 intent/stage/diagnosis/script/pool/coach_log 等 Agent 字段",
        }, ensure_ascii=False))
        return 1

    clients = load_clients_list()
    idx = next((i for i, c in enumerate(clients) if c.get("jid") == jid), None)
    if idx is None:
        print(json.dumps({"ok": False, "error": f"clients.json 中无此 jid: {jid}"}, ensure_ascii=False))
        return 1

    client = dict(clients[idx])
    before_pool = client.get("pool")
    changed = []

    for field in AGENT_SCALAR_FIELDS:
        if field in payload and payload[field] is not None:
            client[field] = payload[field]
            changed.append(field)

    # analyzed_at 默认现在
    if "analyzed_at" not in payload:
        client["analyzed_at"] = datetime.now().isoformat()
        if "analyzed_at" not in changed:
            changed.append("analyzed_at")
    client["analyzed_stale"] = False

    # script_history：若有新 script 且与上次不同则追加
    if payload.get("script"):
        hist = list(client.get("script_history") or [])
        last = hist[-1] if hist else None
        last_text = last.get("script") if isinstance(last, dict) else last
        if payload["script"] != last_text:
            hist.append({
                "script": payload["script"],
                "at": client.get("analyzed_at"),
                "result": None,
            })
            client["script_history"] = hist[-30:]
            changed.append("script_history")

    if "coach_log" in payload and payload["coach_log"]:
        client["coach_log"] = _append_unique(
            client.get("coach_log"), payload["coach_log"], key_fields=("type", "summary")
        )
        changed.append("coach_log")

    if "pool_history" in payload and payload["pool_history"]:
        client["pool_history"] = _append_unique(client.get("pool_history"), payload["pool_history"])
        changed.append("pool_history")
    elif "pool" in payload and payload["pool"] and payload["pool"] != before_pool:
        ph = list(client.get("pool_history") or [])
        ph.append({
            "from": before_pool,
            "to": payload["pool"],
            "reason": payload.get("pool_reason") or payload.get("diagnosis") or "agent",
            "at": client.get("analyzed_at"),
        })
        client["pool_history"] = ph[-30:]
        changed.append("pool_history")

    # promises：追加新承诺；promise_updates 按 id 更新状态
    if "promises" in payload and payload["promises"]:
        existing = list(client.get("promises") or [])
        for p in payload["promises"]:
            if not isinstance(p, dict):
                continue
            entry = {
                "id": p.get("id") or f"p_{datetime.now().strftime('%Y%m%d%H%M%S')}_{len(existing)}",
                "text": p.get("text") or "",
                "due": p.get("due"),
                "status": p.get("status") or "open",
                "created_at": p.get("created_at") or datetime.now().isoformat(),
                "source": p.get("source") or "agent",
            }
            if not entry["text"]:
                continue
            # 同 text+open 不重复
            if any(e.get("text") == entry["text"] and e.get("status") == "open" for e in existing):
                continue
            existing.append(entry)
        client["promises"] = existing[-40:]
        changed.append("promises")

    if "promise_updates" in payload and payload["promise_updates"]:
        existing = list(client.get("promises") or [])
        by_id = {e.get("id"): e for e in existing if isinstance(e, dict)}
        for u in payload["promise_updates"]:
            if not isinstance(u, dict):
                continue
            pid = u.get("id")
            if pid and pid in by_id:
                if u.get("status"):
                    by_id[pid]["status"] = u["status"]
                if u.get("note"):
                    by_id[pid]["note"] = u["note"]
                by_id[pid]["updated_at"] = datetime.now().isoformat()
        client["promises"] = list(by_id.values()) if by_id else existing
        changed.append("promises")

    if not changed:
        print(json.dumps({"ok": False, "error": "无有效字段可写"}, ensure_ascii=False))
        return 1

    backup = backup_clients_json()
    clients[idx] = client
    atomic_write_json(CLIENTS_JSON, clients)
    result = {
        "ok": True,
        "jid": jid,
        "name": client.get("name"),
        "changed": changed,
        "backup": backup,
        "pool": client.get("pool"),
        "intent": client.get("intent"),
        "promises_open": sum(1 for p in (client.get("promises") or []) if p.get("status") == "open"),
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


def save_report(payload):
    """保存报告快照 → last_report.json + report_history/日期.json，供昨日复盘。"""
    if isinstance(payload, str):
        payload = _parse_payload(payload)
    if not isinstance(payload, dict):
        print(json.dumps({"ok": False, "error": "payload 必须是对象"}, ensure_ascii=False))
        return 1

    now = datetime.now()
    snapshot = {
        "generated_at": payload.get("generated_at") or now.isoformat(),
        "date": payload.get("date") or now.strftime("%Y-%m-%d"),
        "owner_name": payload.get("owner_name") or load_config().get("owner_name") or "owner",
        "top3": payload.get("top3") or [],
        "actions": payload.get("actions") or [],
        "temperatures": payload.get("temperatures") or {},
        "promises_open": payload.get("promises_open") or [],
        "risks": payload.get("risks") or [],
        "one_liner": payload.get("one_liner") or "",
        "notes": payload.get("notes") or "",
    }
    # 若未传 promises_open，从 clients 汇总 open 承诺
    if not snapshot["promises_open"] and CLIENTS_JSON.exists():
        open_ps = []
        for c in load_clients_list():
            for p in c.get("promises") or []:
                if isinstance(p, dict) and p.get("status") == "open":
                    open_ps.append({
                        "jid": c.get("jid"),
                        "name": c.get("name"),
                        "id": p.get("id"),
                        "text": p.get("text"),
                        "due": p.get("due"),
                    })
        snapshot["promises_open"] = open_ps

    atomic_write_json(LAST_REPORT, snapshot)
    REPORT_HISTORY_DIR.mkdir(parents=True, exist_ok=True)
    hist_path = REPORT_HISTORY_DIR / f"{snapshot['date']}.json"
    atomic_write_json(hist_path, snapshot)
    # 只保留最近 30 天历史
    histories = sorted(REPORT_HISTORY_DIR.glob("*.json"))
    for old in histories[:-30]:
        try:
            old.unlink()
        except OSError:
            pass

    print(json.dumps({
        "ok": True,
        "path": str(LAST_REPORT),
        "history": str(hist_path),
        "top3_count": len(snapshot["top3"]),
        "actions_count": len(snapshot["actions"]),
        "promises_open_count": len(snapshot["promises_open"]),
    }, ensure_ascii=False, indent=2))
    return 0


def detail(jid):
    """输出单个客户完整对话 + customer_style_stats + 已有分析/承诺。"""
    cfg = load_config()
    db_path = Path(cfg["db_path"]).expanduser()
    owner_name = cfg.get("owner_name") or "owner"

    if not db_path.exists():
        print(json.dumps({"error": f"数据库不存在: {db_path}"}, ensure_ascii=False))
        return

    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row

    chat = conn.execute("SELECT jid, name, last_message_ts FROM chats WHERE jid=?", (jid,)).fetchone()
    if not chat:
        print(json.dumps({"error": f"未找到客户: {jid}"}, ensure_ascii=False))
        conn.close()
        return

    msgs = conn.execute("""
        SELECT text, display_text, from_me, ts
        FROM messages WHERE chat_jid=? AND text IS NOT NULL
        ORDER BY ts DESC LIMIT 30
    """, (jid,)).fetchall()

    old = {}
    if CLIENTS_JSON.exists():
        with open(CLIENTS_JSON, encoding="utf-8") as f:
            for c in json.load(f):
                if c["jid"] == jid:
                    old = c
                    break

    recent = []
    for m in reversed(msgs[:15]):
        text = (m["text"] or m["display_text"] or "").strip()
        if text:
            recent.append({"role": owner_name if m["from_me"] else "客户", "text": text})

    msg_dicts = [
        {"text": m["text"], "display_text": m["display_text"], "from_me": bool(m["from_me"]), "ts": m["ts"]}
        for m in msgs
    ]
    style_stats = compute_customer_style_stats(msg_dicts, owner_name)

    country, offset = get_country(jid, cfg["country_tz"])

    # 昨日报告中与该客户相关的行动
    yesterday_actions = []
    if LAST_REPORT.exists():
        try:
            lr = json.loads(LAST_REPORT.read_text(encoding="utf-8"))
            for a in lr.get("actions") or []:
                if a.get("jid") == jid or a.get("name") == (chat["name"] or ""):
                    yesterday_actions.append(a)
        except (json.JSONDecodeError, OSError):
            pass

    output = {
        "jid": jid,
        "name": chat["name"] or jid.split("@")[0],
        "country": country,
        "utc_offset": offset,
        "local_hour": get_local_hour(offset),
        "recent_conversation": recent,
        "customer_style_stats": style_stats,
        "intent": old.get("intent"),
        "stage": old.get("stage"),
        "diagnosis": old.get("diagnosis"),
        "script": old.get("script"),
        "script_history": old.get("script_history", []),
        "coach_log": old.get("coach_log", []),
        "pool": old.get("pool", "B"),
        "pool_suggestion": old.get("pool_suggestion"),
        "temperature": old.get("temperature"),
        "promises": old.get("promises", []),
        "analyzed_at": old.get("analyzed_at"),
        "analyzed_stale": old.get("analyzed_stale"),
        "yesterday_actions": yesterday_actions,
    }
    conn.close()
    print(json.dumps(output, ensure_ascii=False, indent=2))

def extract():
    cfg = load_config()
    db_path = Path(cfg["db_path"]).expanduser()

    if not db_path.exists():
        print(f"❌ 数据库不存在: {db_path}")
        return

    # === 自动备份数据库（每天只备份一次）===
    if db_path.exists():
        BACKUP_DIR.mkdir(parents=True, exist_ok=True)
        today_str = datetime.now().strftime("%Y%m%d")
        existing_today = list(BACKUP_DIR.glob(f"wacli_backup_{today_str}_*.db"))
        if not existing_today:
            ts = datetime.now().strftime("%Y%m%d_%H%M")
            backup_path = BACKUP_DIR / f"wacli_backup_{ts}.db"
            shutil.copy2(db_path, backup_path)
            # 只保留最近7个备份
            backups = sorted(BACKUP_DIR.glob("wacli_backup_*.db"))
            for old in backups[:-7]:
                old.unlink()
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    now_ts = int(datetime.now().timestamp())

    own_phone = cfg["owner_phone"]
    tz_map = cfg["country_tz"]

    chats = conn.execute("""
        SELECT jid, name, last_message_ts FROM chats
        WHERE jid NOT IN (?, '0@s.whatsapp.net', 'status@broadcast')
        AND jid NOT LIKE '%@g.us'
        AND jid NOT LIKE '86%'
        AND last_message_ts IS NOT NULL
        ORDER BY last_message_ts DESC
    """, (f"{own_phone}@s.whatsapp.net",)).fetchall()

    old_clients = {}
    if CLIENTS_JSON.exists():
        with open(CLIENTS_JSON) as f:
            for c in json.load(f):
                old_clients[c["jid"]] = c

    # 从 config.json 读取上次提取时间（避免 Agent 写回 clients.json 导致 mtime 污染）
    last_extract_at = cfg.get("last_extract_at")
    if last_extract_at:
        try:
            last_extract_ts = int(datetime.fromisoformat(last_extract_at).timestamp())
        except (ValueError, TypeError):
            last_extract_ts = 0
    else:
        last_extract_ts = 0

    # 热池容量保护：统计当前热池人数，自动升级时防止超限
    hot_count = sum(1 for c in old_clients.values() if c.get("pool") == "hot")
    hot_limit = cfg["pools"]["hot_max_size"]

    clients = []
    phrase_stats = {}
    for chat in chats:
        jid = chat["jid"]
        name = chat["name"] or jid.split("@")[0]
        days = (now_ts - chat["last_message_ts"]) // 86400
        country, offset = get_country(jid, tz_map)
        old = old_clients.get(jid, {})

        msgs = conn.execute("""
            SELECT text, display_text, from_me, ts
            FROM messages WHERE chat_jid=? AND text IS NOT NULL
            ORDER BY ts DESC LIMIT 30
        """, (jid,)).fetchall()

        cust_msgs = [m for m in msgs if not m["from_me"]]
        my_msgs = [m for m in msgs if m["from_me"]]
        cust_text = " ".join((m["text"] or "") for m in cust_msgs).lower()
        cust_count = len(cust_msgs)
        latest_cust_ts = cust_msgs[0]["ts"] if cust_msgs else chat["last_message_ts"] or 0
        age_hours = (now_ts - latest_cust_ts) / 3600 if latest_cust_ts else 99999

        priority = compute_priority(cust_text, cust_count, age_hours, cfg)
        products = extract_products(name, cust_text, my_msgs, cfg)
        detection = auto_detect(msgs, cfg)
        last_script = old.get("script")
        script_track = track_script(msgs, last_script)
        phrase_stats = update_phrase_correlations(msgs, phrase_stats, cfg)

        analyzed_at = old.get("analyzed_at")
        analyzed_stale = False
        if analyzed_at and chat["last_message_ts"]:
            try:
                at = datetime.fromisoformat(analyzed_at).timestamp()
                analyzed_stale = chat["last_message_ts"] > at
            except:
                analyzed_stale = True

        old_last_cust_ts = old.get("last_customer_message_ts", 0)
        has_new_cust_msg = latest_cust_ts > old_last_cust_ts if latest_cust_ts else False

        old_pool = old.get("pool", "B")
        pool_sug = pool_suggestion(priority, days, old_pool, has_new_cust_msg, cfg)

        # 机会温度先算（自动入热池门槛依赖温度）
        old_temp = old.get("temperature", {}).get("value") if old.get("temperature") else None
        temperature = compute_temperature(cust_msgs, days, detection["pursuit_warning"], cfg)
        temp_trend = "→"
        if old_temp is not None:
            diff = temperature - old_temp
            if diff >= 8: temp_trend = "↑"
            elif diff <= -8: temp_trend = "↓"
        temp_history = old.get("temperature_history", [])
        if old_temp is not None and temp_trend != "→":
            temp_history.append({
                "date": datetime.now().strftime("%Y-%m-%d"),
                "from": old_temp, "to": temperature, "trend": temp_trend
            })
        temp_history = temp_history[-30:]

        # 自动入热：须过 hot_entry 门槛 + 容量；否则仅保留 pool_suggestion 给 Agent
        if old_pool == "B" and can_auto_upgrade_to_hot(
            priority, days, has_new_cust_msg, cust_count,
            detection["pursuit_warning"], temperature, cfg,
        ):
            if hot_count < hot_limit:
                old_pool = "hot"
                pool_sug = None
                hot_count += 1
            else:
                pool_sug = "upgrade_to_hot"
        elif cust_count == 0 and days > 30 and old_pool == "B":
            old_pool = "dormant"
            pool_sug = None

        client = {
            "jid": jid, "name": name, "country": country,
            "utc_offset": offset, "local_hour": get_local_hour(offset),
            "priority": priority, "days_silent": days,
            "cust_msg_count": cust_count, "my_msg_count": len(my_msgs),
            "products": products, "last_message_ts": chat["last_message_ts"],
            "last_customer_message_ts": latest_cust_ts,
            "is_new_message": chat["last_message_ts"] > last_extract_ts,
            "is_new_customer_message": has_new_cust_msg,
            "detection": {"pursuit_warning": detection["pursuit_warning"],
                          "max_consecutive_owner_msgs": detection["max_consecutive_owner_msgs"],
                          "weak_phrase_count": detection["weak_phrase_count"],
                          "weak_phrase_samples": detection["weak_phrase_samples"]},
            "script_tracking": script_track,
            "pool": old_pool,
            "pool_suggestion": pool_sug,
            "analyzed_stale": analyzed_stale,
            "temperature": {"value": temperature, "trend": temp_trend},
            "temperature_history": temp_history,
            "intent": old.get("intent"),
            "stage": old.get("stage"),
            "diagnosis": old.get("diagnosis"),
            "script": old.get("script"),
            "send_time": old.get("send_time"),
            "risk": old.get("risk"),
            "coach_note": old.get("coach_note"),
            "analyzed_at": old.get("analyzed_at"),
            "script_history": old.get("script_history", []),
            "coach_log": old.get("coach_log", []),
            "pool_history": old.get("pool_history", []),
            "promises": old.get("promises", []),
        }
        clients.append(client)

    conn.close()
    # 进化管线：风格指纹 → 模式发现 → 统计聚合 → 自动裁决
    profile = compute_correlations(phrase_stats, cfg, len(clients))
    pending = discover_patterns(phrase_stats, cfg, profile)
    aggregate_stats(clients, cfg, profile)
    added = apply_auto_rules(pending, cfg)
    if added:
        print(f"   🔍 自动加入新模式: {added}")
    # 写入两个文件：轻量摘要 + 完整元数据
    CLIENTS_JSON.parent.mkdir(parents=True, exist_ok=True)

    # 统计
    high = sum(1 for c in clients if c["priority"] == "high")
    med = sum(1 for c in clients if c["priority"] == "medium")
    low = sum(1 for c in clients if c["priority"] == "low")
    analyzed = sum(1 for c in clients if c.get("analyzed_at"))
    stale = sum(1 for c in clients if c.get("analyzed_stale"))
    pursuit = sum(1 for c in clients if c["detection"]["pursuit_warning"])
    with_sug = sum(1 for c in clients if c.get("pool_suggestion"))
    hot = sum(1 for c in clients if c.get("pool") == "hot")
    b_pool = sum(1 for c in clients if c.get("pool") == "B")
    new_msg = sum(1 for c in clients if c.get("is_new_customer_message"))
    
    # 销售行为统计
    total_owner_msgs = sum(c["my_msg_count"] for c in clients)
    pursuit_count = sum(1 for c in clients if c["detection"]["pursuit_warning"])
    broken_promises = sum(
        len([e for e in c.get("coach_log", [])
             if isinstance(e, dict) and e.get("pattern") == "broken_promise"])
        for c in clients
    )
    wait_signals = ["wait", "later", "next month", "not now", "两个月", "2 month", "明年"]
    chase_after_wait = 0
    for c in clients:
        if c["detection"]["pursuit_warning"]:
            cust_texts = " ".join(
                (m["text"] or "").lower()
                for m in []  # We don't have raw msgs here, skip for now
            )
    
    behavior_stats = {
        "active_followups": total_owner_msgs,
        "excessive_followups": pursuit_count,
        "broken_promises": broken_promises,
    }
    # 写入行为统计文件
    stats_path = SCRIPT_DIR / "data" / "behavior_stats.json"
    old_stats = {}
    if stats_path.exists():
        with open(stats_path) as f:
            old_stats = json.load(f)
    
    weekly = {
        "generated_at": datetime.now().isoformat(),
        "current": behavior_stats,
        "previous": old_stats.get("current", {}),
    }
    with open(stats_path, "w") as f:
        json.dump(weekly, f, ensure_ascii=False, indent=2)
    
    # summary.json — Agent 首先读的文件（极小，只用于选客户）
    summary = {
        "generated_at": datetime.now().isoformat(),
        "total": len(clients),
        "pools": {"hot": hot, "B": b_pool},
        "new_customer_messages": new_msg,
        "stale_analyses": stale,
        "pursuit_warnings": pursuit,
        "priority": {"high": high, "medium": med, "low": low},
        "hot_pool": [
            {"jid": c["jid"], "name": c["name"][:35], "country": c["country"],
             "priority": c["priority"], "days_silent": c["days_silent"],
             "products": c["products"], "pool": c["pool"],
             "pool_suggestion": c.get("pool_suggestion"),
             "analyzed_stale": c.get("analyzed_stale"),
             "is_new_customer_message": c.get("is_new_customer_message")}
            for c in clients if c["pool"] == "hot"
        ],
        "b_pool_sample": [
            {"jid": c["jid"], "name": c["name"][:35], "country": c["country"],
             "priority": c["priority"], "days_silent": c["days_silent"],
             "products": c["products"], "cust_msg_count": c["cust_msg_count"]}
            for c in clients if c["pool"] == "B"
        ][:10],
        "needs_attention": [
            {"jid": c["jid"], "name": c["name"][:35], "country": c["country"],
             "priority": c["priority"], "days_silent": c["days_silent"],
             "products": c["products"], "pool": c["pool"],
             "pool_suggestion": c.get("pool_suggestion"),
             "analyzed_stale": c.get("analyzed_stale"),
             "is_new_customer_message": c.get("is_new_customer_message")}
            for c in clients
            if c.get("analyzed_stale") or c.get("is_new_customer_message")
        ][:20],
    }
    summary_path = SCRIPT_DIR / "data" / "summary.json"
    atomic_write_json(summary_path, summary)

    # clients.json — 完整元数据（原子写，防半写损坏）
    atomic_write_json(CLIENTS_JSON, clients)

    try:
        print(f"✅ extract.py 完成")
        print(f"   总客户: {len(clients)}  池子: hot={hot} B={b_pool} dormant={sum(1 for c in clients if c.get('pool')=='dormant')}")
        print(f"   priority: high={high}  medium={med}  low={low}")
        print(f"   新客户消息: {new_msg}  |  待重分析: {stale}")
        print(f"   轰炸警告: {pursuit}  |  池子建议: {with_sug}")
        print(f"   文件大小: {round(CLIENTS_JSON.stat().st_size/1024)}KB")
        print(f"   输出: {CLIENTS_JSON}")
    except BrokenPipeError:
        pass

    # 写回 config.json：更新 last_extract_at（避免 Agent 写回 clients.json 导致 mtime 污染）
    cfg["last_extract_at"] = datetime.now().isoformat()
    with open(CONFIG, "w") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Sarah 数据提取 / 详情 / 安全写回 / 报告快照")
    parser.add_argument("--detail", help="输出单个客户完整对话 + customer_style_stats")
    parser.add_argument(
        "--write-analysis",
        metavar="JSON",
        help="安全合并写回 Agent 分析。JSON 或 @file.json，必须含 jid",
    )
    parser.add_argument(
        "--save-report",
        metavar="JSON",
        help="保存报告快照到 data/last_report.json。JSON 或 @file.json",
    )
    args = parser.parse_args()
    if args.write_analysis:
        sys.exit(write_analysis(args.write_analysis) or 0)
    if args.save_report:
        sys.exit(save_report(args.save_report) or 0)
    if args.detail:
        detail(args.detail)
    else:
        extract()
