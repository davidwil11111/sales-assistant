# Cron调试：max_retries_exhausted → BrokenPipeError 排查模式

## 症状

cron job 报 `error`，session dump 显示：
- `reason: max_retries_exhausted`
- `error: {"type": "ReadError", "message": "[Errno 32] Broken pipe"}`

## 根因链

```
skill注入(31KB) + 15个工具定义(36KB) = 53KB系统开销(~13K tokens)
  → API请求过大/超时
  → 重试
  → 每轮重试上下文继续膨胀(工具调用输出追加)
  → 全部重试耗完
  → 会话被调度器杀死
  → 最后写入时pipe已断 → BrokenPipeError
```

## 排查步骤

1. 找到session dump文件：`~/.hermes/sessions/request_dump_cron_<job_id>_*.json`
2. 检查 `reason` 字段：
   - `max_retries_exhausted` → API调用全部失败
   - 其他reason → 不同问题
3. 检查 `request.body` 中的 `messages`：
   - 计算 system prompt 大小
   - 数 tools 数量和总字符数
   - 估算总token开销
4. 检查 `request.url` 确认目标API

## 修复方案（按效果排序）

### 方案A：去skill + 限toolsets（推荐，91%上下文削减）

```
cronjob update:
  skills: []           # 不加载SKILL.md
  enabled_toolsets: ["terminal", "file"]  # 15→2，省36KB
  prompt: 内联模板规则  # 自包含
```

### 方案B：脚本预处理 + Agent只格式化

创建预处理脚本（如 `prepare_report_data.py`）：
- Python做所有重活（extract、detail、温度计算、信号提取）
- 输出单个JSON文件（~12KB）
- Agent只读JSON + 按模板格式化

### 方案C：no_agent模式（纯脚本输出）

```
cronjob create:
  no_agent: true
  script: "scripts/generate_report.sh"
```

适用于报告格式固定、不需要LLM判断的场景。

## 本系统实际应用

每日报告cron (`d6f4cd84d41e`)：
- 旧：加载crane-sales-assistant skill → 31KB注入 + 15工具 → 53KB开销 → 失败
- 新：skills=[] + toolsets=[terminal,file] + prepare_report_data.py预处理 → ~5KB开销 → 预期稳定

## 分析命令

```bash
python3 -c "
import json
with open('path/to/dump.json') as f:
    d = json.load(f)
body = json.loads(d['request']['body'])
msgs = body.get('messages', [])
sys_len = sum(len(str(m.get('content',''))) for m in msgs if m['role']=='system')
tools_len = len(json.dumps(body.get('tools', [])))
print(f'System: {sys_len:,} chars')
print(f'Tools: {tools_len:,} chars ({len(body.get(\"tools\",[]))} tools)')
print(f'Total overhead: {sys_len+tools_len:,} chars')
print(f'Reason: {d.get(\"reason\")}')
"
```
