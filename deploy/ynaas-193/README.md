# 172.16.123.193 云南试用版隔离部署

此配置专用于与小亿、隆耘同机的试用版。它使用独立 Compose 项目 `ynaas-trial`、独立 Docker 网络和数据卷、独立镜像名，以及未被现有服务占用的 HTTPS 端口 `19443`。PostgreSQL、API、Keycloak、MinIO 和解析服务不发布主机端口。首期基因型导入 Worker 不启动；现有基因数据查询不依赖这个 Worker。

云南试用版业务上不设课题：获批且未停用的账号直接进入院内统一工作台。旧数据库中的默认 `project_id` 仅作内部 RLS/存储兼容标识，不要求新科研账号拥有 `project_member` 记录，也不在网页上显示课题选择。既有成员记录保留，不执行删表或数据迁移。

## 院内账号开通

不开放公众自助注册。院方字段管理员在云南系统的“账号管理”页填写登录账号和真实姓名，即可开通**科研人员**账号；普通工作人员不进入 Keycloak 管理后台，也不选择课题。页面只显示一次随机临时密码，由管理员通过院内安全渠道交给本人；首次登录必须修改。账号启停会同步到 Keycloak，停用后应用接口还会逐请求校验本地授权目录。

账号开通只允许创建 `researcher`；数据处理员、字段管理员等高权限身份仍由乙方运维单独办理。所有开通和启停操作写入权限审计，临时密码不写入应用数据库或审计日志。

乙方在**服务器本机**完成一次服务账号配置：先确认 `deploy/ynaas-193/.env` 存在且仅部署用户可读，再执行 `python deploy/ynaas-193/configure-account-provisioner.py`。脚本通过当前临时运维管理员在 `rice-research` 域创建仅具 `realm-management/manage-users` 权限的服务账号，验证用户管理接口后将随机服务密钥写回权限为 `600` 的 `.env`；它不会输出任何口令。随后仅重建云南 API 以加载 `KEYCLOAK_PROVISION_CLIENT_ID` 和 `KEYCLOAK_PROVISION_CLIENT_SECRET`。不要把 `.env`、临时密码或服务密钥提交 Git。

当前部署的 `ynaas-admin` 是 Keycloak 引导阶段临时超级管理员。后续应先建立并验证永久运维管理员，再删除临时账号；不要将任何 Keycloak 超管账号交给院方日常使用。

**重要：不能直接使用仓库根目录的 `docker-compose.lan.yml` 单独上线。** 该文件默认创建 `rice_demo` 演示库；本覆盖文件将数据库改为 `ynaas_rice_ai`。2026-10-08 已经完成经授权的数据库迁移和核验，但这不等于四项业务功能均已完成验收。

## 上线前需要确认

1. 农科院允许将哪些真实数据复制到公司 193 服务器。用户已确认现有真实数据库可以迁移；测序文件、基因组大文件和数据库外的敏感附件尚未纳入本次迁移。
2. 模型 API 的服务商、模型名、服务器端密钥，以及哪些数据允许发送至外部模型。现有代码会拦截带私密标记的附件出站，但试用前仍须完成实际策略验收。
3. 试用入口是否可以使用自签名 IP 证书。正式对外使用时应改由可信 CA 或公司内网 CA 签发证书。
4. 数据库迁移方式及备份位置；源库和目标库均先做备份，再执行恢复。禁止把数据库口令或转储文件加入 Git。

迁移验收时，在源库与目标库分别执行 `verify-table-counts.sql`：表数量、总行数和逐表行数指纹均须一致，再允许 API 首次启动。该脚本只读业务表，不展示行内容。目标库启动后的应用维护可能变更 `public` 系统表，因此应在启动 API 前比较。

## 部署参数

- 路径建议：`/home/lijian/apps/ynaas-trial`（位于容量充足的 `/home` 分区）。
- 入口：`https://172.16.123.193:19443`；防火墙放行与否由管理员决定。
- Compose 文件：`docker-compose.lan.yml` + `deploy/ynaas-193/compose.override.yml`。
- 数据库镜像固定为与当前源库一致的 PostgreSQL 16 + pgvector 0.8.6；版本变更需先验证备份恢复兼容性。
- 环境文件：`deploy/ynaas-193/.env`，由 `env.example` 复制并填写，`chmod 600`，绝不提交。
- 资源上限：默认启动服务的内存上限合计约 10.4 GiB，CPU 配额合计 9.75 核；这是上限，不是常驻占用。共享主机仍需观察真实负载。这个限额优先保障四项试用功能，不承诺大文件 OCR/批量解析性能。

## 仅检查配置，不启动容器

在仓库根目录执行：

```bash
bash deploy/ynaas-193/prepare.sh
bash deploy/compose.sh --env-file deploy/ynaas-193/.env \
  -f docker-compose.lan.yml -f deploy/ynaas-193/compose.override.yml config -q
```

`prepare.sh` 只可首次执行：自动生成独立随机口令与含 IP SAN 的自签名证书，不启动容器，也不填模型 API 密钥；重复执行会拒绝覆盖既有口令或证书。自签名证书会引起浏览器安全提示，仅适用于经同意的内部试用。

本次 2026-10-08 快照完成加密传输和 SHA-256 校验、目标库确认为空、镜像 pgvector 版本为 0.8.6 后，可执行 `bash deploy/ynaas-193/restore-20261008.sh`。脚本遇到任一错误即停止，不清理、不覆盖已有业务表；完成后核对源/目标的逐表行数指纹。

检查通过、完成数据迁移后可启动基础服务；模型密钥可以后配，但缺失时不能把自然语言分析和推荐称为可用。不能对整台主机运行 `docker system prune` 或 `docker volume prune`；不能对小亿、隆耘的 Compose 项目执行 `down`。本项目将来停止时必须带上上述两个 Compose 文件和专用环境文件，且不要添加 `-v`。

## 基础查询修复：院内只读视图

部署新API前，先备份并由数据库管理员显式执行
`deploy/ynaas-193/migrations/001_query_views.sql`，本地与服务器使用同一迁移。
该迁移不修改原始数据，只建立`agent_data.material/material_alias/phenotype`三个业务视图，
API账号仅获这些视图的SELECT权限，不开放整个`core`或`ai`架构，不授权写入。
`LOCAL_INSTITUTE_QUERY_ENABLED=true`仅启用现有治理数据查询；原机构导入数据平面仍保持关闭。
适用边界是当前云南单机构统一工作区中获准使用这些共享治理数据的科研人员；
项目级私人附件仍走原权限体系。未来按课题拆分权限时必须再增加相应行级访问控制。
原始绝对文件路径、教师等身份字段不在本查询视图中。身份待核材料不返回确认性状值。

精确查询及审定选择走本地SQL，不依赖模型密钥；各次审定、院内观测分别展示。
院内查询结果和包含院内内容的历史消息标为local-only，禁止发送外部模型。
数据库故障会明确显示“暂时无法查询”，不当作未收录，也不返回演示数据。
自定义审定选择按钮可直接提交审定编号，另提供“查看全部”；身份歧义不允许跳过。

## 2026-10-08 部署记录

以下为2026-10-08首次部署信息，不代表当前模型配置：

- 官方 `pgvector/pgvector:0.8.6-pg16` 在 Docker Hub 直拉缓慢。通过 DaoCloud 公共镜像前缀按官方 linux/amd64 内容摘要拉取，再在本机标记为官方仓库标签；没有修改整机 Docker 镜像源，也没有重启其他项目。复现命令：

  ```bash
  docker pull m.daocloud.io/docker.io/pgvector/pgvector@sha256:eac621400b7b7ff52493883e41e930e3d104695fea5b68cc0c42370cf7880067
  docker tag m.daocloud.io/docker.io/pgvector/pgvector@sha256:eac621400b7b7ff52493883e41e930e3d104695fea5b68cc0c42370cf7880067 pgvector/pgvector:0.8.6-pg16
  ```

  拉取后核验了 `linux/amd64`、PostgreSQL 16.15 和 pgvector 0.8.6。其他构建基础镜像也使用相同的按官方摘要拉取、服务器本地标记方式；本项目镜像均使用 `ynaas-trial-*` 名称。
- 数据库快照 SHA-256：`9413f30994b1640d14b24a5a90e628a103c715278029b71dd9988e06a96626d5`。恢复前后均核对 133 张业务表、5,348,678 行，逐表行数指纹 `9b28e0a43b3bd4b890660f05c14007ee`。服务器原始备份保存在 `/home/lijian/apps/ynaas-trial-data-import/`，未加入 Git。
- `ynaas-trial` 的数据库、MinIO、解析服务、API、Web 和网关健康；Keycloak 登录页可访问。公司内网入口 `https://172.16.123.193:19443`；自签名证书会出现浏览器警告。验证了网页、`/api/health` 和 OIDC 发现接口返回 HTTP 200。
- 本次没有填写模型 API 密钥，也没有进行模型调用；自然语言问答、五性分析与亲本推荐不能据此认定为可交付状态。后续还需逐项开发和业务验收。
