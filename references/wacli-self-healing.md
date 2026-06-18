# WhatsApp 风控 — wacli 自愈架构

## 三层防护体系

```
第1层: wacli-safe-sync.py     ← 指数退避重连 + 7次失败=熔断30分钟
第2层: systemd RestartSteps=2  ← 包装器崩溃最多重启2次
第3层: wacli-health-check.py   ← 15分钟 cron 检测进程/数据库/熔断状态
```

## 故障实录（2026-06-10）

**事件：** wacli WebSocket 反复 EOF → WhatsApp 服务器 connection reset → wacli 内置重连无退避 → 29,758次/15小时 → 风控升级

**如果真实业务号发生此故障：**
- 热池4客户+173 B池客户消息断流16小时
- 29,758次重连 = 封号级别信号
- Edgar invoice/阿根廷报价等关键节点全断

**修复后（安全包装器）：**
- 退避：2s→4s→8s→16s→32s→64s→128s
- 熔断：7次连续失败 = 停止30分钟
- 告警写入 `/tmp/wacli_health_alert`（不推微信）

## 运维原则（铁律 #7）

用户只看报告和问客户问题，不操心系统死活。

- ❌ wacli 断连 → 不问"要不要重启"
- ✅ wacli 断连 → 自动退避 → 自动熔断 → 冷却后自动恢复
- ❌ 数据库异常 → 不推微信告警
- ✅ 数据库异常 → 写状态文件 → 下次报告时自查
- ⚠️ 唯一需要通知用户：WhatsApp 认证过期（需重新扫码）

## 服务配置

```ini
# ~/.config/systemd/user/wacli-sync.service
[Service]
Environment=PATH=$HOME/.local/bin:/usr/local/bin:/usr/bin:/bin
ExecStart=/usr/bin/python3 .../scripts/wacli-safe-sync.py --account test
Restart=on-failure
RestartSec=30
RestartSteps=2
```

## 健康检查 cron

15分钟间隔，结果写入 `/tmp/wacli_health_status.txt`（不推微信）。
检测：服务状态 → wacli 进程 → 熔断标记 → 数据库新鲜度。
