# Sarah 销售系统 — 架构决策记录

## ADR-1: 为什么18模块→3文件

**日期**: 2026-06-09
**决策**: 从6层18个Python模块砍到1个extract.py + 1个SKILL.md + 1个config.json

**原因**:
- 438客户/14K消息的数据量不需要微服务架构
- Agent本身是最强的分析引擎，Python应该只做数据搬运
- 多模块互相引用形成蜘蛛网状依赖
- 模板引擎生成的话术是废的——只有LLM能写上下文话术

**原则**: 薄Python + 强Agent。机械的事交给机器，判断的事留给Agent。

---

## ADR-2: 为什么对话原文不在clients.json里

**日期**: 2026-06-09
**决策**: clients.json只存元数据（jid/name/country/priority/pool/stats），对话原文通过`extract.py --detail <JID>`按需查询。

**原因**:
- 422客户×完整对话=320KB JSON → 32K tokens/次报告
- 1000客户时read_file直接拒绝
- sumamry.json(200 tokens) + 5×detail(200 tokens) = ~1,200 tokens

**原则**: Agent选客户用摘要，分析客户按需查详情。

---

## ADR-3: 池子自动裁决

**日期**: 2026-06-09
**决策**: extract.py对明确情况自动裁决池子：
- priority=high + B池 → 自动入热池
- cust_msg_count=0 + 超30天 → 自动进沉默池

**原因**: 首次运行时全部422客户在B池，hot_pool为空。Agent按SKILL.md"hot_pool全部选"→无客户可选。Agent在summary.json看到hot_pool=[]只能跳过分析。

**边界**: extract.py只做明确裁决。模糊情况（有客户消息但quality unclear等）留给Agent判断。

---

## ADR-4: 备份按天去重

**日期**: 2026-06-09
**决策**: 每天只备份一份wacli.db。同一天多次运行extract.py不重复备份。

**原因**: 测试阶段每天可能跑10+次extract.py。10×9.5MB=95MB/天→665MB/周。7份备份(按天)已经足够恢复。

---

## ADR-5: needs_attention不包含pool_suggestion

**日期**: 2026-06-09
**决策**: needs_attention只按analyzed_stale和is_new_customer_message触发，不按pool_suggestion触发。

**原因**: pool_suggestion大多是downgrade_to_dormant（超90天没联系的客户），不是真的"需要关注"。把降级建议混进needs_attention会让Agent误以为有41个客户需要紧急处理。

---

## ADR-6: 安全锁设计

**日期**: 2026-06-09
**决策**: 6类危险操作由Agent拦截：删除客户/删库/发消息/改config/改代码/危险命令。

**原因**: 最终用户不懂AI。一句"删了这个客户"可能导致客户数据永久丢失。Agent必须在执行前拦截+解释风险。

---

## ADR-7: 系统文件归集

**日期**: 2026-06-09
**决策**: 所有系统文件统一在`~/.hermes/skills/openclaw-imports/sales-assistant/`，不存在`~/old_sales/`或`~/.openclaw/workspace/`的副本。

- 双份文件导致不一致风险
- 敏感客户数据散落在`~/old_sales/`
- `~/old_sales/node_modules/`等和销售系统零关系的内容

**清理**: 删除`~/old_sales/`、`~/old_sales_backup/`、`~/.openclaw/workspace/skills/sales-assistant-backup/`。所有敏感文件归集到`data/backups/`。
