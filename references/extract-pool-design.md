# extract.py 池子管理设计决策

三个关键修复，于2026-06-10确认。

## 1. 热池容量保护

**问题**：自动裁决 `priority=high + B池 → hot` 无上限，可能导致热池膨胀超过 `hot_max_size`，稀释所有者精力。

**修复**：升级前检查 `hot_count < hot_limit`。

```python
# 初始化（在循环外）
hot_count = sum(1 for c in old_clients.values() if c.get("pool") == "hot")
hot_limit = cfg["pools"]["hot_max_size"]  # 默认7

# 自动裁决（循环内）
if priority == "high" and old_pool == "B":
    if hot_count < hot_limit:
        old_pool = "hot"
        pool_sug = None
        hot_count += 1
    else:
        pool_sug = "upgrade_to_hot"  # 热池满，保留在B池顶部，等Agent裁决
```

超限的 high 客户：留在 B 池，`pool_suggestion = "upgrade_to_hot"`——Agent 在 Step 6 看到后决定是否踢掉热池中更低价值的客户来腾位。

## 2. last_extract_at 改用 config.json

**问题**：`is_new_message` 用 `CLIENTS_JSON.stat().st_mtime` 作基准——Agent 写回 clients.json 后 mtime 更新，下次 extract 跑时 `is_new_message` 全变成 false，即使用户收到了新消息。

**修复**：只在 extract.py 运行结束时写 `config.json.last_extract_at`。Agent 写 clients.json 不影响此字段。

```python
# 读取（循环前）
last_extract_at = cfg.get("last_extract_at")
if last_extract_at:
    last_extract_ts = int(datetime.fromisoformat(last_extract_at).timestamp())
else:
    last_extract_ts = 0

# 写入（extract 结束时）
cfg["last_extract_at"] = datetime.now().isoformat()
with open(CONFIG, "w") as f:
    json.dump(cfg, f, ensure_ascii=False, indent=2)
```

## 3. 沉默池自动裁决后清空 pool_suggestion

**问题**：`pool_suggestion()` 函数对 `B + 0客户消息 + >30天` 返回 `"downgrade_to_dormant"`，自动裁决直接改 `old_pool = "dormant"` 但 `pool_sug` 仍残留 `"downgrade_to_dormant"`——Agent 看到冗余建议。

**修复**：自动裁决后 `pool_sug = None`。

```python
elif cust_count == 0 and days > 30 and old_pool == "B":
    old_pool = "dormant"
    pool_sug = None  # 已自动执行，不需Agent再裁决
```

## 4. Cron 执行 BrokenPipe 防护

**问题**：cron agent 通过 terminal() 运行 extract.py 时，agent 读完 stdout 后关闭管道，extract.py 的 `print()` 继续写 → `BrokenPipeError` → cron job 报 `RuntimeError: [Errno 32] Broken pipe` 失败。

**修复**：extract.py 末尾的 print 段包 `try/except BrokenPipeError`。数据写入（clients.json / summary.json / config.json）在 print 之前完成，管道断了不影响数据落盘。

```python
# clients.json 先写完，再打印（打印失败不影响数据）
try:
    print(f"✅ extract.py 完成")
    # ... 其他 print
except BrokenPipeError:
    pass  # cron agent 已关闭管道，忽略

# config.json 的 last_extract_at 在 print 之后写，
# 确保只有完整成功的 extract 才更新时间戳
```

**教训**：cron 环境里 stdout 不可靠。任何脚本如果 print 之后还有关键操作，把 print 包 try/except，或者用 `logging` 输出到文件。
