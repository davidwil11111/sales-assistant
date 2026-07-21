# 智能引导流程（Onboarding）

> **何时加载：** `config.json` 的 `_status` 为 `"setup"`，或用户说「开始配置」/「初始化」/「setup」。  
> **setup 期间：** 不跑完整「今日报告」分析流水线；先完成本引导。  
> 日常分析不要读此文件，以控制 token。

---

# 系统初始化 · 智能引导流程

当 `config.json` 的 `_status` 为 `"setup"` 时，系统处于未配置状态。此时执行以下引导。  
当用户说「开始配置」/「初始化」/「setup」，**或在 setup 状态下发送任何消息**时，自动进入。

## 设计原则

- **数据优先，不问废话。** wacli.db 里能自动探测的，不提问。只确认。
- **一问一答，不预填。** 每个用户字段必须由用户说出。不假设值。
- **连续执行，不问「继续吗？」。** 用户确认后，技术步骤（写配置 / sync / extract）自动跑完。
- **先出样本再定稿。** 探测结果先展示，用户确认/修改后才写入。
- **遇到阻塞才停。** 只有需要用户动作的事（扫码认证）才中断。

## 总流程（给 Agent 的地图）

```
Phase 0  环境与 WhatsApp 就绪（无库则先引导装 wacli + 扫码 + sync）
   ↓
Phase A  onboarding.py scan 自动探测
   ↓
Phase B  一问一答确认个人信息与行业
   ↓
Phase C  write → auth → sync → extract → 启动报告
```

---

## Phase 0：WhatsApp / 数据库就绪（可跳过）

先读 `config.json` 的 `db_path`、`wacli_account`（默认 `test`）。

### 0.1 检查数据库

```
python3 onboarding.py scan
```

| scan 结果 | 动作 |
|-----------|------|
| `status=="ok"` 且 `db_message_count>0` | 进入 Phase A2 展示探测报告 |
| `status=="ok"` 且消息为 0 | 诚实说明库空，进入 0.2 同步 |
| `status=="no_db"` | 进入 0.2，不要假装已有客户数据 |

### 0.2 无库或空库时：引导装账号 + 同步

1. 说明：系统**只读同步**聊天记录，**不会**用 wacli 发消息；话术永远人审后手机发。  
2. 问 wacli 账号名（对应 `--account`）：  
   - 默认选项：`A) test（推荐首次） B) 其他（请说）`  
   - 记录 `wacli_account`  
3. 若用户尚未配对：

```bash
wacli accounts add {wacli_account} --phone +{号码} --follow
# 手机：WhatsApp → 已链接设备 → 关联设备 → 输入配对码
wacli auth status --account {wacli_account}
```

4. 同步（一次性）：

```bash
wacli sync --once --account {wacli_account} --max-db-size 500MB
```

5. 再跑 `python3 onboarding.py scan`。仍无数据则说明可能号码/路径不对，检查 `config.json` 的 `db_path`。

> Phase 0 只解决「有没有消息可读」。个人信息仍走 Phase B，不要在 0 里假设 owner_name。

---

## Phase A：自动探测（不问用户）

### A1：扫描

```
python3 onboarding.py scan
```

关注字段：

- `detected_owner_phone` / `detected_countries` / `detected_name_keywords` / `detected_chat_keywords`
- `detected_capacity_samples` / `detected_languages`
- **`suggested_temperature_hot` / `suggested_temperature_mid` / `suggested_temperature_demand`**（引导 B8/B9 用）
- `wacli_account_current`

### A2：展示探测报告

```
📊 数据库扫描结果

📁 数据规模：XXX条消息 / XXX个对话
📞 检测到的账号：+XXXXX...
🌍 客户国家分布（Top 5）：...
🔤 客户名高频词（Top 10）：...
💬 对话高频关键词（Top 10）：...
📐 规格数字样本：...
🌐 检测到的语言：英语 / 阿拉伯语 / ...
🔥 疑似成交信号词（建议作高温）：invoice(12) payment(8) ...
🔍 疑似询价/技术词（建议作中温）：quote(20) price(15) ...

--- 请确认。接下来逐一确认配置项。---
```

消息为 0 时诚实说：部分探测为空是正常的，可先填行业再同步。

---

## Phase B：交互确认（一问一答）

> 每步等用户回答再下一步。给 2–4 个具体选项，或让用户直接说答案。

### B0：wacli 账号名（若 Phase 0 未定）

- 问：「wacli 同步用的账号名是？（`--account` 参数）」  
- 默认：`test`  
- 记录 `wacli_account`

### B1：你的名字

- 问：「报告中引用你时用什么名字？（平时和客户沟通用的名字）」  
- 例：David / Mohamed / 张伟  
- → `owner_name`

### B2：时区

- 问：「你在哪个时区？」  
- 选项：`A) 北京 UTC+8  B) 迪拜 UTC+4  C) 欧洲中部 UTC+1  D) 美东 UTC-5  E) 其他`  
- → `owner_timezone`

### B3：WhatsApp 号码

- 若 scan 检测到号码 →「是 +XXXX 吗？」确认或改  
- 否则请用户输入（不含 +，如 `8613800138000`）  
- → `owner_phone`

### B4：行业名称

- 问：「你的行业怎么描述？越具体越好。」  
- 例：工程机械外贸 / LED灯外贸 / 化工原料出口  
- → `industry.name`

### B5：从业年限

- 选项：1年以内 / 3–5年 / 5–10年 / 10年以上  
- → `industry.persona_years`（可存数字或原文）

### B6：产品关键词

1. 展示 scan 的客户名/对话高频词  
2. 请用户用格式：`英文关键词=中文标签`  
   例：`panel=面板灯, bulb=灯泡, crane=起重机`  
3. → `industry.product_keywords`（对象）

### B7：规格单位

- 有规格样本则展示  
- 选项：吨(t) / 瓦(w/kw) / 米(m) / 千克(kg) / 其他  
- 映射：  
  - 吨 → `capacity_pattern: "(\\d+)\\s*[tT吨]"`，`capacity_label: "吨"`  
  - 瓦 → `capacity_pattern: "(\\d+)\\s*[wW瓦kwKW]"`，`capacity_label: "瓦"`  
- → `industry.capacity_pattern` / `capacity_label`

### B8：技术 / 询价信号（中温）

- 问：「客户聊规格、参数、报价时，常出现哪些词？」  
- 先展示 `suggested_temperature_mid` + `suggested_temperature_demand`  
- 用户确认或补充（逗号分隔英文词）  
- → 同时写入：  
  - `industry.context_keywords`  
  - `industry.temperature_demand_signals`  
  - `industry.temperature_mid_signals`（可与 demand 合并去重；用户未说则用通用默认，**write 时可不传 mid，extract 会回退默认**）

### B9：成交推进信号（高温）

- 问：「客户哪些词出现时，说明快要下单/在谈付款发货？（不是随便询价）」  
- 先展示 `suggested_temperature_hot`  
- 给行业示例：  
  - 通用：`invoice, payment, deposit, pi, confirm order`  
  - LED 可加：`driver, lumen` 一般**不要**放高温，高温应是成交动作  
- 用户确认列表 → `industry.temperature_hot_signals`  
- 用户说「用默认」→ 传空数组 `[]`，系统用内置通用外贸高温词

### B10：客户主要语言（可选，一问）

- 若 scan 有阿拉伯语/西语/法语 → 「客户主要用哪些语言？写话术时会查术语表。」  
- 仅记入口头/写在 notes 即可；提醒 Agent 以后写非中文话术读 `references/glossary-multilingual.md`  
- 不强制写 config 字段

### B11：最终确认

```
✅ 配置汇总确认

名字：{owner_name}
时区：{owner_timezone}
WhatsApp：{owner_phone}
wacli 账号：{wacli_account}
行业：{industry.name}
从业：{industry.persona_years}
产品关键词：{product_keywords}
规格：{capacity_label} / {capacity_pattern}
中温/技术信号：{temperature_demand_signals}
高温/成交信号：{temperature_hot_signals 或「系统默认」}

确认无误？输入「确认」或告诉我改哪项。
```

---

## Phase C：写入并激活（自动连续，不问继续吗）

### C1：写入 config

组装 JSON（字段名必须与 `onboarding.py write` 一致）：

```json
{
  "owner_name": "...",
  "owner_phone": "...",
  "owner_timezone": "...",
  "wacli_account": "test",
  "industry": {
    "name": "...",
    "persona_years": 5,
    "product_keywords": {"panel": "面板灯"},
    "capacity_pattern": "(\\d+)\\s*[tT吨]",
    "capacity_label": "吨",
    "context_keywords": ["spec", "drawing"],
    "temperature_demand_signals": ["spec", "drawing"],
    "temperature_mid_signals": ["quote", "price", "spec"],
    "temperature_hot_signals": ["invoice", "payment", "deposit"]
  }
}
```

```bash
python3 onboarding.py write '<json>'
```

成功后 `_status` 变为 `active`。

### C2：wacli 认证

```bash
wacli auth status --account {wacli_account}
```

- 已认证 → 继续  
- 未认证 → **停下**引导扫码，完成后再继续  

### C3：首次同步

```bash
wacli sync --once --account {wacli_account} --max-db-size 500MB
```

报告：是否成功、耗时。失败则给可执行建议（重试 sync / 查路径），不要静默跳过。

### C4：首次 extract

```bash
python3 extract.py
```

展示：总客户、hot / B / dormant、priority 分布、新消息数。  
说明：热池自动入池有门槛（新消息+高温等），首次 hot 可能为 0 是正常的。

### C5：启动报告（一次性汇总）

```
✅ 系统已启动

📋 用户：{owner_name} | 时区：{owner_timezone} | 行业：{industry.name}
📱 WhatsApp：{owner_phone} | wacli：{wacli_account}

📊 当前数据：
   总客户：N
   热池：H  |  B池：B  |  沉默：D
   priority high/medium/low：...

⚙️ 已生效：
   - 温度信号（行业配置或默认）
   - 热池自动门槛（config.pools.hot_entry）
   - 分析写回请用 extract.py --write-analysis（勿手改 clients.json）

🎯 现在可以：
   - 「今日报告」— 完整早会（会按需加载报告模板）
   - 「分析一下[客户名]」
   - 「帮我写话术」（非中文会查多语言术语表）

📅 cron / 每日 09:00 推送需你在 Hermes 侧手动启用（见 production-startup.md）
```

---

## 引导中的硬约束

1. **wacli 只允许** `auth status` 与 `sync`（优先 `--once`）。禁止 send。  
2. 价格/交期/品牌引导阶段不要写进任何示例话术当真值。  
3. Phase C 失败：按 `⚠️Sarah异常·onboarding / 错误 / 影响 / 建议` 报告，修好后从失败步继续，不必重问 B1–B11。  
4. 用户中途说「先跳过同步」：允许 write 激活配置，但明确警告「无消息则报告为空」，并留下 sync 命令。

---

## write JSON 字段速查

| 字段 | 来源步骤 |
|------|----------|
| owner_name | B1 |
| owner_timezone | B2 |
| owner_phone | B3 |
| wacli_account | B0 / Phase 0 |
| industry.name | B4 |
| industry.persona_years | B5 |
| industry.product_keywords | B6 |
| industry.capacity_* | B7 |
| industry.context_keywords / temperature_demand_signals / temperature_mid_signals | B8 |
| industry.temperature_hot_signals | B9（空=系统默认） |
