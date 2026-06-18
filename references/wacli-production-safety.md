# wacli 生产环境安全 — 6月10日故障全记录

## 故障实录

| 时间 | 事件 |
|------|------|
| 6/9 23:37 | wacli 最后一次正常连接 |
| 6/10 00:00 | WhatsApp 服务器 `157.240.3.8` 开始 `connection reset by peer` |
| 00:00→15:17 | wacli 每2秒重连 → **29,758次** → 空转15小时无人知晓 |
| 15:17 | 发现异常，停止服务 |
| 15:29 | 试探连接 → Connected → 限流已冷却（约15分钟） |
| 15:31 | 部署三层防护后重启 |

## 真实环境如果发生同样故障

- 业务号被 WhatsApp 限流/封号
- 16小时零消息同步
- 热池客户的消息看不到 = 丢单
- 30,000次重连在 WhatsApp 眼里 = bot = 封号

## wacli 关键参数（之前不知道的）

### `--once` — 一次性同步
```bash
wacli sync --once --account test
```
同步完自动退出，不保持长连接。适合低流量账号（测试号/冷号）。

### `--follow` 是默认
```bash
wacli sync --account test   # 默认就是 --follow，保持长连接
```
适合真实业务号（有持续消息流量）。测试号没流量→WhatsApp断空闲连接→EOF→重连→限流。

### `--max-reconnect` — 内置重连上限
```bash
wacli sync --account test --max-reconnect 10m
```
默认 5 分钟。超过这个时间还在断连就停止。这是 wacli 自带的熔断，之前没用。

### `--read-only` — 全局安全锁
```bash
wacli --read-only sync --account test
# 或
WACLI_READONLY=1 wacli sync --account test
```
拒绝任何写操作。防止意外发送消息。**真实号必须开。**

## 三层防护体系

### 第1层：wacli-safe-sync.py（指数退避+熔断）
```
断连 → 2s 等 → 重试 → 4s → 8s → 16s → 32s → 64s → 128s
7次连续失败 = 熔断 30 分钟
```

### 第2层：systemd RestartSteps=2
```
包装器进程崩溃 → 最多重启 2 次
```

### 第3层：wacli-health-check.py（15分钟 cron）
```
检测：服务状态 / wacli 进程 / 熔断文件 / 数据库新鲜度
异常 → 写 /tmp/wacli_health_alert → 不推微信（基础设施自治）
```

## 测试号 vs 真实号的同步策略

| 环境 | 策略 | 命令 |
|------|------|------|
| 测试号 | 每2小时一次性同步 | `wacli sync --once --account test ...` |
| 真实号 | 持久长连接+退避熔断 | `wacli sync --account main --max-reconnect 10m ...` + safe-sync wrapper |

测试号消息量低，长连接空转→WhatsApp断→限流。
真实号消息持续，长连接稳定。

## 健康检查不推微信

基础设施问题由系统自行处理，不惊动用户。
只有需要用户行动的事（如重新扫码认证）才推通知。
详见 SKILL.md 铁律 #7：不惊动用户。
