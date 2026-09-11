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

## 尚未执行

Docker 客户端可用，但 Docker Desktop Linux Engine 服务端管道不存在，因此没有启动 Keycloak，也没有实际导入 Realm 或执行网页登录验收。本轮未执行 factory reset，未删除或修改任何 Docker 镜像、容器、卷。

既有 `core/governance/ingest/raw/ricedata/ncbi/ai` 数据尚未接入隆耘查询层；其适配边界和后续映射项见 `YUNNAN-BOOTSTRAP.md`。
