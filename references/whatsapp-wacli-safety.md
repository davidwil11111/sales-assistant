# WhatsApp 风控 — wacli 安全使用指南

## 风险本质

wacli 通过模拟 WhatsApp Web 协议工作，不是 Meta 官方 API。WhatsApp 会检测非官方客户端行为并封号。

## wacli 关键命令参数（必须掌握）

### `sync` 子命令

| 参数 | 作用 | 默认值 |
|------|------|--------|
| `--follow` | 持续同步直到 Ctrl+C | **默认 true**（不加 `--once` 就是长连接） |
| `--once` | 同步完空闲30秒后自动退出 | 关闭 |
| `--max-reconnect` | 重连超时上限，超时停止（不无限重试） | `5m`（5分钟） |
| `--max-db-size` | 数据库文件上限 | 无限制 |
| `--max-messages` | 消息总数上限 | 无限制 |
| `--idle-exit` | --once 模式下的空闲退出时间 | `30s` |

### 全局参数

| 参数 | 作用 |
|------|------|
| `--read-only` | 拒绝任何 WhatsApp 写操作。也可设 `WACLI_READONLY=1` 环境变量 |

### 两种同步模式

**1. 定时一次性同步（测试号/低活跃号）**
```
wacli sync --once --account test --max-db-size 500MB --max-messages 50000
```
- 连接→同步消息→空闲30秒→自动退出
- 不保持长连接，WhatsApp 不会因空闲断开
- 适合：测试号、消息量少的号

**2. 持续长连接同步（真实业务号）**
```
wacli sync --account prod --max-reconnect 10m --max-db-size 500MB --max-messages 50000
```
- `--follow` 是默认行为（不需要显式指定）
- `--max-reconnect 10m`：重连超过10分钟自动停止（wacli 内置安全网）
- 适合：消息量大、需要实时接收的业务号

## 铁律：wacli 唯一操作 = 同步聊天记录

| 命令 | 允许？ | 原因 |
|------|--------|------|
| `wacli sync --once` | ✅ 定时同步 | 一次性，最低风险 |
| `wacli sync` (长连接) | ✅ 业务号 | 配合 --max-reconnect 安全网 |
| `wacli send` | ❌ | 主动写操作，直接触发风控 |
| `wacli auth status` | ✅ 仅排查 | 确认认证状态 |
| 任何其他命令 | ❌ | 不做 sync/auth status 以外的操作 |

## 风控触发条件

- 短时间内大量消息发送
- 多设备/IP同时在线
- 被多人举报/拉黑
- 新号立即大量加好友/发消息
- 使用非官方客户端（wacli 本身有此风险，只读是最低风险姿势）
- **频繁重新连接** — 这是最常见的触发方式。wacli 内置重连无退避，断开后每2秒重试，短时间内数千次重连=WhatsApp 视为 bot 攻击

## 安全包装器（用于持久连接模式）

`scripts/wacli-safe-sync.py` 在 wacli `--max-reconnect` 之上增加额外保护：

| 机制 | 裸 wacli | wacli + --max-reconnect | wacli + 安全包装器 |
|------|---------|------------------------|-------------------|
| 重连间隔 | 固定~2秒 | 固定~2秒 | 指数退避：2s→4s→8s→...→128s |
| 失败上限 | 无 | 5分钟后停止 | 7次连续失败=熔断30分钟 |
| 适用场景 | 不要用 | 轻量保护 | 业务号完整防护 |

## 健康检查

`scripts/wacli-health-check.py` — 15分钟 cron job，静默运行：

- 检查 `/tmp/wacli_sync_last_ok` 时间戳（>4小时未同步=异常）
- 检查 `/tmp/wacli_sync_last_fail`（上次失败时间晚于上次成功=异常）
- 检查数据库最新消息时间（>12小时=异常）
- 异常写入 `/tmp/wacli_health_alert`，不推送微信

## 2026-06-10 故障记录（教训）

**故障链：**
```
wacli sync --follow (没加 --once，没加 --max-reconnect)
→ 测试号无消息 → WebSocket 空闲 → WhatsApp EOF 断开
→ wacli 内置重连（无退避）→ 每2秒一次
→ WhatsApp connection reset by peer (157.240.3.8)
→ 15小时内 29,758 次重连
→ 最后成功连接 6/9 23:37，消息断流16小时
→ 没有任何监控告警
```

**如果是真实业务号的影响：**
- 16小时看不到客户消息 → 错失商机
- 30K次重连 = 封号级别信号
- 每日报告基于旧数据 = 废纸

**根因：** 不知道 `--once` 和 `--max-reconnect` 这两个参数的存在。

**修复后：**
- 测试号：`wacli sync --once` 每2小时 cron（一次性同步，无长连接）
- 业务号：`wacli sync --max-reconnect 10m` + 安全包装器
- 健康检查：15分钟 cron，静默写状态文件

## 账号保护

- 主号（用于接单通讯）→ 只在手机上正常使用，不接 wacli
- 切换真实账号前 → 先用测试号观察至少2周，确认无风控警告
- 封号后 → 立即停止同步，wacli.db 本地数据不受影响
