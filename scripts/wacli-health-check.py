#!/usr/bin/env python3
"""wacli 健康检查 — 定时同步模式（每2小时cron触发，不再持久连接）"""
import os, sys, sqlite3, json
from datetime import datetime
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent.parent
CONFIG_PATH = SCRIPT_DIR / "config.json"

def get_db_path():
    """从 config.json 读取数据库路径"""
    try:
        with open(CONFIG_PATH) as f:
            cfg = json.load(f)
        return os.path.expanduser(cfg.get("db_path", "~/.local/state/wacli/accounts/test/wacli.db"))
    except:
        return os.path.expanduser("~/.local/state/wacli/accounts/test/wacli.db")

def check():
    issues = []
    
    # 1. 最近一次同步是否成功
    ok_file = "/tmp/wacli_sync_last_ok"
    fail_file = "/tmp/wacli_sync_last_fail"
    
    last_ok = None
    last_fail = None
    
    if os.path.exists(ok_file):
        try:
            ts_str = open(ok_file).read().strip().split(" ", 1)[1]
            last_ok = datetime.fromisoformat(ts_str)
        except:
            pass
    
    if os.path.exists(fail_file):
        try:
            content = open(fail_file).read().strip()
            ts_str = content.split(" ", 1)[1]
            last_fail = datetime.fromisoformat(ts_str)
        except:
            pass
    
    # 同步失败且比上次成功晚 → 异常
    if last_fail and (not last_ok or last_fail > last_ok):
        hours = int((datetime.now() - last_fail).total_seconds() / 3600)
        issues.append(f"上次同步失败（{hours}小时前）")
    
    # 超过4小时没同步 → 异常
    if last_ok:
        hours = int((datetime.now() - last_ok).total_seconds() / 3600)
        if hours > 4:
            issues.append(f"上次同步成功在{hours}小时前")
    elif not last_fail:
        issues.append("从未同步过")
    
    # 2. 数据库是否存在且可读
    db_path = get_db_path()
    if not os.path.exists(db_path):
        issues.append("数据库文件不存在")
        return issues
    
    conn = sqlite3.connect(db_path)
    try:
        cnt = conn.execute("SELECT COUNT(*) FROM messages").fetchone()[0]
        latest = conn.execute("SELECT MAX(ts) FROM messages").fetchone()[0]
    except:
        issues.append("数据库无法读取")
        conn.close()
        return issues
    conn.close()
    
    if latest:
        hours_ago = (datetime.now().timestamp() - latest) / 3600
        if hours_ago > 12:
            issues.append(f"数据库最新消息{int(hours_ago)}小时前（{cnt}条）")
    else:
        issues.append("数据库无消息")
    
    return issues

if __name__ == "__main__":
    issues = check()
    if not issues:
        sys.exit(0)
    
    alert_msg = "⚠️ wacli同步异常"
    for i in issues:
        alert_msg += f"\n  {i}"
    print(alert_msg)
    sys.exit(1)
