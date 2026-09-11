# 隆耘 Agent 育种智能体（云南省农业科学院）

## 部署模式

本仓库同时保留两种明确隔离的运行方式：

- **本地开发与演示**：使用根目录 `docker-compose.yml`，入口为 `http://localhost:5183`；其中的本地端口、示例密码和调试设置不得用于正式环境。
- **农科院内网生产试点**：使用 `docker-compose.lan.yml`、`deploy/.env.production` 和 HTTPS 域名。共享服务器默认仅开放独立 HTTPS `8443`，数据库、API、文档解析和 Keycloak 均不直接暴露；获得专用反向代理后可切换至 `443`。共享 CentOS 7 服务器按 [CentOS 7 共享服务器上线步骤](docs/CentOS7共享服务器上线步骤.md) 执行，通用安全要求见 [内网生产上线实施手册](docs/内网生产上线实施手册.md)。

上线前务必完成域名与 HTTPS 证书、正式密码、备份位置、账号管理和外部 AI 数据外发策略确认；生产 API 会拒绝使用演示密码、非 HTTPS 回调或宽泛跨域配置启动。

本地演示版，包含网页/文字 PDF/Excel/CSV 导入、规则版本、质量检查、逐条审核、部分发布、科研查询、图表和下载。

## 启动

确保 Docker Desktop 已启动后，在本目录执行：

```powershell
docker compose up --build
```

浏览器打开 `http://localhost:5183`。

首次构建会安装 Docling、PaddleOCR 和 CPU 版 PyTorch，本机网络与磁盘性能不同，通常需要数分钟。后续启动会快很多。

停止服务：

```powershell
docker compose down
```

## 平台账号登录

浏览器会先跳转到 Keycloak 登录页，而不是使用页面中的模拟角色。为避免将账号密码提交到 Git，实际 Realm 导入文件 `keycloak/rice-research-realm.json` 仅保留在本机，且已被版本控制忽略。

首次从仓库部署时，复制安全示例并为所有账号设置独立的强密码：

```powershell
Copy-Item keycloak/rice-research-realm.json.example keycloak/rice-research-realm.json
```

示例内预置科研人员 `ynaas.researcher`、数据处理员 `ynaas.processor`、字段管理员 `ynaas.fieldadmin` 三类账号。占位密码只能用于初始化，启动前必须分别替换；所有账号首次登录也应修改密码。科研人员具有“已发布标准数据”的只读查询能力，但会话、上传附件、解析文本和压缩上下文严格隔离。数据处理员不能修改标准模板；字段管理员不能导入、审核或发布业务数据。

## 机构与课题边界

云南省农业科学院是默认机构，用户不需要创建、选择或切换机构。机构归属由受控的账号目录预先配置，登录时不会被请求参数或页面表单改写；每个账号只能进入所属机构及其有权课题。数据接入层可为每个已配置机构建立私有 MinIO Bucket 和独立 PostgreSQL 业务数据库，并在数据记录中继续保留 `institution_id`、`project_id` 和实体标识。当前原生数据库共存初始化默认关闭该独立数据平面，避免在未配置 MinIO 时另建数据库。详见 [云南原生 PostgreSQL 初始化说明](docs/YUNNAN-BOOTSTRAP.md)。

## 云南原生 PostgreSQL 初始化

目标库已有 `core/governance/ingest/raw/ricedata/ncbi/ai` 等业务 schema 时，使用下列脚本把隆耘结构非破坏性创建到 `public`：

```powershell
.\scripts\bootstrap-yunnan.ps1
```

脚本从本机私密 JSON 文件读取数据库与应用角色凭据，不在命令行或输出中显示密码；执行前后逐表比较所有非 `public` 表的精确行数，并拒绝连接非 `ynaas_rice_ai` 数据库。默认只创建表、视图、索引、约束、RLS、云南机构/默认课题/三类账号目录、标准模板和知识分类，不导入历史海南/江西演示业务数据。只有明确需要隔离演示环境时才能显式使用 `-IncludeDemoData`。

Docker Desktop 可用后，执行云南专用完整运行入口：

```powershell
.\scripts\set-yunnan-api-key.ps1
.\scripts\start-yunnan-docker.ps1
```

该入口先重新执行安全数据库初始化，再生成被 Git 忽略的 Keycloak Realm、三类账号随机初始密码和本地 HTTPS 证书，最后构建并启动 MinIO、MinerU、Keycloak、API、基因型 Worker 与 Web。`docker-compose.yunnan.yml` 明确禁用内部演示数据库，API 和 Worker 连接宿主机现有 `ynaas_rice_ai`；运行口令保存在仓库外的本机私密文件中，不会显示或提交。

启动后访问 `http://localhost:5183`，Keycloak 为 `https://localhost:8443`。本地证书为自签名证书，首次浏览器访问需人工确认；三类账号会强制首次修改密码。若要让智能体返回真实模型答案，还需在本机配置 `YUNNAN_API_KEY`，或把 AI Provider 指向可用的本地 vLLM，模型凭据不得提交。

## 云南 CherryIn 配置

运行 `scripts\set-yunnan-api-key.ps1` 隐藏录入 `YUNNAN_API_KEY`。密钥只保存在当前 Windows 用户的外部运行时密钥文件中，不要写进前端代码或提交到版本库。默认通过 CherryIn 调用 `agent/deepseek-v4-flash`。

智能体使用受控只读工具查询现有 PostgreSQL 中的品种、审定、系谱、基因别名、NCBI 注释和 GO 注释。工具只执行固定参数化模板，不接受模型生成的 SQL，并对结果数量和系谱深度设置上限。

## 可信公开资料检索

在 `.env` 中填写 `TAVILY_API_KEY` 后，科研助手支持“访问网站、找到某品种”、指定论文标题以及近期文献检索。显式公开检索保留当前问题中的公开标题/品种名，以指定域名限定 Tavily Search，最多再通过一次 Extract 获取两页公开正文摘录；不登录网站、不读取付费全文。未明确指定公开目标的背景检索仅发送通用主题词，不发送会话历史、附件或数据库内容；带私密/敏感信息标记的问题和内网、带凭据或签名的 URL 会被拦截。“不要联网”“仅查本地知识库”不会调用 Tavily。

**指定网页读取与关键词搜索分开处理。** 粘贴详情页链接并问“这里有什么信息”“总结这个页面”时，直接通过 Tavily Extract 读取该 URL，不先搜索这些问句，也不要求正文包含这些问句。域名首页加“找到某品种/搜索论文”仍走站内检索。支持“总结刚才的网页”等明确追问，只从近期用户消息中提取公开 URL，不向 Tavily 发送历史正文。直接读取每轮最多3个页面、每页最多18000字符；长页会明确标记截断，使用 Markdown 保留表格结构，图片中的文字/数字标记为未识别，禁止猜测或拼接数值。指定页面读取失败时只返回失败原因，不再让模型根据域名生成“可能包含”的网站介绍。

指定网页的模型总结若60秒仍未完成，会取消该次工具协议调用；在尚未输出部分回答时，使用同一正文进行一次无工具文本恢复，再失败则明确展示已取得的来源及状态，不无限等待。

结果附来源链接，区分搜索摘要与公开页面摘录；无匹配、限额、鉴权失败会如实提示。若模型的工具协议返回空回答，使用相同证据进行一次无工具文本恢复；仍失败则只列出已取得的来源及状态，并明确标记“模型未完成综合分析”，不再丢失本轮搜索结果。回归测试：`cd backend` 后执行 `python -m unittest tests.test_research_search tests.test_research_agent_search -v`。

检索结果只作为当前回答的补充证据：回答下方会显示来源卡片和原始链接，不会自动写入平台知识库或标准数据库。未配置 Key、检索额度不足或未找到可信来源时，助手会在流式进度中明确提示并继续使用本地证据回答。

## 本地解析与知识库模型预热

Docling、PaddleOCR 与 `bge-m3` 均在 API 容器本地运行。首次部署仍可联网时，执行一次：

```powershell
docker compose --profile warmup run --rm model-warmup
```

预热后的模型缓存保存在本地 Docker 卷 `model_data`。确认预热成功后，可在农科院内网环境关闭容器对外网的访问；常规 PDF、Office 文档和扫描件解析会读取本地缓存，不会把附件上传给第三方服务。`bge-m3` 只对通过本地解析合格的知识库文本生成 1024 维向量，不会在用户请求中临时下载模型。图片附件不使用 Docling 或 PaddleOCR 进行本地文字解析，提问时会将原图发送给已配置的 CherryIn 多模态服务进行视觉分析，因此正式部署时应纳入图片数据外发策略。

## 个人与公共知识库

- 研究人员可在“知识库”中维护“我的知识库”：支持文件夹、上传、元数据修改和永久删除。删除会同时删除本地原文件、解析文本、切片与向量。
- 字段管理员在“公共知识库”维护公共资料。公共资料必须经过本地解析预览、填写来源单位和分类后才能发布；新版本发布时需要填写变更说明，旧版本保留为“已替代”。
- 支持 PDF、Word、Excel、PPT、HTML、Markdown、TXT、CSV、JSON 和 XML；单文件不超过 100 MB，每次最多 10 个文件。图片仅作为科研助手的多模态临时附件，不进入知识库。
- 知识库页面只显示资料目录和助手引用信息，不提供正文阅读或下载。助手可按“我的 + 公共 / 仅我的 / 仅公共”检索本地向量，并在证据卡片中显示资料范围、标题、来源、定位和短摘录。

## 访问隔离

- Keycloak 负责真实登录、角色与 Token 签发；前端只携带短期 Bearer Token。
- API 使用非超级用户 `rice_app` 连接 PostgreSQL；迁移与建表使用独立的 bootstrap 账号。
- `research_session`、`research_message`、`research_attachment`、`research_audit`、`research_result` 同时按课题和登录账号启用并强制 PostgreSQL RLS。即使有人篡改会话 ID，也无法读取其他课题或其他研究人员的私有内容。
- `knowledge_folder`、`knowledge_document`、`knowledge_chunk` 同样按课题启用并强制 PostgreSQL RLS。私有知识库只能由资料所有者访问；研究人员只能检索当前课题已发布的公共资料，字段管理员才可核验、发布和撤回公共资料。
- 品种、表型、原始来源、区域试验、GWAS、基因型资产、知识、任务和成果均带 `project_id`；新数据归入云南省农业科学院有权课题。
- 六类统一导入的原始文件进入所属机构私有 MinIO Bucket，结构化实体和关联进入所属机构独立业务数据库；API 仍按账号机构和当前课题双重校验。
- 私有附件存入本地 Docker 卷，不提供对浏览器的直接文件路径访问；删除会话会一并删除附件、解析文本和压缩上下文。

## 说明

- 数据、规则、审核日志保存在本机 Docker 卷内。
- 文档解析优先使用 Docling；扫描型 PDF 或图片会由本地 PaddleOCR 兜底识别。OCR 结果可能存在识别误差，需通过“解析文字预览”核验。
- 网页 URL 只尝试读取单个页面，不进行整站抓取。
- 数据处理员导入前选择管理员发布的标准模板。当前保留六套结构化模板，并兼容两套历史导入模板；根系模板发布后的数据写入独立 `root_phenotype_observation` 表。
- Excel/CSV 会按每一行生成一个品种候选；确认“创建全部 N 个品种待处理草稿”后，每个品种会在待处理区独立展开、审核和发布。
- 未识别字段可从导入预览提交管理员处理。管理员在“标准模板管理”中发布带变更说明的新版本，数据处理员可从来源标签按最新版本重新处理原始文件。
- 当前 `docker-compose.yml` 设置 `ENABLE_SOURCE_DEDUPLICATION: "false"`，允许同一文件重复导入以便演示和调试。正式使用时改为 `"true"` 后重启服务，新导入将按文件内容哈希和网页地址阻止重复来源。
