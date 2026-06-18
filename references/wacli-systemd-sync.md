# WaCLI 同步方案

> ⚠️ 安全红线：wacli 唯一允许的操作是 `sync`（`--once` 或 `--follow`）+ `auth status`。任何其他命令都禁止。

## 当前方案：定时一次性同步（测试号）

**为什么不用长连接：** 测试号消息极少，WhatsApp 会断开空闲 WebSocket。断开→重连→EOF→循环→限流。

**2小时 cron job：** `wacli sync --once --account test`
- 连接→同步→空闲30秒→自动退出
- 不保持长连接，无断开问题
- 失败时写 `/tmp/wacli_sync_last_fail`

**健康检查：** 15分钟 cron → `wacli-health-check.py`
- 检查 sync 成功/失败时间戳
- 检查数据库新鲜度
- 异常写文件，不推送微信

## 业务号方案（上线时切换）

**systemd service：** `wacli sync --account prod --max-reconnect 10m --max-db-size 500MB --max-messages 50000`
- `--max-reconnect 10m` = wacli 内置安全网（重连超10分钟自动停）
- 可选：加 `scripts/wacli-safe-sync.py` 包装器（指数退避+熔断）

## 常用排查命令

```bash
# 查看认证状态
wacli auth status --account test

# 一次性手动同步
wacli sync --once --account test

# 数据库统计
python3 -c "
import sqlite3, os
from datetime import datetime
db = os.path.expanduser('~/.local/state/wacli/accounts/test/wacli.db')
conn = sqlite3.connect(db)
cnt = conn.execute('SELECT COUNT(*) FROM messages').fetchone()[0]
ts = conn.execute('SELECT MAX(ts) FROM messages').fetchone()[0]
print(f'{cnt} msgs, latest: {datetime.fromtimestamp(ts)}')
"
```

## 存储限制

`wacli sync` 默认不限制存储。**必须添加：**
- `--max-db-size 500MB` — 数据库上限
- `--max-messages 50000` — 消息总数上限
