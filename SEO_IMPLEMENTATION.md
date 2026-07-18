# Insighta ZIP 详细审计与 SEO 实施报告

**实施日期：2026-07-17**  
**项目形态：FastAPI + Jinja2 + SQLAlchemy 单体应用**  
**生产默认域名：`https://insightaco.org`（可通过环境变量覆盖）**

## 1. 执行结论

本次工作不是简单批量添加关键词，而是为项目建立了完整、可持续的 SEO 基础设施，并已经把它应用到公开页面与动态研究页面。

核心结果：

- 新增统一 SEO 策略模块 `app/seo.py`；
- 新增共享 `<head>` 模板 `app/templates/_seo_head.html`；
- 新增动态 `robots.txt` 与 XML sitemap；
- 新增可抓取的研究目录、六类分类页和内容型页面；
- 为动态研究详情生成唯一标题、摘要、canonical、Open Graph、Twitter Card 和 schema.org JSON-LD；
- 把登录、后台、支付、问卷作答、结果等非公开页面统一设为 `noindex`；
- 移除移动端首页跳转，桌面端与移动端统一使用一个响应式 canonical 首页；
- 将关闭研究从 sitemap 排除，并设置双层 `noindex`；
- 将研究封面从 CSS 背景图改为真实 `<img>`，改善图片抓取、可访问性和首屏资源发现；
- 增加静态审计、端到端测试和 GitHub Actions 回归检查；
- 清理首页无法由数据证明的硬编码数量、转化率、奖励和用户评价；
- 额外移除文档中的明文邮件密码，并取消不安全的默认管理员密钥。

最终自动检查结果：

- `python scripts/seo_audit.py --strict`：**59 项通过，0 警告，0 失败**；
- `python scripts/smoke_test_seo.py`：**通过**；
- `python scripts/smoke_test_llm_only.py`：**通过，且已隔离到临时数据库**；
- Python 编译检查：**通过**；
- 真实路由验证：公开页、sitemap、robots、发布/关闭研究、登录页和 404 均符合预期。

## 2. 原始 ZIP 架构分析

### 2.1 技术栈

- Web 框架：FastAPI；
- 页面渲染：Jinja2 服务端渲染；
- ORM：SQLAlchemy；
- 默认数据库：SQLite，支持通过 `DATABASE_URL` 使用其他数据库；
- 静态资源：FastAPI `StaticFiles`；
- 支付：Stripe；
- 登录：邮箱密码、Google OAuth、LinkedIn OAuth；
- 邮件：Gmail SMTP 或 Resend；
- AI：Anthropic；
- 部署：Vercel Python 构建配置。

服务端渲染本身对搜索抓取是有利的，因为主要正文在初始 HTML 中即可获得。但原项目没有把这一优势转化为系统化 SEO 能力。

### 2.2 原始页面与路由特征

原项目包含约 30 个 Jinja 页面和大量业务路由。真正适合公开索引的页面主要只有：

- 首页 `/`；
- 参与者入口 `/participant`；
- 动态招募分享页 `/r/{share_slug}`。

其余大多数页面属于账户、控制台、发布、支付、回答、结果和后台页面，不应进入搜索索引。

### 2.3 原始 SEO 基线问题

对基线副本的静态检查显示：

- **0 个 canonical 标签**；
- **0 个 robots/noindex 标签**；
- **0 个 JSON-LD 结构化数据块**；
- **0 个 Open Graph 元数据实现**；
- 没有 `robots.txt`；
- 没有 sitemap；
- 没有公开研究目录或分类落地页；
- 动态研究页只能依赖外部分享链接发现，形成“孤岛页面”；
- `/privacy` 与 `/terms` 已在页脚出现，但实际路由和页面不存在；
- 未使用统一模板布局，元数据无法集中维护；
- 登录、后台、支付、问卷答案和结果页面没有统一索引边界；
- 首页针对移动 User-Agent 执行 302 跳转到另一套入口，桌面与移动抓取内容不一致；
- 研究详情封面使用 CSS `background-image`，搜索引擎与辅助技术不容易将其识别为内容图片；
- 首页存在未经数据库或分析系统支撑的固定营销数字、性能比较及用户引语；
- 一个未使用的 `catogory.html` 遗留模板名称拼写错误，且原本指向失效的 `/survey/{id}` 链接；本次已将链接修正为公开分享页并保留模板，以避免未知外部依赖被破坏；
- 原文档中出现了看似真实的 Gmail 应用密码；
- 管理员鉴权代码带有固定默认密钥。

## 3. SEO 信息架构改造

### 3.1 新增公开抓取链路

新的公开信息架构为：

```text
/                            首页
├── /participant             参与者落地页
├── /studies                 全部公开研究目录
│   ├── /studies/research    一般研究
│   ├── /studies/academic    学术研究
│   ├── /studies/life        生活方式与健康
│   ├── /studies/market      消费者与市场研究
│   ├── /studies/clubs       社区与校园研究
│   ├── /studies/other       其他研究
│   └── /r/{share_slug}      动态研究详情
├── /about
├── /privacy
└── /terms
```

每个已发布研究至少可从研究目录或分类页通过标准 `<a href>` 到达，从而消除动态详情页的孤岛状态。

### 3.2 搜索意图映射

代码中的 `CATEGORY_CONTENT` 为各页面维护独立的：

- 页面标题；
- H1；
- Meta description；
- 分类介绍；
- 默认图片；
- 语义标签。

覆盖的主要搜索意图包括：

- recruit research participants；
- research participant recruitment platform；
- research studies for participants；
- paid research studies / volunteer research studies；
- academic research studies；
- consumer and market research studies；
- lifestyle and wellbeing studies；
- surveys and interviews。

关键词被自然放入标题、正文、内链和结构化数据中，没有使用隐藏文字或关键词堆砌。

## 4. 可复用 SEO 能力

### 4.1 `app/seo.py`

该模块集中处理：

- 生产域名规范化；
- canonical URL；
- 标题和摘要清洗、截断；
- 相对图片转绝对 URL；
- JSON-LD 安全序列化；
- Google/Bing 站点验证码；
- 首页、参与者页、目录页、分类页、研究页和内容页元数据；
- Organization、WebSite、WebPage、CollectionPage、ItemList、BreadcrumbList、ResearchProject schema。

用户创建的研究标题或描述在进入 JSON-LD 前会移除 HTML，并对 `<`、`>`、`&` 等字符进行转义，避免终止 `<script>` 标签。

### 4.2 `_seo_head.html`

所有页面都包含共享 head partial。公开路由显式传入 `seo` 对象；未传入的页面默认输出：

```html
<meta name="robots" content="noindex, nofollow, noarchive">
```

公开页则输出：

- meta description；
- robots 与 googlebot；
- canonical；
- Open Graph；
- Twitter Card；
- Google/Bing verification；
- JSON-LD；
- manifest 和 Apple touch icon。

这种“默认不索引、显式开放”的策略能显著降低以后新增后台页面时被误索引的风险。

### 4.3 HTTP 级索引保护

中间件对非公开路由增加：

```http
X-Robots-Tag: noindex, nofollow, noarchive
```

对所有 4xx/5xx 响应也增加 `noindex`。因此即使某个响应不是 HTML，仍有 HTTP 层保护。

公开但已关闭的研究使用：

```http
X-Robots-Tag: noindex, follow, noarchive
```

并在 HTML 中同步输出 noindex。

## 5. robots.txt 与 sitemap

### 5.1 `robots.txt`

`/robots.txt` 由应用动态生成，允许抓取公开页面，并限制 API、后台认证、连接回调、Webhook 和上传目录等不适合作为搜索结果的区域。同时在文件末尾声明生产 sitemap URL。

登录和其他私有 HTML 页面没有被 robots 直接屏蔽，以便合规爬虫可以读取其中的 `noindex`；这是刻意区分“抓取控制”和“索引控制”。

### 5.2 `sitemap.xml`

`/sitemap.xml` 动态包含：

- 首页；
- 参与者页；
- 研究目录；
- About、Privacy、Terms；
- 有已发布研究的分类页；
- 所有 `status == "published"` 的研究详情页。

关闭、草稿和未发布研究不会进入 sitemap。动态页面使用 `published_at` 或 `created_at` 生成 `lastmod`。输出限制为 sitemap 协议允许的 50,000 条以内。

环境变量 `SEO_INDEX_STUDIES=false` 可在需要时整体关闭公开研究索引与 sitemap 收录。

## 6. 动态研究页面优化

每个 `/r/{share_slug}` 已发布研究现在拥有：

- 根据研究标题生成的唯一 `<title>`；
- 从研究描述清洗得到的唯一 meta description；
- 不受 `?from=` 等参数影响的生产 canonical；
- Open Graph/Twitter 分享卡片；
- 可见面包屑；
- 指向首页、目录和分类页的内链；
- `ResearchProject`、`WebPage` 和 `BreadcrumbList` JSON-LD；
- 研究时长 `timeRequired`；
- 目标受众语义；
- `datePublished`；
- 真实 `<img>` 封面、alt、宽高、`fetchpriority="high"` 和异步解码；
- 页脚中的 Studies/About/Privacy/Terms 链接。

此外，复制分享链接和二维码现在使用配置的生产 canonical origin，避免预览域名或代理 Host 泄漏到长期分享 URL。

## 7. 首页与参与者页优化

### 首页

- 移除移动端 User-Agent 跳转，统一响应式页面；
- H1 和首屏正文明确描述研究招募和研究参与；
- 增加 `/studies`、`/about` 等真实内链；
- 用产品能力描述替换无法证明的固定用户数量、完成率和奖励数字；
- 删除无法验证的用户评价；
- 保留研究者/参与者交互切换，但初始服务端 HTML 已包含完整可理解内容；
- 修正联系邮箱和页脚信息。

### 参与者页

- 唯一标题和描述；
- logo 使用有效 alt 与尺寸；
- 增加公开研究目录入口；
- 增加服务端渲染的参与流程说明；
- 增加 About/Privacy/Terms 内链；
- 明确参与者逐项选择研究，而非自动报名。

## 8. 新增内容页面

新增：

- `about.html`：解释双边平台、研究者流程、参与者流程和产品原则；
- `privacy.html`：根据代码中实际处理的数据类型、Cookie、Stripe、OAuth、邮件、Anthropic 和外部研究工具编写产品行为说明；
- `terms.html`：覆盖研究者伦理与同意义务、参与者真实性、奖励审核、外部工具、可接受使用和平台限制。

Privacy 和 Terms 明确标注为产品实现草案；正式上线前仍必须由适用司法辖区的律师审核，并补充法律实体、管辖、保留期限、子处理者和争议条款。

## 9. 性能与可抓取性改进

- 启用 FastAPI GZip 中间件，压缩超过 1 KB 的响应；
- 静态资源增加适度浏览器缓存与 stale-while-revalidate；
- 公开目录图片具有固定宽高，减少布局抖动；
- 首屏研究图片优先加载，其余图片延迟加载；
- 新增内容页使用系统字体，避免额外字体阻塞；
- 首页保留 Google Fonts 但已有 preconnect；
- FastAPI 自动 `/docs` 与 `/redoc` 被关闭，避免形成不必要的可发现页面；
- 所有 HTML 响应设置 `Content-Language`；
- 所有响应设置 `X-Content-Type-Options: nosniff`。

## 10. 自动化质量门禁

### 10.1 静态审计

```bash
python scripts/seo_audit.py --strict
```

检查范围：

- 所有模板是否包含统一 SEO partial；
- 公开页面是否只有一个 H1；
- 公开标题是否来自 `seo.title`；
- viewport、lang、图片 alt；
- 是否存在不可抓取的占位链接；
- 所有公开 SEO 路由；
- canonical、robots、社交卡片、JSON-LD 和验证码；
- schema.org 上下文与 JSON-LD 安全；
- HTTP noindex；
- 关闭研究策略；
- robots 与 sitemap；
- 本地 SEO 图片是否存在；
- `.env.example` 是否覆盖全部 SEO 配置；
- manifest 完整性；
- 首页是否重新出现无法证明的硬编码营销数字；
- 文档是否误提交常见秘密值；
- Python 语法。

当前结果：**59 passed, 0 warnings, 0 failed**。

### 10.2 端到端测试

```bash
python scripts/smoke_test_seo.py
```

测试使用临时 SQLite 数据库，创建一个已发布研究和一个关闭研究，并验证：

- 所有公开路由状态；
- 移动 crawler 不发生首页跳转；
- canonical；
- meta description；
- Organization 和 ResearchProject JSON-LD；
- 研究目录真实内链；
- 关闭研究 noindex；
- 私有页 noindex；
- 404 noindex；
- robots sitemap 声明；
- sitemap 仅包含已发布研究。

当前结果：**SEO smoke tests passed**。

### 10.3 CI

`.github/workflows/seo-check.yml` 会在 push 和 pull request 时执行静态审计、安装依赖并运行端到端测试。

## 11. 配置与上线步骤

生产环境至少应设置：

```env
BASE_URL=https://insightaco.org
SEO_SITE_URL=https://insightaco.org
SEO_SITE_NAME=Insighta
SEO_CONTACT_EMAIL=insightacom@gmail.com
SEO_DEFAULT_IMAGE=/static/screenshot-desktop.png
SEO_LANGUAGE=en-US
SEO_LOCALE=en_US
SEO_INDEX_STUDIES=true
ADMIN_KEY=<long-random-secret>
```

上线后按顺序执行：

1. 确认 HTTP 全部重定向到唯一 HTTPS 主域；
2. 访问生产 `/robots.txt`，检查 sitemap URL；
3. 访问 `/sitemap.xml`，确认只含应公开 URL；
4. 在 Google Search Console 和 Bing Webmaster Tools 验证域名；
5. 提交 sitemap；
6. 对首页、目录、一个分类、一个已发布研究和一个关闭研究做 URL Inspection；
7. 检查 Open Graph 分享预览；
8. 用真实设备测试 Core Web Vitals；
9. 定期执行 SEO audit，避免新增模板绕过 noindex 或公共元数据；
10. 只有在研究标题、描述、封面和招募信息真实、具体、非重复时才发布研究。

## 12. 仍需业务方完成的工作

代码级 SEO 已实现，但排名不会仅由技术标签决定。以下工作依赖真实业务和内容，不能从 ZIP 中凭空生成：

- 用真实、可核验数据替代任何未来的用户规模、完成率或效果声明；
- 为每个公开研究编写具体、非模板化的标题和说明；
- 让研究者提供合适的封面图、资格条件、时长和奖励；
- 获取可信网站、大学实验室、研究团队和合作机构的自然引用与链接；
- 建立内容发布计划，例如研究招募指南、参与者指南、研究方法和伦理说明；
- 根据 Search Console 的真实查询、展现、点击和索引数据继续调整页面；
- 监控低质量或重复研究页面，必要时关闭索引；
- 完成隐私、条款、研究伦理和数据保留的正式法律审查。

## 13. 附带发现与修复

### 已修复

- 文档中的 Gmail 应用密码已清空；该凭据如果曾经真实使用，应立即在邮件服务端撤销并重新生成；
- 原有 LLM 冒烟测试导入主应用时会触发默认数据库迁移；现已强制改用一次性临时数据库，避免测试改写仓库内 `survey.db`；
- `ADMIN_KEY` 不再使用 `insighta-admin` 默认值，且比较改为 `hmac.compare_digest`；未配置管理员密钥时，提供任意空值都不会通过；
- 首页无法验证的数字和评价已移除；
- 页脚引用但不存在的 Privacy/Terms 已实现。

### 建议后续单独处理

- 项目启动时直接执行多处 `ALTER TABLE`，应迁移到 Alembic 或其他正式迁移机制；
- Cookie 登录模式值得进行独立安全审计，包括签名会话、CSRF、Secure/HttpOnly/SameSite 策略和权限边界；
- `api/main.py` 体积很大，应按认证、公开页面、研究、支付、后台、SEO 等领域拆分 router/service；
- 仓库中包含 `survey.db` 与 `surveybridge.db`；本次检查发现两者都包含用户记录及 Gmail 地址（未在报告中披露具体地址）。已将这些数据库加入 `.gitignore`，但交付包仍保留原始文件以避免破坏现有本地数据；正式共享或提交仓库前应备份并脱敏/移除；
- `catogory.html` 仍是未被当前路由引用且文件名拼写错误的遗留模板；其中失效链接已修复，可在后续确认无外部依赖后删除或重命名；
- 用户生成内容应增加发布审核、反垃圾和重复内容策略；
- 当前 sitemap 在请求时查询全部已发布研究，规模增长后应分页/缓存或拆分 sitemap index。

## 14. 变更文件索引

主要新增文件：

- `app/seo.py`
- `app/templates/_seo_head.html`
- `app/templates/studies.html`
- `app/templates/about.html`
- `app/templates/privacy.html`
- `app/templates/terms.html`
- `app/static/seo_public.css`
- `scripts/seo_audit.py`
- `scripts/smoke_test_seo.py`
- `.github/workflows/seo-check.yml`
- `SEO_IMPLEMENTATION.md`

主要修改文件：

- `api/main.py`
- `app/templates/index.html`
- `app/templates/participant_landing.html`
- `app/templates/recruitment_share.html`
- 所有其余 Jinja 页面（统一加入默认 noindex partial）
- `.env.example`
- `app/static/manifest.json`
- `README.md`
- `Insighta_README_current_v2.md`

