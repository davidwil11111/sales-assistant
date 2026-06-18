# 生产环境启动流程

系统从干净状态到全功能运行的7步引导流程。

## 前置条件

- config.json `_status: "paused"`, `owner_phone: ""`, `owner_name: ""`
- data/clients.json = `[]`（空数组）
- 所有cron任务已暂停
- wacli已安装，test账号存在

## 启动步骤

### ① 填写配置

```json
// config.json 关键修改
"_status": "active",
"owner_phone": "86***********",
"owner_name": "你的名字",
"db_path": "~/.local/state/wacli/accounts/test/wacli.db"
```


### ② 检查wacli认证

```bash
wacli auth status --account test
# 期望输出: Authenticated as 8618530726580@s.whatsapp.net
```

如果未认证 → `wacli login --account test` → 扫码

### ③ 启用wacli同步cron

```bash
hermes cron resume c5cea84c916b  # wacli定时同步(每2小时)
```

### ④ 手动同步 + 验证extract

```bash
wacli sync --once --account test --max-db-size 500MB
cd ~/.hermes/skills/openclaw-imports/crane-sales-assistant
python3 extract.py
# 验证：总客户>0，池子分布正常
```

### ⑤ 创建extract定时cron

```bash
# 或 hermes cron create:
# schedule: 1 */2 * * *
# script: crane_extract.sh (内容: python3 extract.py >/dev/null 2>&1)
# no_agent: true
# deliver: local
```

cron job_id应类似 `d0a217dbc8ee`。

### ⑥ 启用每日报告cron

```bash
hermes cron resume d6f4cd84d41e  # 每天09:00
```

⚠️ 这个cron的特殊配置：
- `skills: []` — 不加载SKILL.md（31KB技能注入导致max_retries_exhausted）
- `enabled_toolsets: ["terminal", "file"]` — 仅2个工具（15个工具=36KB=9K tokens开销）
- 模板规则内联到prompt中

### ⑦ 验证全链路

```bash
# 跑一次完整报告
cd ~/.hermes/skills/openclaw-imports/crane-sales-assistant
python3 scripts/prepare_report_data.py > /tmp/report_input.json
# 检查输出：hot_clients, b_samples, temperature, key_signals 等字段齐全
```

## 生产链路架构

```
WhatsApp ──→ wacli sync (c5cea84c916b, 每2h:00) ──→ test.db
                                                         │
                                          extract.py (d0a217dbc8ee, 每2h:01)
                                                         │
                                                   clients.json (≤50KB)
                                                         │
                                          每日报告cron (d6f4cd84d41e, 09:00)
                                          ① 跑 prepare_report_data.py
                                          ② 读 /tmp/report_input.json (~12KB)
                                          ③ 按模板格式化
                                          ④ 推送微信
```

## cron vs 交互模式差异

| | 交互模式 | cron模式 |
|---|---|---|
| 触发 | 用户通过SKILL.md指令 | 定时自动 |
| 加载SKILL.md | ✅ 是 | ❌ 否（skills=[]） |
| 工具集 | 全量（~15个） | terminal + file（2个） |
| 数据提取 | extract.py + --detail逐客户 | prepare_report_data.py一次性 |
| 上下文开销 | ~52KB（skill+tools） | ~5KB（base system+tools） |
| 节省 | — | 91% |

## 常见问题

### cron报max_retries_exhausted

症状：API调用失败 → 重试耗完 → 会话被杀 → BrokenPipeError。

根因链：
1. SKILL.md（31KB）注入cron system prompt
2. 15个工具定义（36KB）追加到上下文
3. Agent跑extract.py + detail → 每次工具调用往上下文堆输出
4. 5-6轮后上下文超50K tokens
5. DeepSeek API调用失败
6. 重试 → 再失败 → 直到max_retries_exhausted
7. 会话终止 → BrokenPipeError

修复：不加载skill + 限toolsets + 预处理数据。

### Weixin token missing

cron agent生成报告成功但推送失败。cron在独立session运行，没有微信token上下文。需要确认微信集成token在cron环境中可访问。

### 数据稀疏期

新系统上线初期hot池为空（无足够活跃客户）。这是正常的——随着WhatsApp消息同步进来，extract.py会自动填充池子。观察期（前50个客户样本）系统静默收集数据，不批评不裁决。
