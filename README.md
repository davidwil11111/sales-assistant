# Sarah — AI 外贸销售跟进参谋

自动分析你的 **WhatsApp** 客户聊天记录，每天告诉你该跟进谁、说什么、有什么风险。  
行业与产品可配置，不绑定特定品类。

> **定位：** WhatsApp 外贸每日跟进参谋（不是 AI 自动成交引擎）。  
> 机械的事交给脚本，判断的事留给 AI，**发送权永远在你手机上**。

仓库：https://github.com/davidwil11111/sales-assistant

---

## 它能做什么

| 能力 | 说明 |
|------|------|
| **只读同步 WhatsApp** | 经 wacli 拉取聊天（`sync` only），不代发消息 |
| **客户池 + 温度** | 热 / B / 沉默池；机会温度 0–100（信号可配置） |
| **智能引导** | 首次「开始配置」：填资料 → 认证同步 → 首次 extract |
| **每日早会报告** | TOP3、今日工作、风格错配、承诺追踪等（十大板块） |
| **安全写回** | `--write-analysis` 合并写回，禁止 Agent 手改整份 clients.json |
| **报告快照** | `last_report.json` 支撑真实「昨日复盘」 |
| **风格统计** | `--detail` 输出 `customer_style_stats`（可证伪，非编造） |
| **多语言术语表** | 英/西/阿/法语术兜底，见 `references/glossary-multilingual.md` |

---

## 如何工作

```
WhatsApp
   │  wacli sync --once（只读）
   ▼
SQLite (wacli.db)
   │  extract.py
   ▼
data/summary.json + clients.json
   │  Agent 按 SKILL.md 分析
   ▼
--write-analysis / --save-report
   ▼
早会报告（人审后手机发送话术）
```

**首次使用：** `config._status == "setup"` → Agent 加载 `references/onboarding-flow.md` → 引导完成 → `_status = active`。

---

## 适用 / 不适用

**适合：** 单兵或小团队、主要用 WhatsApp 跟进、客户几十到一两百、需要每日跟进节奏。  
**不适合：** 指望 AI 代发消息、代填价格交期、替代 CRM 成交流程；客户极少且全在脑子里。

---

## 前置条件

| 依赖 | 说明 |
|------|------|
| Python 3.8+ | 仅标准库，**零 pip 依赖** |
| [wacli](https://github.com/mautrix/whatsapp) | WhatsApp Web 非官方 CLI，只用于 sync |
| AI Agent | 能执行终端命令 + 读写文件（Hermes / Claude Code / Cursor 等） |

```bash
# 安装 wacli（示例）
go install go.mau.fi/mautrix-whatsapp/cmd/wacli@latest
# 或从 mautrix/whatsapp releases 下载预编译包
```

> ⚠️ wacli 非官方客户端，有风控风险。本项目**只读同步**，不调用 send。

---

## 安装与首次配置

### 1. 克隆

```bash
git clone https://github.com/davidwil11111/sales-assistant.git
cd sales-assistant
```

OpenClaw / Hermes：

```bash
cd ~/.hermes/skills/openclaw-imports/
git clone https://github.com/davidwil11111/sales-assistant.git
```

### 2. 配对 wacli（账号名默认 `test`，可在引导中改）

```bash
wacli accounts add test --phone +86********* --follow
# 手机：WhatsApp → 已链接设备 → 用配对码关联
wacli auth status --account test
wacli sync --once --account test --max-db-size 500MB
```

确认库路径（按系统调整）：

```bash
ls ~/.local/state/wacli/accounts/test/wacli.db
# Windows 常见：%USERPROFILE%\.local\state\wacli\accounts\test\wacli.db
```

路径不对时改 `config.json` 的 `db_path`。

### 3. 在 Agent 里说

```text
开始配置
```

引导会完成：探测数据库 → 确认名字/时区/号码/行业/产品词/温度信号 → 写入 config → 再 sync → `extract.py` → 启动报告。  
细节见 **`references/onboarding-flow.md`**。

---

## 日常指令

| 你说 | 系统做什么 |
|------|------------|
| `今日报告` | extract → 分析 → 早会报告 → `--save-report` |
| `分析一下[客户]` | detail + 7 步分析 → `--write-analysis` |
| `[客户]最新情况` | 状态速览 |
| `帮我写话术` | 先问价/交期/品牌/付款；非中文查术语表 |
| `搜一下[国家]新闻` | 搜索后匹配客户写触达话术 |

### 核心 CLI

```bash
python extract.py                                 # 或 python3
python extract.py --detail <JID>
python extract.py --write-analysis '{"jid":"...","intent":"高",...}'
python extract.py --save-report '{"top3":[...],"actions":[...]}'
python scripts/test_stability.py                  # 单元/逻辑自检
python scripts/e2e_smoke.py                       # 真实库 E2E（备份或 --db）
python scripts/e2e_smoke.py --db path/to/wacli.db
```

**禁止**手改覆盖 `data/clients.json`；分析结论一律走 `--write-analysis`。

---

## 配置要点（`config.json`）

| 区块 | 作用 |
|------|------|
| `owner_*` / `wacli_account` / `db_path` | 身份与数据源 |
| `industry.product_keywords` | 产品识别 |
| `industry.temperature_hot_signals` 等 | 温度信号（空=通用外贸默认） |
| `pools.hot_entry` | **自动入热池门槛**（priority/新消息/温度/无轰炸等） |
| `scoring` | priority 粗筛关键词 |
| `evolution` | 弱模式自学习（默认 `enabled: false`） |

LED 行业示例（温度中温可配技术词）：

```json
{
  "industry": {
    "name": "LED灯外贸",
    "product_keywords": { "panel": "面板灯", "bulb": "灯泡" },
    "capacity_pattern": "(\\d+)\\s*[wW瓦]",
    "capacity_label": "瓦",
    "temperature_hot_signals": ["invoice", "deposit", "pi", "payment", "confirm order"],
    "temperature_demand_signals": ["watt", "lumen", "cri", "ip", "driver", "spec", "quote"]
  }
}
```

---

## 项目结构

```
sales-assistant/
├── SKILL.md                 # Agent runtime（瘦身核心规则）
├── config.json              # 全局配置（仓库内为 setup 模板）
├── extract.py               # 提取 / detail / 安全写回 / 报告快照
├── onboarding.py            # scan + write 引导
├── scripts/
│   ├── prepare_report_data.py
│   ├── test_stability.py
│   ├── e2e_smoke.py
│   ├── wacli-safe-sync.py
│   └── wacli-health-check.py
├── references/
│   ├── onboarding-flow.md          # 首次引导全文
│   ├── report-template.md          # 早会完整模板
│   ├── glossary-multilingual.md    # 多语言术语
│   └── ...                         # 设计决策与安全文档
└── data/                    # 运行时生成（默认 gitignore，勿提交客户数据）
```

---

## 定时任务（可选）

每日报告 cron **不要加载完整 SKILL**（上下文过大），用预处理脚本：

```bash
# 每 2 小时同步（账号名与 config.wacli_account 一致）
0 */2 * * * wacli sync --once --account test --max-db-size 500MB

# 同步后 1 分钟刷新
1 */2 * * * cd /path/to/sales-assistant && python3 extract.py

# 每天 09:00 预处理（Agent 只做格式化推送时 skills 留空）
0 9 * * * cd /path/to/sales-assistant && python3 scripts/prepare_report_data.py
```

Hermes 示例见 `references/production-startup.md`、`references/cron-debugging-max-retries.md`。

---

## 安全与隐私

- wacli **仅** `sync` / `auth status`；话术人发  
- 对话原文不进 `clients.json`，按 JID detail 查询  
- `data/clients.json`、备份库、报告快照等已在 `.gitignore`  
- **请勿**把含真实客户聊天的 `data/` 或 `*.db` 推到公开仓库  

---

## 测试

```bash
python scripts/test_stability.py   # 写回 / 温度配置 / 热池门槛 / SKILL 体积等
python scripts/e2e_smoke.py        # 使用 data/backups 或 config 中的库（隔离临时目录）
```

---

## 常见问题

**Q: 安全吗？**  
只读同步 + 本地数据 + 不自动发消息，是刻意的最低风险姿势；wacli 本身仍非官方。

**Q: 不用 Hermes 能用吗？**  
能。把 `SKILL.md` 当 system / skill 加载，Agent 能跑终端即可。

**Q: 需要 pip 吗？**  
不需要。

**Q: 热池为什么经常是 0？**  
自动入热有门槛（新消息、温度≥35、priority=high 等）。也可由 Agent 裁决 `pool_suggestion` 后 `--write-analysis` 升池。

**Q: 客户消息很少？**  
`customer_style_stats.confidence=low` 时会标低置信度，不编造「过去 100 条」风格。

---

## 许可

MIT License

---

## 贡献

欢迎 Issue / PR。大改动建议先开 Issue 讨论。

---

**由 wacli + Python 3 + AI Agent 驱动 · [GitHub](https://github.com/davidwil11111/sales-assistant)**
