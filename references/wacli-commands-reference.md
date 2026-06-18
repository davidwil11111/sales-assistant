# wacli 命令速查与安全红线

## 唯一允许的命令

### 定时同步（测试号/低流量号）
```bash
wacli sync --once --account <name> --max-db-size 500MB --max-messages 50000
```
`--once` = 连接→同步→空闲30秒→自动退出。无长连接，无WhatsApp断开风险。

### 持续同步（真实业务号，有频繁消息来往）
```bash
wacli sync --account <name> --max-reconnect 10m --max-db-size 500MB --max-messages 50000
```
`--max-reconnect` = 重连超过10分钟自动停止。wacli内置安全机制，不需要外部包装器。

### 检查认证状态
```bash
wacli auth status --account <name>
```

## 关键参数发现（2026-06-11）

| 参数 | 作用 | 之前不知道 |
|------|------|-----------|
| `--once` | 同步完自动退出（默认是 `--follow`=true，不加这个就是长连接） | ❌ |
| `--max-reconnect 5m` | 重连超过5分钟自动停止（默认已有） | ❌ |
| `--read-only` | 拒绝所有写操作。也可设 `WACLI_READONLY=1` | ❌ |
| `--idle-exit 30s` | `--once` 模式下空闲30秒退出 | ❌ |

## 严格禁止

- `wacli send` — 任何发送操作
- `wacli list` / `wacli status` / `wacli info` — 非必要查询增加异常流量
- `wacli login` — 除非账号掉线且所有者明确指示
- 任何其他 wacli 子命令

## 系统集成

真实业务号上线后：
- 持续同步用 systemd service + `wacli sync --max-reconnect 10m`
- 测试号用 cron 2h 定时 + `wacli sync --once`
- 健康检查：15分钟 cron → 检查同步文件时间戳和DB新鲜度
