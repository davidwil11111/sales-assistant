# config.json weak_phrases 已更新

**状态：✅ 已于 2026-06-09 更新。** 6个弱句 + emoji_danger 全部加入 config.json。

## 新增 weak_phrases

```json
"i'm truly touched",
"soy una niña pequeña",
"me pondré triste",
"no te molestaré más",
"going on and on about this is just bringing me down"
```

## 新增 emoji_danger（config.json detection 新字段）

```json
"emoji_danger": ["💔"]
```

## 为什么加

| 弱句 | 案例 | 严重度 |
|------|------|--------|
| `i'm truly touched` | 摩洛哥1T — 客户说价格合理，David用感动回应 | high |
| `soy una niña pequeña` | 委内瑞拉Maikel — 性别幼化 | critical |
| `me pondré triste` | 委内瑞拉Maikel — 情感绑架 | critical |
| `no te molestaré más` | 委内瑞拉Maikel — 内疚诱导 | critical |
| `going on and on...bringing me down` | 肯尼亚Erick — 向客户倾泻挫败感 | medium |
| `💔` (emoji_danger) | 埃及Ahmed/肯尼亚25T/加纳Signoriu — 3个客户 | high |

## 注意

- `soy una niña pequeña` / `me pondré triste` / `no te molestaré más` 是西语
- `💔` 作为emoji单独检测，放在 `emoji_danger` 数组，不在 weak_phrases 里混
- 新增后 weak_phrases 从15个增至19个
