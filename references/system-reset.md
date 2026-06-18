# 系统重置流程

当需要将系统回归到"无任何用户数据"的初始状态时执行。

## 步骤

### 1. 归档旧数据
```bash
cd ~/.hermes/skills/openclaw-imports/sales-assistant
mkdir -p data/backups/archive_$(date +%Y%m%d)
cp data/clients.json data/backups/archive_$(date +%Y%m%d)/
cp data/summary.json data/backups/archive_$(date +%Y%m%d)/
cp data/style_profile.json data/backups/archive_$(date +%Y%m%d)/
cp data/behavior_stats.json data/backups/archive_$(date +%Y%m%d)/
cp config.json data/backups/archive_$(date +%Y%m%d)/config_old.json
```

### 2. 清空数据文件
```bash
echo '[]' > data/clients.json
echo '{"generated_at":"","total":0,"pools":{"hot":0,"B":0},"new_customer_messages":0,"stale_analyses":0,"pursuit_warnings":0,"priority":{"high":0,"medium":0,"low":0},"hot_pool":[],"b_pool_sample":[],"needs_attention":[]}' > data/summary.json
echo '{"baseline_reply_rate":0,"patterns":{},"sample_count":0}' > data/style_profile.json
```

### 3. 重置 config.json
- `_status`: "setup"
- `owner_name`, `owner_phone`, `owner_timezone`: ""
- `evolution.enabled`: false
- `db_path`: 保持指向 test 数据库

### 4. 暂停所有cron
列出所有cron → 逐个pause。如果临时cron（如extract刷新）不存在则跳过。

### 5. 验证
- clients.json 为 `[]`
- config.json owner字段全空
- 所有cron enabled=false

## 注意事项
- 旧的业务数据库不做任何操作，保留作为历史参考
- wacli test账号的认证状态不做任何操作
- SKILL.md 不修改
