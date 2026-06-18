#!/usr/bin/env python3
"""
wacli 安全同步包装器
替代直接运行 `wacli sync --follow`，增加退避和熔断保护。

用法：python3 wacli-safe-sync.py --account test

行为：
- 正常同步消息
- WebSocket 断开时：指数退避重连（2s→4s→8s→16s→32s→64s→128s）
- 连续失败 ≥7 次（约4分钟）：熔断，停止重连，发送警报
- 熔断后 30 分钟内不重启

为什么：
wacli 内置重连无退避、无上限。
WhatsApp 检测频繁重连 → rate limit → connection reset → 封号。
这个包装器在 wacli 触发风控之前就停下来。
"""
import subprocess, sys, time, os, json
from datetime import datetime
from pathlib import Path

ALERT_FILE = Path("/tmp/wacli_circuit_breaker_alert")
CIRCUIT_BREAK = Path("/tmp/wacli_circuit_break_until")

MAX_CONSECUTIVE_FAILURES = 7
INITIAL_BACKOFF = 2       # 秒
MAX_BACKOFF = 128         # 秒
CIRCUIT_BREAK_MINUTES = 30


def send_alert(account, reason):
    """写入告警文件，由 cron 健康检查拾取"""
    alert = {
        "time": datetime.now().isoformat(),
        "account": account,
        "reason": reason,
        "severity": "critical"
    }
    ALERT_FILE.write_text(json.dumps(alert, ensure_ascii=False))
    print(f"🚨 熔断告警: {reason}")


def run_wacli(account):
    consecutive = 0
    backoff = INITIAL_BACKOFF
    
    cmd = [
        "wacli", "sync", "--follow",
        "--account", account,
        "--max-db-size", "500MB",
        "--max-messages", "50000"
    ]
    
    while True:
        # 检查是否在熔断期
        if CIRCUIT_BREAK.exists():
            try:
                until = datetime.fromisoformat(CIRCUIT_BREAK.read_text().strip())
                if datetime.now() < until:
                    remaining = (until - datetime.now()).total_seconds() / 60
                    print(f"⏸ 熔断中，{int(remaining)}分钟后恢复")
                    time.sleep(60)
                    continue
                else:
                    CIRCUIT_BREAK.unlink()
                    consecutive = 0
                    backoff = INITIAL_BACKOFF
                    print("🔄 熔断期结束，重新连接")
            except:
                CIRCUIT_BREAK.unlink()
        
        print(f"▶ 启动 wacli sync (account={account})")
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1
        )
        
        # 监控进程输出
        connected = False
        disconnect_time = None
        
        for line in proc.stdout:
            line = line.strip()
            
            if "Connected." in line:
                if not connected:
                    print(f"✅ {datetime.now().strftime('%H:%M:%S')} 已连接")
                connected = True
                consecutive = 0
                backoff = INITIAL_BACKOFF
                # 清除告警
                if ALERT_FILE.exists():
                    ALERT_FILE.unlink()
            
            elif "Disconnected." in line:
                if connected:
                    disconnect_time = time.time()
                    connected = False
            
            elif "Reconnecting..." in line:
                pass  # wacli 内部重连，我们不管
            
            elif "ERROR" in line or "error" in line.lower():
                print(f"  {line[:120]}")
                
                # 检查是否是 connection reset
                if "connection reset" in line.lower():
                    consecutive += 1
                    if consecutive >= MAX_CONSECUTIVE_FAILURES:
                        # 熔断
                        until = datetime.now().timestamp() + CIRCUIT_BREAK_MINUTES * 60
                        until_str = datetime.fromtimestamp(until).isoformat()
                        CIRCUIT_BREAK.write_text(until_str)
                        
                        reason = (
                            f"连续{consecutive}次 connection reset，"
                            f"熔断{CIRCUIT_BREAK_MINUTES}分钟。"
                            f"WhatsApp 正在拒绝连接，继续重连=封号风险。"
                        )
                        send_alert(account, reason)
                        
                        proc.terminate()
                        try:
                            proc.wait(timeout=10)
                        except:
                            proc.kill()
                        
                        print(f"🛑 熔断！{reason}")
                        break
        
        # 进程结束了
        if proc.poll() is not None:
            consecutive += 1
            
            if consecutive >= MAX_CONSECUTIVE_FAILURES:
                until = datetime.now().timestamp() + CIRCUIT_BREAK_MINUTES * 60
                until_str = datetime.fromtimestamp(until).isoformat()
                CIRCUIT_BREAK.write_text(until_str)
                
                reason = (
                    f"wacli 进程异常退出{consecutive}次，"
                    f"熔断{CIRCUIT_BREAK_MINUTES}分钟。"
                )
                send_alert(account, reason)
                print(f"🛑 熔断！{reason}")
            else:
                print(f"⚠ wacli 退出 (第{consecutive}次)，{backoff}秒后退避重连")
                time.sleep(backoff)
                backoff = min(backoff * 2, MAX_BACKOFF)


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--account", required=True, help="wacli account name")
    args = parser.parse_args()
    run_wacli(args.account)
