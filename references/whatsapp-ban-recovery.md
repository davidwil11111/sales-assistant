# WhatsApp 封号后客户数据恢复

## 场景
WhatsApp 账号被封（无法登录），但本地 wacli SQLite 数据库完整保留。

## 先决条件
- 被封号码的 wacli 数据库文件存在（如 `~/.local/state/wacli/accounts/business/wacli.db`）
- `python3` 可用（系统自带 `sqlite3` 模块）

## 数据库结构

wacli SQLite 关键表：

| 表名 | 用途 |
|------|------|
| `chats` | 所有会话（jid, name, last_message_ts, archived, unread） |
| `messages` | 完整消息历史（text, display_text, from_me, ts, media_type 等） |
| `contacts` | 联系人详情 |
| `groups` | 群组信息 |

## 恢复步骤

### 1. 统计总数据

```sql
SELECT COUNT(*) FROM chats WHERE jid NOT LIKE '%@g.us';  -- 私聊数
SELECT COUNT(*) FROM messages;                            -- 消息总数
```

### 2. 导出客户列表（排除自己的号）

```python
own_jids = ("[自己的JID]", "0@s.whatsapp.net", "status@broadcast")

cursor = conn.execute("""
    SELECT jid, name, kind, last_message_ts, archived, unread
    FROM chats 
    WHERE jid NOT IN (?, ?, ?)
    AND jid NOT LIKE '%@g.us'
    ORDER BY last_message_ts DESC
""", own_jids)
```

### 3. 恢复 Sarah 的分类体系

Sarah 用名称前缀标记客户等级：
- **A / a 开头** → A级客户，正在成单中（最高优先）
- **W / w 开头** → W级客户，跟进中（中等优先）
- **M / m 开头** → M级客户，低活跃
- 名称中还嵌入了国家、产品、报价状态等信息

提取分类：
```python
if name.startswith(('A ', 'a ')):
    priority.append(record)
elif name.startswith(('W ', 'w ')):
    mid.append(record)
elif name.startswith(('M ', 'm ')):
    low.append(record)
else:
    unclassified.append(record)
```

### 4. 完整导出脚本（模板）

```python
import sqlite3, json
from pathlib import Path
from datetime import datetime

db_path = Path.home() / ".local/state/wacli/accounts/[旧账号名]/wacli.db"
conn = sqlite3.connect(str(db_path))
conn.row_factory = sqlite3.Row

own_jids = ("[自己的JID]", "0@s.whatsapp.net", "status@broadcast")

# 获取所有客户
cursor = conn.execute("""
    SELECT jid, name, last_message_ts
    FROM chats 
    WHERE jid NOT IN (?, ?, ?)
    AND jid NOT LIKE '%@g.us'
    ORDER BY last_message_ts DESC
""", own_jids)

results = []
for c in cursor.fetchall():
    jid = c['jid']
    phone = jid.split('@')[0]
    name = (c['name'] or phone)
    
    # 消息数
    cursor2 = conn.execute("SELECT COUNT(*) FROM messages WHERE chat_jid = ?", (jid,))
    msg_count = cursor2.fetchone()[0]
    
    # 最近消息
    cursor2 = conn.execute("""
        SELECT text, display_text, from_me, ts
        FROM messages WHERE chat_jid = ?
        ORDER BY ts DESC LIMIT 5
    """, (jid,))
    recent = []
    for m in cursor2.fetchall():
        recent.append({
            'text': (m['text'] or m['display_text'] or '')[:200],
            'from_me': bool(m['from_me']),
            'ts': m['ts']
        })
    
    results.append({
        'name': name,
        'phone': phone,
        'messages': msg_count,
        'recent': recent,
        'jid': jid
    })

# 导出 JSON
outpath = Path.home() / 'sales-assistant' / 'recovered_customers.json'
with open(outpath, 'w', encoding='utf-8') as f:
    json.dump({
        'export_time': datetime.now().isoformat(),
        'total': len(results),
        'customers': results
    }, f, ensure_ascii=False, indent=2)
```

### 5. 新号恢复行动

导出数据后，David 需要：

1. **A级客户优先** — 立即用新号添加 WhatsApp
2. **W级客户次之** — 本周内添加
3. 发送换号通知（不提被封）：
   > *Hey [Name], this is [Your Name] from [Your Company]. I've switched to my new business number (+86 XXX). We were discussing [产品] — wanted to make sure you have my new contact. Looking forward to picking up!*

## 注意事项

- 数据库中的 `last_message_ts` 是 Unix 时间戳（秒），需 `datetime.fromtimestamp()` 转换
- `messages.ts` 字段是整数时间戳，不是字符串
- 空 `text` 的消息通常是图片/音频，检查 `media_type` 和 `display_text`
- 导出文件放在 `sales-assistant/data/` 下便于后续分析脚本发现
- 旧数据库不要删除，保留作为永久客户档案备份
