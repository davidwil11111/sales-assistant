# 直接分析旧 wacli 数据库（账号已删除/未认证）

## 触发场景
- WhatsApp旧号被封，wacli 里已删除该账号，但 `wacli.db` 文件还在
- 当前默认账号数据库里的客户不是你想分析的那批
- `analyze_customer.py --daily` 跑出来只有8个杂联系人而不是438个真实客户

## 铁律：分析前先确认数据源
```bash
# 1. 看当前默认是谁
wacli accounts list --json

# 2. 看当前库有多少会话
wacli chats list --limit 5

# 3. 如果数字对不上 → 切到直接 SQL 分析
```

## 数据库结构速查

wacli SQLite 关键表：
- `chats` — 会话列表（jid, name, kind, last_message_ts, archived, unread）
- `messages` — 消息（chat_jid, sender_jid, text, display_text, from_me, ts）
- `contacts` — 联系人（jid, phone, push_name, full_name, first_name）
- `group_participants` — 群组成员（group_jid, user_jid, role）

排除规则（不算客户）：
```python
own_jids = ("86XXX@s.whatsapp.net", "0@s.whatsapp.net", "status@broadcast")
# chats WHERE jid NOT IN own_jids AND jid NOT LIKE '%@g.us'
```

## 意向判断公式

消息文本 `all_text` = 客户侧所有消息合并后小写

```python
d_score = sum(k in all_t for k in ["order","po","deposit","invoice","confirm"]) * 3
n_score = sum(k in all_t for k in ["price","delivery","lead time","payment","quote","fob","cif"]) * 2
e_score = sum(k in all_t for k in ["spec","capacity","vs","compare","catalog"])
i_score = sum(k in all_t for k in ["how much","interested","looking for","need","requirement"])

if d_score >= 3 or (total >= 5 and n_score >= 4): intent = "🔥高"
elif n_score >= 2 or e_score >= 3 or (total >= 3 and i_score >= 1): intent = "⚡中"
else: intent = "💤低"
```

## 产品提取

```python
tons = re.findall(r'(\d+)\s*(?:ton|t|mt)', all_text)
product_kw = ['crane','hoist','winch','lifting','girder','hook']  # 示例，实际从 config.json industry 读取
```

## 称呼提取（用于话术）

```python
def get_greeting(name):
    # 1. 去Sarah分类前缀: A/W/M/w/m
    cleaned = re.sub(r'^[AWMawm]\s+', '', name)
    # 2. 去emoji前缀
    cleaned = re.sub(r'^[✅🔥🔴🟡⚪⚡💤]\s*', '', cleaned)
    # 3. 去掉中文+产品描述后缀
    cleaned = re.sub(r'[\u4e00-\u9fff].*$', '', cleaned).strip()
    # 4. 如果清理后是占位符 → 用 "Hi there"
    if not cleaned or len(cleaned) < 2: return "Hi there"
    if any(c.isdigit() for c in cleaned): return "Hi there"
    if len(cleaned.split()) >= 3: return "Hi there"
    return cleaned.split()[0]
```

⚠️ 常见bug：`get_greeting()` 返回 "Hi there" 时，话术模板不要再加 "Hi " 前缀 → 产生 "Hi Hi there" 双重问候。

## 话术生成规则

按 stage + days 分层：

| 阶段 | ≤3天 | 4-30天 | >30天 |
|------|------|--------|-------|
| Decision | PI催单 | 唤醒+保留slot | 复活 |
| Negotiation | 谈价格/交期/付款 | 跟进+灵活调整 | 重新开价 |
| Evaluation | 确认已看规格 | 提供参照 | 轻推 |
| Interest | 要参数报价 | 新产品钩子 | 重新打招呼 |
| Awareness | 介绍公司 | 刷存在 | 轻触 |

每条话术必须是可直接复制的英文消息，不能是模板说明。

## 群组成员提取

群组成员可能在 `group_participants` 表里存的是内部ID而非真实号码 → 需要从 `messages` 表里按 `sender_jid` 反推真正发过言的人。
