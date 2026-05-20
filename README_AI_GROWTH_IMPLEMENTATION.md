# Insighta AI Growth 完整实现包

本实现包接入两个能力：

1. **AI 受访者完成率预测 V0**：规则评分 + 可解释原因 + 发布者预测面板 + 候选人 Top N。
2. **一键跳转网关**：统一处理外部问卷、内置问卷、访谈预约的 Response 创建/复用、JumpEvent 埋点、token、return_url、状态闭环。

## 文件结构

```text
app/ai_growth/
  __init__.py
  models.py          # JumpEvent / RespondentPrediction / SurveySegmentStats / UserActivityEvent
  security.py        # https URL 校验、token hash、next 安全校验、脱敏 hash
  matching.py        # 从 dashboard 抽象出来的画像匹配与 0~1 匹配分
  jump.py            # 一键跳转网关核心逻辑
  segments.py        # 人群段生成与漏斗统计
  prediction.py      # V0 规则完成率预测
  routes.py          # 所有 API / 页面跳转路由
scripts/
  install_ai_growth.py       # 自动安装并 patch 当前仓库
  create_ai_growth_tables.py # 可选，手动创建新增表
```

## 安装方式

推荐方式一：把本实现包直接解压到 Insighta 仓库根目录，然后执行：

```bash
python scripts/install_ai_growth.py
python -m py_compile api/main.py app/ai_growth/*.py
python scripts/create_ai_growth_tables.py
```

如果你把实现包解压成了一个独立目录，也可以在 Insighta 仓库根目录执行：

```bash
python path/to/extracted/scripts/install_ai_growth.py --repo-root .
```

启动项目：

```bash
uvicorn api.main:app --reload --host 0.0.0.0 --port 8000
```

## 新增接口

```text
GET  /surveys/{survey_id}/jump?source=dashboard
POST /api/surveys/{survey_id}/jump/start?source=dashboard
GET  /surveys/{survey_id}/return?token=xxx&status=returned
POST /surveys/{survey_id}/return?token=xxx
POST /surveys/{survey_id}/complete-with-token?token=xxx
GET  /api/surveys/{survey_id}/prediction/me
GET  /api/surveys/{survey_id}/prediction/summary
GET  /api/surveys/{survey_id}/prediction/respondents?limit=20
POST /api/prediction/recompute
POST /api/prediction/preview
POST /api/activity/impression
```

## 环境变量

```bash
# 推荐生产环境配置，未配置时会使用开发默认值
export AI_GROWTH_TOKEN_SECRET='replace-with-a-long-random-secret'

# 可选：限制外部问卷平台域名，逗号分隔；为空则允许任意 https 域名
export AI_GROWTH_ALLOWED_EXTERNAL_DOMAINS='qualtrics.com,google.com,typeform.com'
```

## 验收用例

1. 参与者进入 dashboard，卡片会显示 AI completion fit。
2. 点击 Start & Jump，后端先创建或复用 Response，再写入 JumpEvent。
3. 内置问卷跳 `/surveys/{id}/take?rid=...&token=...`。
4. 外部问卷跳安全 https 链接，并拼接 `insighta_rid`、`insighta_token`、`return_url`。
5. 外部 return_url 返回后可点击确认完成，Response 变为 completed，Notification 进入 publisher 审核链路。
6. Publisher 页面每个 listing 展示 AI Completion Forecast，可 Recompute 与查看 Top candidates。

## 设计边界

- 敏感属性只参与资格过滤，不作为解释原因展示。
- 预测 V0 不依赖大模型和额外训练依赖，先积累 JumpEvent/Response 数据。
- 所有新增数据库对象均为 additive table，不破坏原表结构。


## Debug 后修复说明

见 `DEBUG_REPORT.md`。本版安装脚本已经支持直接解压和嵌套解压两种布局，并修复了返回页 HTML 转义、FastAPI request 参数、异常 survey_id 处理等问题。
