# 云南原生 PostgreSQL 初始化验证记录（2026-09-11）

## 执行范围

- 仓库：`D:\IDEA-Project\yun-nan`
- 数据库：`127.0.0.1:5432/ynaas_rice_ai`
- 初始化模式：结构与必要系统配置；`DEMO_DATA_ENABLED=false`
- 全库备份：`D:\IDEA-Project\yun-nan-backups\pre-bootstrap-20260911-140619\ynaas_rice_ai.full.backup`
- schema 备份：`D:\IDEA-Project\yun-nan-backups\pre-bootstrap-20260911-140619\ynaas_rice_ai.schema.sql`
- pgvector：`0.8.6`

数据库凭据仅从本机私密 JSON 文件读取，未写入仓库或本记录。

## 数据库验证

初始化脚本连续执行三次均成功，后两次没有新增重复系统配置。独立 `psql` 验证结果如下：

| 项目 | 结果 |
|---|---:|
| 初始化前既有非 `public` 表 | 80 |
| 初始化前后既有表行数变化 | 0 |
| 既有表总行数（前/后） | 5,348,593 / 5,348,593 |
| 新建 `public` 表 | 52 |
| 新建 `public` 视图 | 1 |
| `public` 索引 | 192 |
| `public` 约束 | 138 |
| 无效索引 | 0 |
| 启用且强制 RLS 的表 | 16 / 16 |
| RLS 策略 | 18 |
| 应用角色缺少 DML 授权的 `public` 表 | 0 |

应用角色 `ynaas_app` 已验证可登录，且为 `NOSUPERUSER / NOCREATEDB / NOCREATEROLE / NOREPLICATION / NOBYPASSRLS`。

必要系统配置为 1 个云南机构、1 个默认课题、3 个互斥业务账号、1 个科研人员课题成员关系、8 套模板及版本、6 个公共知识分类和 1 套水稻基因型 QC 模板。`variety_basic`、`phenotype_observation`、`trial_data_package`、`breeding_material`、`breeding_program` 均为 0 行，确认没有导入海南/江西演示业务数据。

## 代码与构建验证

- 后端：82 个单元测试全部通过。
- Python 依赖：`pip check` 通过。
- 前端：`npm ci` 完成，Vite 生产构建通过。
- Compose：`docker compose config --quiet` 通过。
- Keycloak Realm：本地和生产两份 JSON 模板均通过 JSON 解析，包含 `rice-research-web` Client 与 `ynaas.researcher`、`ynaas.processor`、`ynaas.fieldadmin` 三类账号。

## Docker 完整运行验证

- Docker Desktop 4.80.0、Linux Engine 29.6.1 和 Docker Compose 5.1.4 已恢复可用。
- 云南覆盖配置完成 API、基因型 Worker、MinerU、MinIO、Keycloak 和 Web 的首次构建与启动；随后使用 `start-yunnan-docker.ps1 -NoBuild` 再次执行，六项服务全部复用原镜像、卷、Realm、证书和口令并保持运行。
- `ynaas-longyun-db` 容器不存在。API 与 Worker 的运行环境均指向 `host.docker.internal:5432/ynaas_rice_ai`，不包含 `rice_demo` 或 `@db:5432` 引用。
- `http://localhost:8000/api/health`、`http://localhost:5183`、`http://localhost:5183/api/health`、`http://localhost:9000/minio/health/live` 和 Keycloak OIDC discovery 均返回 200；MinerU 与 MinIO 的容器健康状态均为 `healthy`，所有六项服务重启次数为 0。
- Keycloak 日志确认 `rice-research` Realm 实际导入。管理 API 验证 3 个账号全部启用且分别只具有 `researcher`、`data_processor`、`field_admin` 角色；PKCE 登录挑战确认三组随机初始口令均有效并停留在强制修改密码页面，没有签发授权码。
- 容器启动和迁移后再次逐表核对：80 张既有非 `public` 表仍为 5,348,593 行，变化表为 0；五张演示业务表仍全部为 0 行。

本轮没有执行 factory reset，也没有删除或覆盖任何既有 Docker 镜像、容器或卷。Keycloak 使用仅限本机开发的自签名 HTTPS 证书，首次浏览器访问需由用户确认本地证书；生产部署不能复用该证书或 `start-dev` 模式。

## 当前运行边界

本地完整应用、鉴权、存储、文档解析和 ACPs Direct JSON-RPC 入口已经可运行。当前容器没有配置 `SHENNONG_API_KEY` 或本地 vLLM 凭据，因此需要真实模型回答时仍须在本机运行环境配置合法的模型服务凭据；不得提交到 Git。ACPs Group/Registry、RabbitMQ 与 mTLS 也未配置，不影响单机 Direct 模式。

既有 `core/governance/ingest/raw/ricedata/ncbi/ai` 数据尚未接入隆耘查询层；其适配边界和后续映射项见 `YUNNAN-BOOTSTRAP.md`。
