#!/usr/bin/env python3
"""
extract.py — Sarah销售系统唯一数据脚本

默认模式: SQLite → clients.json（元数据，不含对话原文，轻量文件）
详情模式: --detail JID → 输出单个客户的完整对话到 stdout

用法:
  python3 extract.py                  # 生成 clients.json
  python3 extract.py --detail <JID>   # 输出单个客户详情
"""
import sqlite3, json, re, sys, argparse
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

def load_config():
    with open(CONFIG) as f:
        return json.load(f)

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
    context_kws = ind.get("context_keywords", ["crane"])
    label_hints = ind.get("context_label_hints", [])

    products = []
    name_lower = name.lower()

    # 1. 从客户名匹配关键词
    for kw, label in kw_map.items():
        if kw in name_lower and label not in products:
            products.append(label)

    # 2. 容量/规格提取（如吨位），使用可配置的正则
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
    pools = cfg["pools"]
    if not old_pool:
        old_pool = "B"
    if old_pool == "hot" and days > pools["hot_max_silent_days"]:
        return "downgrade_to_B"
    if old_pool == "B" and has_new_cust_msg and priority in ("high", "medium"):
        return "upgrade_to_hot"
    if old_pool in ("dormant", "archive") and has_new_cust_msg:
        return "upgrade_to_hot"
    if old_pool == "B" and days > pools["B_max_silent_days"]:
        return "downgrade_to_dormant"
    return None

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
    """机会温度 0-100：从客户聊天里扫描具体成交信号，不是靠猜
    
    高温信号（客户在主动推动交易）：
    - invoice/PI/银行账号/付款方式/发货/包装/CE证书/来厂验货
    
    中温信号（客户在评估）：
    - 配置/技术参数/图纸/报价（关键词来自 config.json industry.temperature_demand_signals）
    
    低温信号（客户在了解）：
    - 公司介绍/产品目录/初步询价
    
    降温信号：
    - 沉默>7天 / 连发轰炸 / 客户说"等等"
    """
    temp = 10  # 基础温度：有对话
    
    # 扫描近30条客户消息
    customer_texts = [(m["text"] or "").lower() for m in cust_msgs]
    all_text = " ".join(customer_texts)
    
    # 高温信号：+15分/个
    hot_signals = [
        "invoice", "pi", "proforma", "bank", "account", "payment",
        "deposit", "delivery", "ship", "packing", "ce certif",
        "visit factory", "come to china", "visit china",
        "swift", "iban", "transfer", "remit", "tt", "l/c", "lc ",
        "confirm order", "place order", "purchase order", "po ",
    ]
    for sig in hot_signals:
        if sig in all_text:
            temp += 15
    
    # 中温信号：+10分/个 — 通用成交信号 + 行业技术信号（来自config）
    mid_signals = [
        "quote", "quotation", "price", "fob", "cif",
        "warranty", "certif", "standard",
    ]
    # 追加行业配置的技术讨论信号
    if cfg:
        ind = cfg.get("industry", {})
        for sig in ind.get("temperature_demand_signals", []):
            if sig not in mid_signals:
                mid_signals.append(sig)
    for sig in mid_signals:
        if sig in all_text:
            temp += 10
    
    # 客户提问信号（问句=兴趣）：+5分/个
    question_markers = ["?", "how", "what", "when", "where", "can you", "do you", "is it", "could you", "would you"]
    q_count = sum(1 for t in customer_texts if any(m in t for m in question_markers))
    temp += min(q_count * 5, 15)
    
    # 降温
    if days_silent > 7:
        temp -= 10
    if days_silent > 14:
        temp -= 15
    if pursuit_warning:
        temp -= 10
    
    # 客户明确说等/延迟
    wait_signals = ["wait", "later", "next month", "next year", "not now", "next week", "next time",
                    "两个月", "2 month", "two month", "明年", "过段时间"]
    for sig in wait_signals:
        if sig in all_text:
            temp -= 15
            break
    
    return max(0, min(100, temp))

def update_phrase_correlations(msgs, phrase_stats, cfg):
    """追踪David的每条消息→客户48h内是否回复"""
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

def detail(jid):
    """输出单个客户的完整对话到 stdout"""
    cfg = load_config()
    db_path = Path(cfg["db_path"]).expanduser()
    owner_name = cfg["owner_name"]

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

    # 加载旧 clients.json 中的 Agent 分析字段
    old = {}
    if CLIENTS_JSON.exists():
        with open(CLIENTS_JSON) as f:
            for c in json.load(f):
                if c["jid"] == jid:
                    old = c
                    break

    recent = []
    for m in reversed(msgs[:15]):
        text = (m["text"] or m["display_text"] or "").strip()
        if text:
            recent.append({"role": owner_name if m["from_me"] else "客户", "text": text})

    cfg_country = cfg["country_tz"]
    country, offset = get_country(jid, cfg_country)

    output = {
        "jid": jid,
        "name": chat["name"] or jid.split("@")[0],
        "country": country,
        "utc_offset": offset,
        "local_hour": get_local_hour(offset),
        "recent_conversation": recent,
        # Agent 已有分析
        "intent": old.get("intent"),
        "stage": old.get("stage"),
        "diagnosis": old.get("diagnosis"),
        "script": old.get("script"),
        "script_history": old.get("script_history", []),
        "coach_log": old.get("coach_log", []),
        "pool": old.get("pool", "B"),
        "analyzed_at": old.get("analyzed_at"),
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

        # 自动裁决：priority=high + B池 → 入热池（受容量保护）；0客户消息+超30天 → 入沉默池
        if priority == "high" and old_pool == "B":
            if hot_count < hot_limit:
                old_pool = "hot"
                pool_sug = None
                hot_count += 1
            else:
                pool_sug = "upgrade_to_hot"  # 热池满，保留在B池顶部
        elif cust_count == 0 and days > 30 and old_pool == "B":
            old_pool = "dormant"
            pool_sug = None

        # 机会温度（0-100）：从聊天信号扫描，不是猜
        old_temp = old.get("temperature", {}).get("value") if old.get("temperature") else None
        temperature = compute_temperature(cust_msgs, days, detection["pursuit_warning"], cfg)
        temp_trend = "→"
        if old_temp is not None:
            diff = temperature - old_temp
            if diff >= 8: temp_trend = "↑"
            elif diff <= -8: temp_trend = "↓"
        # 机会温度历史追踪
        temp_history = old.get("temperature_history", [])
        if old_temp is not None and temp_trend != "→":
            temp_history.append({
                "date": datetime.now().strftime("%Y-%m-%d"),
                "from": old_temp, "to": temperature, "trend": temp_trend
            })
        temp_history = temp_history[-30:]  # 只保留最近30条

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
    with open(summary_path, "w") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)

    # clients.json — 完整元数据（Workflow 可能用到）
    with open(CLIENTS_JSON, "w") as f:
        json.dump(clients, f, ensure_ascii=False, indent=2)

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
    parser = argparse.ArgumentParser()
    parser.add_argument("--detail", help="输出单个客户的完整对话")
    args = parser.parse_args()
    if args.detail:
        detail(args.detail)
    else:
        extract()
