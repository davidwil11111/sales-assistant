# Unicode引号归一化 — 已知陷阱

## 问题

WhatsApp消息使用智能引号（RIGHT SINGLE QUOTATION MARK `'` = U+2019），而 config.json 使用直引号（APOSTROPHE `'` = U+0027）。导致短语匹配静默失败。

## 表现

- `"i'm truly touched"` 出现在DB消息 `"I'm truly touched"` 中
- 但 `text.lower()` 匹配 `"i'm truly touched"` → **False**
- 原因：`I'm` 中的 `'` 是 U+2019，搜的是 U+0027

## 影响范围

`extract.py` 的两处短语匹配：
1. `auto_detect()` — 弱句检测
2. `update_phrase_correlations()` — 风格指纹追踪

## 修复

```python
def norm(s):
    return (s or "").lower().replace("\u2019", "'").replace("\u2018", "'")
```

两处短语匹配前都先 `norm()` 归一化。```text` 和 `phrase` 两侧都归一化。

## 排查方法

```python
text = msg_from_db.lower()
print(repr(text))  # 看引号字节码
# 如果看到 \u2019 而不是 ' → 归一化后再匹配
```

## 潜在新增需要处理的码点

| 码点 | 字符 | WhatsApp出现 |
|------|------|-------------|
| U+2018 | ' | 左单引号 |
| U+2019 | ' | 右单引号 ✅ 已处理 |
| U+201C | " | 左双引号 |
| U+201D | " | 右双引号 |
| U+2014 | — | em dash |

当前只归一化了 `\u2018` 和 `\u2019`。如果未来弱句包含其他智能标点，需扩展 `norm()`。
