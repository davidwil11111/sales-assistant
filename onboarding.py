#!/usr/bin/env python3
"""
onboarding.py — Sarah 销售系统智能引导脚本

从 wacli.db 自动探测可配置信息，生成 JSON 报告供 Agent 使用。
Agent 向用户展示探测结果 → 交互确认 → 生成 config.json。

用法:
  python3 onboarding.py scan              # 扫描数据库，输出探测报告 JSON
  python3 onboarding.py write <json>      # 将确认后的配置写入 config.json
"""
import sqlite3, json, re, sys, os
from pathlib import Path
from collections import Counter
from datetime import datetime

SCRIPT_DIR = Path(__file__).resolve().parent
CONFIG_PATH = SCRIPT_DIR / "config.json"


def load_config():
    with open(CONFIG_PATH) as f:
        return json.load(f)


def scan():
    """扫描 wacli.db，自动探测配置信息"""
    cfg = load_config()
    db_path = Path(os.path.expanduser(cfg.get("db_path", "")))

    result = {
        "status": "ok",
        "db_exists": False,
        "db_message_count": 0,
        "db_chat_count": 0,
        # ---- 自动探测结果 ----
        "detected_owner_phone": None,
        "detected_countries": [],        # [{country, count, prefix}]
        "detected_name_keywords": [],     # 客户名中的高频词 [{word, count}]
        "detected_chat_keywords": [],     # 对话中的高频产品关键词 [{word, count}]
        "detected_languages": [],         # [{lang, count}]
        "detected_capacity_samples": [],  # 规格数字样本 ["5吨", "20t", ...]
        "sample_customer_names": [],      # 前20个客户名样本
        "sample_messages": [],            # 前10条客户消息样本
        "country_tz_map": cfg.get("country_tz", {}),
        "current_industry": cfg.get("industry", {}),
    }

    if not db_path.exists():
        result["status"] = "no_db"
        result["error"] = f"数据库不存在: {db_path}"
        return result

    result["db_exists"] = True
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row

    # ---- 1. 统计基本信息 ----
    try:
        msg_count = conn.execute("SELECT COUNT(*) FROM messages").fetchone()[0]
        chat_count = conn.execute("SELECT COUNT(*) FROM chats").fetchone()[0]
        result["db_message_count"] = msg_count
        result["db_chat_count"] = chat_count
    except:
        conn.close()
        result["status"] = "db_read_error"
        result["error"] = "数据库不可读"
        return result

    # ---- 2. 探测 owner_phone ----
    try:
        own = conn.execute("""
            SELECT chat_jid, COUNT(*) as cnt
            FROM messages WHERE from_me=1
            GROUP BY chat_jid ORDER BY cnt DESC LIMIT 1
        """).fetchone()
        if own:
            phone = own["chat_jid"].split("@")[0]
            if phone.isdigit():
                result["detected_owner_phone"] = phone
    except:
        pass

    # ---- 3. 探测客户国家分布 ----
    tz_map = cfg.get("country_tz", {})
    try:
        chats = conn.execute("""
            SELECT jid, name FROM chats
            WHERE jid NOT LIKE '%@g.us' AND jid NOT LIKE 'status%' AND jid NOT LIKE '86%'
        """).fetchall()
        country_counts = Counter()
        for c in chats:
            phone = c["jid"].split("@")[0]
            for length in [4, 3, 2, 1]:
                prefix = phone[:length]
                if prefix in tz_map:
                    country_counts[tz_map[prefix][0]] += 1
                    break
        result["detected_countries"] = [
            {"country": k, "count": v, "prefix": None}
            for k, v in country_counts.most_common(20)
        ]
    except:
        pass

    # ---- 4. 探测客户名中的关键词 ----
    stop_words = {"customer", "client", "buyer", "supplier", "company", "group",
                  "trading", "import", "export", "general", "international",
                  "ltd", "llc", "co", "inc", "corp", "srl", "ltda", "sa",
                  "engineering", "industries", "industrial", "equipment",
                  "machinery", "steel", "metal", "heavy", "the", "and", "for",
                  "of", "de", "la", "el", "al", "un", "una", "del"}
    try:
        name_words = Counter()
        for c in chats:
            name = (c["name"] or "").lower()
            # 分词（简单按空格和非字母数字切分）
            words = re.findall(r'[a-zA-Z\u4e00-\u9fff]{3,}', name)
            for w in words:
                if w not in stop_words:
                    name_words[w] += 1
        result["detected_name_keywords"] = [
            {"word": k, "count": v}
            for k, v in name_words.most_common(30)
        ]
        # 客户名样本
        sample_names = conn.execute(
            "SELECT name FROM chats WHERE name IS NOT NULL AND name != '' LIMIT 20"
        ).fetchall()
        result["sample_customer_names"] = [n["name"] for n in sample_names]
    except:
        pass

    # ---- 5. 探测对话中的产品关键词 ----
    try:
        # 取最近的客户消息，提取高频实词
        msgs = conn.execute("""
            SELECT text FROM messages
            WHERE from_me=0 AND text IS NOT NULL AND text != ''
            ORDER BY ts DESC LIMIT 200
        """).fetchall()
        chat_words = Counter()
        for m in msgs:
            text = (m["text"] or "").lower()
            words = re.findall(r'[a-zA-Z]{4,}', text)
            for w in words:
                if w not in stop_words and w not in (
                    "what", "that", "this", "with", "have", "from", "your",
                    "about", "please", "thanks", "would", "could", "should",
                    "hello", "there", "just", "like", "some", "will", "when",
                    "where", "which", "them", "then", "also", "very", "much",
                    "need", "want", "know", "think", "send", "tell", "said",
                    "well", "still", "back", "more", "here", "been", "into",
                    "other", "after", "over", "only", "most", "even", "good",
                ):
                    chat_words[w] += 1
        result["detected_chat_keywords"] = [
            {"word": k, "count": v}
            for k, v in chat_words.most_common(30)
        ]
        # 消息样本
        sample_msgs = conn.execute("""
            SELECT text, from_me FROM messages
            WHERE text IS NOT NULL AND text != ''
            ORDER BY ts DESC LIMIT 10
        """).fetchall()
        result["sample_messages"] = [
            {"from_me": m["from_me"], "text": (m["text"] or "")[:200]}
            for m in sample_msgs
        ]
    except:
        pass

    # ---- 6. 探测容量/规格数字 ----
    try:
        capacity_patterns = re.findall(r'(\d+\.?\d*)\s*([tT]on|[tT]|吨|kg|kgs|mm|cm|m|w|kw|hp|pcs|sets?)',
                                       " ".join(n["name"] or "" for n in chats))
        seen = set()
        samples = []
        for num, unit in capacity_patterns:
            s = f"{num}{unit}"
            if s not in seen:
                samples.append(s)
                seen.add(s)
        result["detected_capacity_samples"] = samples[:20]
    except:
        pass

    # ---- 7. 探测语言 ----
    lang_markers = {
        "Arabic": re.compile(r'[\u0600-\u06ff]'),
        "Spanish": re.compile(r'\b(?:que|los|las|por|para|como|esta|muy|gracias|hola|buenos)\b', re.I),
        "French": re.compile(r'\b(?:vous|nous|pour|dans|avec|cette|bonjour|merci|très|êtes)\b', re.I),
        "Portuguese": re.compile(r'\b(?:você|para|como|muito|obrigado|bom|dia|não|sim)\b', re.I),
        "Russian": re.compile(r'[\u0400-\u04ff]'),
    }
    try:
        lang_counts = Counter()
        for m in msgs:
            text = (m["text"] or "")
            for lang, pattern in lang_markers.items():
                if pattern.search(text):
                    lang_counts[lang] += 1
        result["detected_languages"] = [
            {"language": k, "count": v}
            for k, v in lang_counts.most_common()
        ]
    except:
        pass

    conn.close()
    return result


def write_config(data):
    """将确认后的配置写入 config.json"""
    cfg = load_config()

    # 更新基础信息
    if data.get("owner_name"):
        cfg["owner_name"] = data["owner_name"]
    if data.get("owner_phone"):
        cfg["owner_phone"] = data["owner_phone"]
    if data.get("owner_timezone"):
        cfg["owner_timezone"] = data["owner_timezone"]

    # 更新行业配置
    ind = data.get("industry", {})
    if ind.get("name"):
        cfg["industry"]["name"] = ind["name"]
    if ind.get("persona_years"):
        cfg["industry"]["persona_years"] = ind["persona_years"]
    if ind.get("product_keywords"):
        cfg["industry"]["product_keywords"] = ind["product_keywords"]
    if ind.get("capacity_pattern"):
        cfg["industry"]["capacity_pattern"] = ind["capacity_pattern"]
    if ind.get("capacity_label"):
        cfg["industry"]["capacity_label"] = ind["capacity_label"]
    if ind.get("context_keywords"):
        cfg["industry"]["context_keywords"] = ind["context_keywords"]
    if ind.get("context_label_hints"):
        cfg["industry"]["context_label_hints"] = ind["context_label_hints"]
    if ind.get("temperature_demand_signals"):
        cfg["industry"]["temperature_demand_signals"] = ind["temperature_demand_signals"]

    # 激活
    cfg["_status"] = "active"

    with open(CONFIG_PATH, "w") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)

    return {"status": "ok", "config_path": str(CONFIG_PATH)}


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("用法: python3 onboarding.py scan|write")
        sys.exit(1)

    cmd = sys.argv[1]

    if cmd == "scan":
        result = scan()
        print(json.dumps(result, ensure_ascii=False, indent=2))

    elif cmd == "write":
        if len(sys.argv) < 3:
            print(json.dumps({"error": "缺少配置数据，用法: python3 onboarding.py write '<json>'"}))
            sys.exit(1)
        try:
            data = json.loads(sys.argv[2])
        except json.JSONDecodeError as e:
            print(json.dumps({"error": f"JSON 解析失败: {e}"}))
            sys.exit(1)
        result = write_config(data)
        print(json.dumps(result, ensure_ascii=False, indent=2))

    else:
        print(json.dumps({"error": f"未知命令: {cmd}"}))
        sys.exit(1)
