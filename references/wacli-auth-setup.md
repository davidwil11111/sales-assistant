# WaCLI 账号认证设置指南

## 概述

WaCLI 支持两种认证方式连接 WhatsApp：
1. **QR码** — 默认，终端显示二维码，用手机扫码
2. **手机号配对** — 通过 `--phone` 参数，WhatsApp 往手机上发配对码

手机号配对适合无图形终端（SSH、远程）或不想处理二维码的场景。

## 添加新账号并认证

```bash
# 添加名为 test 的账号，用手机号配对，认证后自动开始同步
wacli accounts add test --phone +86********* --follow
```

流程：
1. 终端显示配对码（格式如 `6YR8-PJV2`）
2. 手机上打开 WhatsApp → **已链接设备** → **关联设备** → **用手机号关联**
3. 输入配对码
4. WaCLI 自动完成认证并开始同步

> ⚠️ `--follow` 让认证完成后立即开始同步。不加则只认证退出。

## 查看已配置账号

```bash
wacli accounts list --json
```

## 查看认证状态

```bash
wacli auth status --json
# 返回 {"authenticated": true/false}
```

## 查看账号详情

```bash
wacli accounts show <name>
```

## 查询时指定账号

确认已认证的账号：
```bash
# 指定账号查看认证状态（非常重要！不指定则查默认账号）
wacli auth status --account test --json
wacli auth status --account business --json
```

所有数据查询都需指定账号：
```bash
wacli --account test sync
wacli --account test chats list --limit 5
wacli --account test --json messages list --chat "<JID>" --limit 50
```

## 切换默认账号

```bash
wacli accounts use <name>
```

## 登出

```bash
wacli auth logout --account <name>
```

## 账号清理与删除

当账号认证过期、不再使用、或需要替换时：

### 移除账号配置

```bash
# 移除账号（配置删除，数据保留）
wacli accounts remove <name>
```

⚠️ **移除账号时，store目录保留在磁盘上不删除**。也就是说：
- 账号名和配置从 `config.yaml` 中移除
- 数据库文件（`wacli.db`）、WAL、媒体文件等仍在原路径
- 如果后续需要恢复，重新添加同名账号即可关联已有数据

### 彻底清理（含数据）

```bash
# 先移除账号配置
wacli accounts remove <name>

# 再手动删除 store 目录
rm -rf ~/.local/state/wacli/accounts/<name>/
```

### 切换默认账号后验证

```bash
# 切换默认
wacli accounts use <新账号名>

# 验证
wacli accounts list --json
# default_account 应该是新账号名

# 确认认证状态
wacli auth status --json
# authenticated: true
```

### 注意事项

- 删除前先确认是否有其他服务（如 cron job、脚本）引用了该账号名
- 如果 OpenClaw WhatsApp 扩展也连接了该号码，删除 WaCLI 账号后仍需检查 OpenClaw 配置（`~/.openclaw/openclaw.json`）中的 `enabled` 字段
- store 目录保留意味着可以使用 WAL recovery 等技术恢复已同步但未写入的数据（详见 `troubleshooting-missing-chats.md`）
- 移除账号不影响其他账号的数据

### 账号生命周期最佳实践

| 阶段 | 操作 | 验证 |
|------|------|------|
| 添加 | `wacli accounts add <name> --phone ... --follow` | `wacli doctor --account <name>` |
| 日常使用 | 默认账号 + 定期 `wacli doctor` | AUTHENTICATED: true |
| 认证过期 | `wacli auth --account <name>` 重连 | 同上 |
| 替换账号 | `wacli accounts add <新名>` → 认证 → `wacli accounts use <新名>` → 确认后删除旧配置 | 数据库查询 |
| 删除 | `wacli accounts remove <name>` | `wacli accounts list --json` |

## 常见问题

### 配对码有效期
配对码有时效限制，生成后尽快在手机上输入。

### 配对成功后出现 "failed to sync WhatsApp app state" 警告

```
warning: failed to sync WhatsApp app state regular_high: failed to fetch app state
regular_high patches: websocket disconnected before info query returned response
```

这是非关键性警告。WhatsApp app state 同步在首次配对时偶尔会因 websocket 时序问题失败，不影响主数据同步。如果后续 `Connected. Waiting for history sync...` 出现并开始计数消息，说明认证和主同步正常。

### 同一号码多个账号
WaCLI 支持多账号隔离（不同 store 目录），但一个 WhatsApp 号码同时只能在一处认证。如果手机上已登录，通过配对码关联设备不会踢掉手机。

### 账号已存在
如果账号名已存在，直接 `wacli auth --phone ...` 用现有账号重新认证即可。

### OpenClaw 冲突
如果同时运行 OpenClaw WhatsApp 扩展（使用 baileys 库），与 WaCLI（使用 whatsmeow 库）同号连接可能导致 WhatsApp 强制登出所有设备。参见 `troubleshooting-missing-chats.md`。
