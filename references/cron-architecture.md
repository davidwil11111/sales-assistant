# Cron报告生成架构

## 问题

每日报告cron加载31KB SKILL.md全量skill后，agent需自行跑extract.py + 4个detail + 组报告，上下文溢出导致error。

## 方案：预处理分层

```
旧: cron agent → 加载SKILL.md(31KB) → 跑extract → 读summary → 跑4×detail → 组报告 → 超时
新: cron agent → 跑prepare_report_data.py(5s) → 读12KB JSON → 格式化 → 推送
```

**Python干重活，LLM干格式。** 预处理脚本负责所有数据提取和计算，cron agent只做格式化模板的工作。

## 关键文件

- `scripts/prepare_report_data.py`：跑extract.py → 取hot pool detail → 计算温度 → 提取信号 → 输出单个JSON（~12KB）
- Cron配置：`skills: []`（不加载SKILL.md），`enabled_toolsets: ["terminal","file"]`（最小工具集）

## 其他cron注意事项

- 所有加载销售助手的 skill 的 cron 都面临同样的31KB注入问题
- 低频cron（月度/30天验证）风险较低但建议同样改用预处理模式
- 周备份cron也加载了skill，如出现error优先考虑移除skill依赖
