# 生产环境启动流程

系统从干净状态到全功能运行的引导流程。

## 前置条件

- `config.json`: `_status` 为 `"setup"` 或 `"paused"`，所有用户字段为空
- `data/`: 所有JSON文件为空或不存在
- cron任务全部暂停
- wacli已安装，账号已添加

## 启动步骤

### ① 运行智能引导

```bash
python3 onboarding.py scan    # 自动探测数据库
```

然后通过 Agent 交互式完成配置（名字、时区、号码、行业、产品关键词）。

### ② 检查wacli认证

```bash
wacli auth status --account <your_account>
# 期望输出: Authenticated as 86***********@s.whatsapp.net
```

如果未认证 → `wacli login --account <your_account>` → 扫码

### ③ 手动同步 + 验证extract

```bash
wacli sync --once --account <your_account> --max-db-size 500MB
python3 extract.py
# 验证：总客户>0，池子分布正常
```

### ④ 启用定时任务（可选）

```bash
# wacli同步 cron (每2小时)
hermes cron create --schedule "0 */2 * * *" \
  --command "wacli sync --once --account <your_account> --max-db-size 500MB" \
  --no-agent

# extract刷新 cron (每2小时，同步后1分钟)
hermes cron create --schedule "1 */2 * * *" \
  --command "cd /path/to/sales-assistant && python3 extract.py" \
  --no-agent

# 每日报告 cron (09:00)
hermes cron create --schedule "0 9 * * *" \
  --command "cd /path/to/sales-assistant && python3 scripts/prepare_report_data.py" \
  --skills "" --toolsets "terminal,file"
```

> ⚠️ 每日报告 cron 必须 `skills: ""` 和 `toolsets: "terminal,file"`，否则可能因上下文过大导致失败。

## 生产链路架构

```
WhatsApp → wacli sync (每2h:00) → wacli.db
                                      │
                         extract.py (每2h:01)
                                      │
                                clients.json
                                      │
                       每日报告 cron (09:00)
                       ① 跑 prepare_report_data.py
                       ② 读预处理 JSON
                       ③ 按模板格式化
                       ④ 推送微信
```

## cron vs 交互模式差异

| | 交互模式 | cron模式 |
|---|---|---|
| 触发 | 用户指令 | 定时自动 |
| 加载SKILL.md | ✅ | ❌（skills=[]） |
| 工具集 | 全量 | terminal + file（2个） |
| 数据提取 | extract.py + --detail逐客户 | prepare_report_data.py一次性 |
| 上下文开销 | ~52KB | ~5KB |
| 节省 | — | 91% |

## 常见问题

### cron报max_retries_exhausted

症状：API调用失败 → 重试耗完 → 会话被杀。

根因：SKILL.md全量注入 + 全量工具定义 → 上下文溢出 → API失败 → 重试耗尽。

修复：不加载skill + 限toolsets + 预处理数据。

### 数据稀疏期

新系统上线初期hot池为空。这是正常的——随着WhatsApp消息同步，extract.py会自动填充池子。观察期系统静默收集数据。
