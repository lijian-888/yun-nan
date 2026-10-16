# 直接关系采集与本地祖先图

主服务为 pedigree-direct（profile pedigree-direct）。完整网页展开程序及所有旧断点、
source_verified 快照保留为核验工具，旧 normal/complex 服务停止主采集，不删除数据。

先执行 pedigree-direct.sql。两个新表分别保存直接关系证据与来源材料待采集任务。
新来源材料按正整数来源编号加原文名称识别；同名不合并，无编号中间节点按所属关系片段与位置隔离。
采集只展开当前材料及其无编号中间组合，遇到具名、带来源编号的材料即转为待采集引用。
任务优先补齐发现的亲本，每个来源材料独立请求一次自己的直接关系。来源 ID 或名称不符不能强行替换。

启动前用 verify_pedigree_direct.py 对归档全树比较所有名称路径及重复出现次数。
原站问号/零值名称保留原文作为审计证据，界面显示“亲本名称待核实”，不计为完整图谱。
完整来源快照继续优先展示；没有完整快照的品种可展示直接关系及本地递归图，缺失、冲突、回环需明确提示。
回环的内部关系不作为有效親缘关系展示。原始片段保留，不删除、不填造、不静默修正原站事实。

已有 HTML 来源证据会用于初始化直接关系，不复用旧错误的 SQLite 全局节点/边。
初始化后继续采集所有目录材料以及新发现的祖先材料。断点位于 /data/research/pedigree-direct。
进度中的 complete 仅指直接亲本片段完成，不等于全库祖先图完整。
direct_queue_finished 仅表示直接采集队列结束；来源缺失、needs_review、冲突和回环仍需核验。
复用数据库不是新科学事实的证明，图谱完整只表示原站提供的有效关系已覆盖，不代表未知祖先有记录。

沿用 /data/research/pedigree-rebuild/request-rate.lock 及 source-blocked.json，
不提高原站请求启动频率，不绕过 403/429。部署后需更新定时检查，监控新服务和新进度路径。

## 2026年10月16日专项排查

27项已离线回放并逐项重新请求原站：不同具名节点具有相同 TreeView toggle 参数，点击后已有分支被折叠。按用户最新授权，SourceExpansionAmbiguity 不再等待完整展开，而是保留已采集的最大展开状态。`retain_source_limited_pedigrees.py` 离线保留现有断点的全部可见名称与关系；任务标为 source_limited、事实标为 verified_partial，不计入 complete/direct_count。原始名称、来源 HTML、点击前后页面及丢失路径保存在 `/data/research/pedigree-direct/recovery-audit-20261016/audit.json` 及相邻证据文件，迁移结果为 retained-source-limited.json。界面明确提示“原站展开受限”，不将无法展开的节点当作已知叶节点，不按复合名称猜测亲本，不再反复点击冲突按钮。京香636保留h9418及其已显示的父子关系。

`recover_pedigree_aliases.py` 仅处理两项明确、同来源 ID 的名称差异。先核实来源引用中的 ID 和原名，并重新获取独立档案的直接关系，与既存签名一致后，才以原系谱名保存带证据的关系片段。保留已有事实和原始简称，不做全库按名称或单独 ID 的无条件合并。

荃广优8238的上述名称缺口已补齐，本地/API图谱336节点405关系，但独立全树核验未完成前不得宣布 source verified。独立容器为 `ynaas-trial-pedigree-verify-631806`，结果在 `recovery-audit-20261016/independent-631806/result.json`。`verified` 必须同时满足全树确实展开完毕，名称路径及出现次数完全一致；`source_collection_incomplete` 或 `differences_found` 均不通过。其共享限速不变，运行时不要开启第二个原站采集通道。

当前 API 与直接采集镜像为 `ynaas-trial-api:source-limited-20261016`，运行时在现有 public-domain.yml 后追加服务器 `/home/lijian/apps/ynaas-trial-direct-9b12472/audit/source-limited.yml`，保留域名、认证及 Web 配置。33758项 complete、27项 source_limited；队列无 pending 时正常退出，27项已按受限策略处理，不再作为待修复失败重复通知。独立全树核验容器不受此次变更影响。

## 全库祖先缺口补全（2026年10月16日）

用户授权补全全部16285个祖先图不完整品种，优先云粳39号（variety_id=969、source_id=612639）。基线目录33784品种，按实际API优先快照规则，本地闭合17499、不完整16285；闭合不是全部经过独立官网全树验证。直接片段任务complete绝不参与此完成判定。

启动时进一步检查到岳州玉占（536/633231）、松雅500（537/633230）两份旧source_verified快照含未知名称，先前只看数据库fully_expanded标记把它们误计完整。纠正后的启动基线为本地闭合17497、不完整16287；原16285项全部保留，另2项补入队列。统计必须检查实际名称，不能只信旧完整标记。

`complete_ricedata_ancestors.py` 维护 `/data/research/pedigree-direct/ancestor-completion/queue.sqlite3`，包含全部不完整品种，而非只列27项展开冲突。`baseline.json`固定启动基线，`progress.json`分别统计pending、awaiting_source、collecting_source、closed_local、verified_source、source_limited、needs_review。closed_local只代表本地祖先引用闭合，不代表独立官网全树逐条核验。source_limited/needs_review均不是完整，不因队列一轮结束而宣布完成。

续接只接受同来源编号、官网明示的括号别名或去括号后的完整原名，并重新获取规范档案的直接关系，与既有来源签名一致；多种候选关系冲突、不明确简称和仅同编号均不自动合并。保留原系谱名和来源证据；无编号中间节点重新隔离上下文。当前规范页面每个编号只核实一次，共享结果，减少重复原站请求。证据保存在canonical目录及alias-evidence.jsonl。

不能安全续接的品种从自身官网完整树采集，按55秒/40展开切片轮转，保留full-source下断点。全部可展开节点实际展开后，检查未知名称和同路径来源身份回环；再追加来源核验快照，不覆盖已有事实。用实际API图谱序列化路径比较所有名称路径和重复出现次数，完全一致才写verified_source结果。云粳39号无论本地是否先闭合，都执行这一独立全树核验。

部署镜像`ynaas-trial-api:ancestor-completion-20261016`，服务`pedigree-ancestors`，容器`ynaas-trial-pedigree-ancestors-1`。在已有域名/直接服务配置后追加`ancestor-completion.yml`，仅启动该服务，不改API/Web/认证服务。保持一个原站请求通道，继续使用旧共享限速与403/429阻断文件；旧direct/rebuild/complex及荃广优8238独立核验容器退出不属异常，不重复启动。

另追加`ancestor-api.yml`升级API的快照完整度统计：未知名称快照不计verified_count，列表标source_incomplete。它不把direct_count当祖先完整数；认证与域名环境继续使用已有public-domain.yml，API重建后重载Web nginx上游解析。后续部署须同时保留ancestor-api.yml和ancestor-completion.yml，避免退回旧完整度统计。

荃广优8238独立核验已于2026-10-16T01:38:32Z完成，1214个名称路径出现项与本地图完全一致，missing_paths/extra_paths为空。此次全库任务不重复采它。

云粳39号已于2026-10-16T02:24:31Z独立完整核验：120图谱节点、156关系，756个名称路径出现项与原站完全一致，无missing_paths或extra_paths；追加快照43421，旧快照保留。source SHA-256为649e50ea055372168222b9cd0cc5eb3b20d0ee96d4cb04d22800e2ea134223c8a。API角色实际读取也为120/156、fully_expanded=true、无警告和未知名称。来源、完整节点、原始HTML、核验结果存于ancestor-completion/full-source/612639，不能把756个分支出现项说成756个独立材料。

## 目录节点计数与祖先图保持一致

2026-10-16复现138B（15073/628940）：目录把单个直接亲本片段的5节点/4关系当成全部已存节点，而实际API祖先图是165节点/188关系；该品种没有优先完整快照，非浏览器缓存问题。

`list_varieties`现在只对当前页需要直接关系组装的品种调用与图谱相同的`load_graph`，使用组装后节点数及有效关系数。同一来源在一页内复用组装结果，完整来源快照仍优先。不访问原站，不全库遍历，不把本地闭合标为原站独立核验通过。原片段数量单列为`direct_fragment_node_count`/`direct_fragment_edge_count`；超过安全上限或不能组装时计数为null并标`count_status=needs_review`，不回退到误导的小数字，也不拖垮整页。

前端改为“N个来源节点”，与右侧分母一致；选中品种后使用新读取图谱的实际总数，避免后台采集导致目录旧数字滞留。引用卡片不重复计入来源节点；零有效关系不能被表述成“原站没有亲本”。

热修复以实际在运行的API镜像`ynaas-trial-api:parent-75281c6`为基础，仅覆盖`pedigree_graph.py`及测试；Web以`ynaas-trial-release-9d479aa/frontend`为基础，仅更新工作区文案和摘要函数，保留该版本的亲本推荐等功能。构建模板为`Dockerfile.pedigree-counts`，镜像`ynaas-trial-api:pedigree-counts-20261016`和`ynaas-trial-web:pedigree-counts-20261016`，服务器审计目录`audit/pedigree-counts`。部署在现有域名/source-limited/ancestor-api/parent-release配置之后追加`ancestor-completion.yml`及`pedigree-counts-api.yml`，仅`up -d --no-deps --no-build api web`，不重启采集worker。重载Web与Gateway nginx的上游解析。

后续发布应包含本次源码改动；不要在较新业务版本后无条件追加这份基于旧业务版本的热修复镜像。只读实库回归：在API容器中运行`PYTHONPATH=/app python /app/tests/smoke_pedigree_directory_counts.py`，核对138B、00122－40－315、云粳39号、荃广优8238以及一整页目录与实际图谱计数。候选模块可通过`PEDIGREE_GRAPH_CANDIDATE`指定路径，不需改运行中应用或访问原站。
