# Sarah — AI 外贸销售助手

自动分析你的 WhatsApp 客户聊天记录，每天早上告诉你该跟进谁、说什么。行业、产品完全可配置，不绑定任何特定领域。

---

## 它能做什么

- **自动读 WhatsApp** — 通过 wacli 同步聊天记录（只读，不帮你发消息，安全）
- **自动评分** — 客户优先级、成交温度、是否被你"轰炸"了、说了哪些减分的话
- **每日早会报告** — 告诉你要联系谁、说什么话、什么时候发、有什么风险
- **客户池管理** — 自动把客户分热池/B池/沉默池，规则可配
- **自我进化** — 追踪每条话术的回复率，自动发现并提醒你的减分话术

> 相当于雇了个不要工资的销售助理，每天跟你开 5 分钟早会。

---

## 如何工作

```
你的 WhatsApp
     │
     ▼ (wacli 只读同步，每2小时)
SQLite 数据库
     │
     ▼ (extract.py 提取+分析)
结构化数据 (JSON)
     │
     ▼ (AI Agent 读取数据，按规则生成)
每日早会报告 → 推送到微信 / 终端
```

**核心理念：机械的事交给脚本，判断的事留给 AI。你永远做最终决定。**

---

## 适用平台

本系统通过 `SKILL.md` 定义 Agent 行为，支持任何可以加载 Skill/Instruction 文件的 AI Agent 平台：

| 平台 | 说明 |
|------|------|
| **OpenClaw / Hermes** | ✅ 原生支持，SKILL.md 直接加载 |
| **Claude Code** | ✅ 将 SKILL.md 内容作为 system prompt 注入 |
| **Cursor** | ✅ 使用 `.cursor/rules/` 或 `@file` 引用 |
| **Cline / Roo Code** | ✅ 作为 `.clinerules` 或自定义 instruction 加载 |
| **GitHub Copilot** | ⚠️ 需要手动将关键规则复制到 `.github/copilot-instructions.md` |
| **任意 ChatGPT 套壳** | ✅ 将 SKILL.md 设为自定义 System Prompt |

**最低要求：Agent 需要能执行终端命令（运行 Python 脚本）、读取/写入文件。**

---

## 前置条件

| 依赖 | 版本 | 用途 |
|------|------|------|
| **Python** | 3.8+ | 运行所有脚本（仅用标准库，零 pip 依赖） |
| **wacli** | 最新版 | WhatsApp 数据同步（📦 [GitHub](https://github.com/tulir/whatsmeow) 生态） |
| **AI Agent 平台** | — | 加载 SKILL.md，运行分析 |

### 安装 wacli

wacli 是一个基于 Go 的 WhatsApp Web 非官方命令行客户端。安装方式：

```bash
# Go 用户
go install go.mau.fi/mautrix-whatsapp/cmd/wacli@latest

# 或使用预编译二进制（推荐）
# 从 https://github.com/mautrix/whatsapp/releases 下载对应平台版本
```

> ⚠️ wacli 是非官方客户端，使用存在风控风险。本项目只用 wacli 同步聊天记录（只读），不发送任何消息。

---

## 安装

### 1. 克隆项目

```bash
git clone https://github.com/davidwil11111/sales-assistant.git
cd sales-assistant
```

如果你使用的是 OpenClaw / Hermes：

```bash
cd ~/.hermes/skills/openclaw-imports/
git clone https://github.com/davidwil11111/sales-assistant.git
```

### 2. 配置 wacli

```bash
# 添加账号（替换成你自己的号码）
wacli accounts add test --phone +86********* --follow

# 终端会显示配对码，在手机上：
# WhatsApp → 已链接设备 → 关联设备 → 用手机号关联 → 输入配对码

# 验证认证状态
wacli auth status --account test
# 期望输出: Authenticated as 86*********@s.whatsapp.net
```

### 3. 确认数据库路径

```bash
# wacli 数据库默认位置
ls ~/.local/state/wacli/accounts/test/wacli.db
```

如果数据库在其他位置，修改 `config.json` 中的 `db_path`。

### 4. 启动智能引导

在你的 Agent 中说：

> 开始配置

系统会自动进入引导流程：扫描数据库 → 展示探测结果 → 与你逐一确认配置项 → 写入配置 → 激活。

引导流程会帮你完成：
- 自动探测你的 WhatsApp 号、客户国家分布、高频产品关键词
- 确认你的名字、时区、行业
- 配置产品关键词和规格识别规则
- 运行首次数据提取

**整个流程约 5 分钟。**

---

## 日常使用

配置完成后，在与 Agent 的对话中使用以下指令：

| 指令 | 作用 |
|------|------|
| `今日报告` | 生成完整早会报告（7个板块） |
| `Sarah，分析一下[客户名]` | 深度分析单个客户 |
| `[客户名]最新情况` | 快速查看客户当前状态 |
| `帮我写话术，[情况]` | 生成 WhatsApp 话术 |
| `搜一下[国家]最新新闻` | 搜索行业新闻匹配客户 |

---

## 配置文件

`config.json` 包含所有可配置项：

```json
{
  "owner_name": "你的名字",
  "owner_phone": "8613800138000",
  "owner_timezone": "UTC+8",
  "db_path": "~/.local/state/wacli/accounts/test/wacli.db",
  "industry": {
    "name": "你的行业",
    "product_keywords": { "英文": "中文标签" },
    "capacity_pattern": "规格正则",
    "capacity_label": "单位",
    "temperature_demand_signals": ["技术讨论信号词"]
  },
  "pools": {
    "hot_max_size": 7,
    "hot_max_silent_days": 30,
    "B_max_size": 20,
    "B_max_silent_days": 90
  }
}
```

详细字段说明见 `config.json` 中的 `_comment`。

---

## 切换行业

系统启动时 `config.json` 的 `industry` 节为空。引导流程会帮你填好，也可以手动编辑。以下是一个 LED 灯外贸的实际配置示例：

```json
{
  "industry": {
    "name": "LED灯外贸",
    "persona_years": 5,
    "product_keywords": {
      "panel": "面板灯",
      "bulb": "灯泡",
      "strip": "灯带",
      "highbay": "工矿灯",
      "flood": "投光灯",
      "面板灯": "面板灯",
      "球泡": "灯泡"
    },
    "capacity_pattern": "(\\d+)\\s*[wW瓦]",
    "capacity_label": "瓦",
    "context_keywords": ["led", "light", "bulb", "lamp", "panel", "strip", "highbay"],
    "context_label_hints": ["led", "light", "bulb", "panel"],
    "temperature_demand_signals": ["watt", "lumen", "cri", "ip", "driver", "color", "spec", "quote", "price", "fob"]
  }
}
```

---

## 项目结构

```
sales-assistant/
├── SKILL.md              ← Agent 行为定义（人格、分析规则、报告模板）
├── config.json           ← 全局配置（行业、池子、阈值）
├── extract.py            ← 核心提取脚本（SQLite → JSON）
├── onboarding.py         ← 智能引导脚本（自动探测 + 配置写入）
├── scripts/
│   ├── prepare_report_data.py  ← cron 专用预处理
│   ├── wacli-safe-sync.py      ← wacli 安全包装器（退避 + 熔断）
│   └── wacli-health-check.py   ← 健康检查
├── data/                 ← 运行数据（自动生成）
└── references/           ← 架构决策文档
```

---

## 定时任务（可选，推荐）

配置 cron 后可实现全自动：每 2 小时同步 WhatsApp → 每天 09:00 自动推送报告。

### OpenClaw / Hermes cron 配置

```bash
# 1. wacli 同步（每2小时）
hermes cron create --schedule "0 */2 * * *" \
  --command "wacli sync --once --account test --max-db-size 500MB" \
  --no-agent

# 2. extract 刷新（每2小时，在同步后1分钟）
hermes cron create --schedule "1 */2 * * *" \
  --command "cd ~/.hermes/skills/openclaw-imports/sales-assistant && python3 extract.py" \
  --no-agent

# 3. 每日报告（09:00）
hermes cron create --schedule "0 9 * * *" \
  --command "cd ~/.hermes/skills/openclaw-imports/sales-assistant && python3 scripts/prepare_report_data.py" \
  --skills "" --toolsets "terminal,file"
```

> ⚠️ 每日报告 cron 需要 `skills: ""` 和 `toolsets: "terminal,file"`，否则可能因上下文过大导致失败。

### 通用 cron（Linux/macOS）

```bash
# crontab -e
0 */2 * * * wacli sync --once --account test --max-db-size 500MB
1 */2 * * * cd /path/to/sales-assistant && python3 extract.py
0 9 * * * cd /path/to/sales-assistant && python3 scripts/prepare_report_data.py
```

---

## 安全

### WhatsApp 风控

- ✅ wacli **只做 `sync`**，不调用 `send` 或其他写操作
- ✅ 默认使用 `--once` 模式（一次性同步完就退出，不保持长连接）
- ✅ 安全包装器提供指数退避 + 熔断保护，防止频繁重连触发风控
- ❌ 所有话术由你在手机上手动发送，系统永远不碰发送端

### 数据保护

- 对话原文不存储在 JSON 中，AI 按需查询单客户，控制 token 消耗
- 每次运行自动备份数据库，保留最近 7 份
- 客户数据不出本地，不上传任何服务器

---

## 常见问题

### Q: wacli 是什么？安全吗？

wacli 是一个开源的 WhatsApp Web 命令行工具。它不是 Meta 官方产品，使用有一定风控风险。本项目只使用它的 `sync`（只读同步）功能，不发送任何消息，是最低风险的使用方式。

### Q: 我不用 OpenClaw/Hermes，能用吗？

可以。核心逻辑全在 Python 脚本里，`SKILL.md` 是 Agent 行为规范。你可以手动将 SKILL.md 内容复制到任何支持 system prompt 的 AI 工具中使用，只是自动化程度会降低。

### Q: 需要装什么 Python 库？

不需要。全部使用 Python 3 标准库（`sqlite3`, `json`, `re`, `pathlib`），零 pip 依赖。

### Q: 支持其他聊天工具（微信、Telegram）吗？

当前只支持 WhatsApp（通过 wacli）。微信/Telegram 需要对应的同步工具，核心分析逻辑是通用的。

### Q: 我的客户消息很少，能用吗？

可以。系统会标记数据稀疏情况，在报告中诚实标注置信度。前 50 个客户样本是观察期，系统静默收集数据，不批评不裁决。

---

## 许可

MIT License

---

## 贡献

欢迎提交 Issue 和 Pull Request。建议先开 Issue 讨论你想要的改动。

---

**由 [wacli](https://github.com/mautrix/whatsapp) + Python 3 + AI Agent 驱动。**
