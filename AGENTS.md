# AGENTS.md

## 项目目标

本仓库用于开发 Home Assistant 自定义 Integration，将 Aqara EigenStone 版所使用的中弘 VRF Admin Panel 本地 HTTP 协议接入 Home Assistant。

首要交付目标：

- 通过网关枚举室内机，并为每台室内机创建 `climate` 实体；
- 在 Home Assistant 中正确展示开关、运行模式、设定温度、室温和风速；
- 从 Home Assistant 控制上述能力，并在控制后以设备回读状态为准；
- 以标准 Home Assistant Integration 形态安装和运行，不依赖 Node-RED 或 MQTT；
- 通信与 Integration 主路径保持异步，不阻塞 Home Assistant event loop。

除非用户明确扩展范围，本项目不负责修改 Aqara 固件、搭建 MQTT 桥、提供独立 Web 服务，也不把参考 Node-RED 流程原样移植成运行时依赖。

## 当前阶段和执行边界

当前资料来自 `references/` 下的历史 Node-RED 流程，尚未经过 EigenStone 实机逐项验证。开始实现或改变协议语义前，必须先区分：

- **已确认**：由用户实机响应、抓包或自动化测试 fixture 证明；
- **参考实现观察**：只在 Node-RED 流程中出现；
- **待验证**：不同流程互相冲突，或设备语义无法从字段名确定。

不得把“参考实现观察”写成厂商协议保证。协议差异应收敛在 transport/client/model 层，不得散落在 HA entity 属性和服务方法中。

若任务涉及架构、协议映射或持久标识变更，先给出简短设计说明，再写代码。遵守仓库中的 `COLLABORATION.md`，尤其是模块边界、单一事实来源、不确定性显式化和先验证再总结。

## 参考资料阅读顺序

处理协议或实体行为前，至少检查以下内容：

1. `references/flows_HITACHI.zip`：2024 年流程，包含 `curl --http0.9`、Basic Auth、16–32 °C 和日立/新风相关分支；
2. `references/zhonghong.json.zip`：2022 年流程，包含 18–30 °C、静音风速及另一组模式映射；
3. `references/flows.json.zip`：2019 年较早流程，使用 Node-RED HTTP Request 节点；
4. Home Assistant 官方开发文档，以开发当日内容为准：
   - Integration manifest: <https://developers.home-assistant.io/docs/creating_integration_manifest/>
   - Config flow: <https://developers.home-assistant.io/docs/core/integration/config_flow/>
   - Fetching data / coordinator: <https://developers.home-assistant.io/docs/integration_fetching_data/>
   - Climate entity: <https://developers.home-assistant.io/docs/core/entity/climate/>
   - Integration Quality Scale rules: <https://developers.home-assistant.io/docs/core/integration-quality-scale/rules/>
   - Async blocking operations: <https://developers.home-assistant.io/docs/asyncio_blocking_operations/>

压缩包是只读证据。不要改写它们；需要 fixture 时，将脱敏后的最小响应样本单独放进 `tests/fixtures/`，并记录其来源和已做的脱敏。

## 从 Node-RED 参考实现提取的协议事实

以下均属于“参考实现观察”，不是最终协议规格。

### 查询

- 请求形式：`GET http://<host>/cgi-bin/api.html?f=17&p=<page>`；
- 页码从 `0` 开始递增；
- 响应被当作 JSON，成功条件为 `err == "0"`；具体设备是否返回数字 `0` 待验证；
- 响应中的 `unit` 是本页室内机数组；遇到空数组即结束本轮分页；
- 已观察字段：
  - `idx`：控制请求使用的索引；
  - `oa`：外机地址；
  - `ia`：内机地址；
  - `nm`：部分流程使用的名称；
  - `on`：开关；
  - `mode`：运行模式代码；
  - `tempSet`：目标温度；
  - `tempIn`：室内温度；
  - `fan`：风速代码。
- 参考流程用 `ac_<oa>_<ia>` 标识室内机；在 HA 中还必须加入网关/config-entry 范围，防止多个网关发生 entity unique ID 冲突。

分页实现必须有最大页数、总超时、重复页检测和异常数据保护，不能只依赖空数组结束，否则设备异常时可能形成无限轮询。

### 控制

- 请求形式：`GET http://<host>/cgi-bin/api.html?f=18&idx=...&on=...&mode=...&tempSet=...&fan=...`；
- 协议看起来采用“提交完整状态”而非单字段 patch。修改一个字段时，应以该室内机最新缓存状态合成完整控制请求；
- 并发控制可能产生 read-modify-write 丢失，所有同一网关的写操作必须序列化；
- 成功写入后请求 coordinator refresh，最终状态以设备回读为准；不要长期保留与设备不一致的 optimistic state；
- 一个较新的流程对指定新风内机只发送 `idx` 和 `on`，但这依赖硬编码内机地址 `6`。不得把该地址或特殊行为直接写死；先由实机数据或可配置能力确认。

### 认证和 HTTP 兼容性

- 2022/2024 流程发送 `Authorization: Basic YWRtaW46`，其含义是用户名 `admin`、空密码；
- 认证信息不得出现在日志、diagnostics 或异常字符串中；diagnostics 必须脱敏 host 之外的敏感字段，是否脱敏 host 按 HA 官方规范处理；
- 2022/2024 流程使用 `curl --http0.9`，说明设备可能返回非标准 HTTP/0.9 风格响应；2019 流程则使用普通 HTTP Request 节点，行为存在差异；
- 首选 `aiohttp` 和 Home Assistant 注入的共享 `ClientSession`。只有 fixture 或实机证明 aiohttp 无法解析该设备响应时，才允许在独立 transport 层实现基于 `asyncio.open_connection` 的最小异步兼容传输；
- 禁止把 `curl`、shell、Node-RED 或同步 `requests` 作为 Integration 的运行时方案；禁止为了兼容畸形 HTTP 而污染 coordinator/entity 层。

### 已观察枚举映射

模式代码：

| 设备值 | 候选 HA 模式 | 置信度/差异 |
| --- | --- | --- |
| `1` | `HVACMode.COOL` | 三份流程一致 |
| `2` | `HVACMode.DRY` | 三份流程基本一致 |
| `4` | `HVACMode.FAN_ONLY` | 2019/2024 明确；2022 的展示列表与状态分支冲突 |
| `8` | `HVACMode.HEAT` | 三份流程一致 |
| `5` | `HVACMode.DRY` | 仅 2022 状态解析出现，待验证 |

风速代码：

| 设备值 | HA 风速字符串 | 置信度/差异 |
| --- | --- | --- |
| `0` | `auto` | 三份流程一致 |
| `1` | `high` | 三份流程一致 |
| `2` | `medium` | 三份流程一致 |
| `4` | `low` | 三份流程一致 |
| `6` | `silent` | 2019/2022 有，2024 HITACHI 流程没有 |

温度范围存在 `18–30 °C` 与 `16–32 °C` 两组参考值。未确认前，不得仅凭流程年份选择；优先从设备能力或实测确定。若协议没有能力字段，再设计明确、可迁移的设备 profile，避免在 entity 中写型号分支。

未知枚举值不得静默伪装成常见状态。解析层应保留足够诊断信息，entity 层采取安全 fallback，并以节流日志记录一次可操作的警告。

## 目标架构

本项目按 standalone custom integration 组织，暂定 domain 为 `zhong_hong`。若后续需要提交 Home Assistant Core，保持业务和协议模块可迁移，不依赖 HACS 专有运行时 API。

建议结构：

```text
custom_components/zhong_hong/
├── __init__.py          # config entry 生命周期、runtime_data、平台转发
├── manifest.json        # hub / local_polling / config_flow / version
├── const.py             # domain、配置键和稳定常量
├── config_flow.py       # UI 配置、连接验证、去重、reconfigure/reauth（如适用）
├── client.py            # 面向领域的异步 API；不依赖 HA entity
├── transport.py         # aiohttp；必要时隔离 HTTP/0.9 兼容
├── models.py            # 严格类型、不可变或可比较的网关/室内机状态
├── coordinator.py       # 唯一轮询入口、分页聚合、可用性与错误映射
├── entity.py            # 共享 CoordinatorEntity 基类（有真实共性时才创建）
├── climate.py           # 纯内存属性和异步控制方法
├── diagnostics.py       # 脱敏诊断（达到相应阶段时）
├── strings.json
└── translations/
    ├── en.json
    └── zh-Hans.json
tests/
├── fixtures/
├── test_client.py
├── test_config_flow.py
├── test_coordinator.py
├── test_climate.py
└── conftest.py
```

职责边界：

- `transport.py` 只处理 HTTP 字节、认证、超时和响应兼容；
- `client.py` 负责 URL 参数、分页协议、原始响应校验、枚举转换和写入串行化；
- `models.py` 表达稳定领域语义，不向 HA 层泄漏 `tempSet` 等来源字段名；
- `coordinator.py` 一次轮询整个网关并生成按稳定室内机 ID 索引的数据；
- `climate.py` 只把 coordinator 内存状态映射为 HA 属性，不能在 property 中 I/O；
- `config_flow.py` 只处理配置和连接验证，不复制完整轮询业务。

数据流应保持单向：

```text
ConfigEntry -> async client -> coordinator -> climate entities
                                  ^               |
                                  |--- command ---|
```

## Home Assistant 实现约束

### Integration 形态

- 使用 UI config flow；不新增 YAML 配置入口；
- custom integration 的 `manifest.json` 必须包含 `version`，并明确：
  - `integration_type: "hub"`；
  - `iot_class: "local_polling"`；
  - `config_flow: true`；
- `ConfigEntry.data` 保存建立连接必需的配置，非连接型调节项才放入 `ConfigEntry.options`；
- config flow 在创建 entry 前必须实际测试连接和最小协议响应，并阻止同一网关重复配置；
- host/credentials 变化使用 reconfigure/reauth flow，不要求用户删除重建；
- 没有 mDNS、SSDP 或 DHCP 证据前，不声明网络自动发现。项目第一阶段的“发现”指从 `f=17` 动态发现室内机；
- Integration 必须支持 unload/reload，不遗留 listener、task 或 session；共享 session 不由本 Integration 关闭。

### 异步与轮询

- 所有网络 I/O 使用 async；首选 `async_get_clientsession(hass)` 注入共享 aiohttp session；
- 使用 `DataUpdateCoordinator` 对整个网关做一次协调轮询，并在 setup 时调用 `async_config_entry_first_refresh()`；
- 数据模型可比较时设置 `always_update=False`，减少无变化状态写入；
- 轮询间隔必须作为有依据的单一常量，不复制 Node-RED 的 500 ms 连续循环。先用实机响应时间和控制体验验证合理值；
- coordinator 负责把通信错误转换为 `UpdateFailed`，首次连接失败交由 `ConfigEntryNotReady` 路径；认证失败使用相应 auth flow；
- entity 继承 `CoordinatorEntity`，由 coordinator 可用性驱动 unavailable；
- 动态新增室内机必须能在不重载 Integration 的情况下添加 entity。室内机暂时缺失时先标记不可用；删除 stale device 前需要明确、保守的策略；
- 设置适当的 `PARALLEL_UPDATES`；coordinator 只串行化读，不自动保护写操作，因此 client 仍需写锁。

### Climate 映射

- 使用 `ClimateEntity`、`HVACMode`、`ClimateEntityFeature` 和 `UnitOfTemperature.CELSIUS`，不使用废弃字符串常量；
- 至少实现设备实际支持的：目标温度、HVAC mode、fan mode、turn on/off；不虚构 `hvac_action`、preset、swing 或 humidity；
- `hvac_mode` 在 `on == 0` 时为 `HVACMode.OFF`；开机时按已验证枚举映射；
- `async_set_hvac_mode`、`async_set_temperature`、`async_set_fan_mode`、`async_turn_on`、`async_turn_off` 通过 client 提交命令；
- 温度边界和步长必须来自已验证 profile/capability，并在协议边界再次校验；
- 每台室内机有稳定 `unique_id` 和 `DeviceInfo`。网关作为 hub device，室内机作为经由网关连接的 device；不要用 IP 地址作为室内机 ID；
- 新实体使用 `_attr_has_entity_name = True`。作为设备主要功能的 climate entity 通常 `_attr_name = None`；名称优先使用有效的 `nm`，否则使用可预测的地址回退名；
- 实体属性只读 coordinator 内存，绝不触发 HTTP 请求。

### Runtime data、类型和安全

- 用带泛型的 typed `ConfigEntry` 和 dataclass 保存 client/coordinator 等 runtime data；不得使用 `hass.data[DOMAIN][entry_id]` 作为默认设计；
- 新代码完整类型标注，避免 `Any` 跨模块扩散；外部 JSON 先验证再构造 typed model；
- 不记录密码、Authorization header 或完整控制 URL；调试日志若含查询参数，先确认其中没有凭据和用户敏感信息；
- 设备只允许访问用户配置的本地 host。不得跟随到任意远端的重定向；需要处理 IPv4、IPv6/hostname 的 URL 规范化，禁止字符串随意拼 URL；
- 所有请求有显式 connect/read/total timeout；错误需保留可诊断原因，但不能泄漏凭据。

## 测试与验收

新增行为必须伴随测试。最低覆盖：

- 正常单页、多页和空首页查询；
- `err` 为字符串/数字、缺字段、错误类型、畸形 JSON、重复页和超过页数上限；
- 认证失败、连接失败、超时、设备恢复后的 available 状态；
- 所有已确认 mode/fan 映射，以及未知枚举；
- 两组温度边界在对应 profile 下的行为；
- 控制请求保留未修改字段，写操作不会并发丢失；
- 控制后 refresh 及设备拒绝/未生效时的状态；
- config flow 成功、无法连接、认证失败、重复配置、reconfigure/reauth；
- config entry setup、unload、reload；
- 动态发现新室内机、暂时缺失和重新出现；
- entity unique ID、device registry 关系、名称和 supported features；
- diagnostics 脱敏；
- 标准 HTTP 与经证据确认后的 HTTP/0.9 兼容 transport。

测试不得依赖真实网关或公网。协议样本使用脱敏 fixture，HTTP 层使用可控 fake server/mocks。涉及 HA 生命周期的测试使用 Home Assistant 官方测试模式；协议 client 的大部分测试保持为独立、快速的 asyncio 单元测试。

交付前按仓库实际工具配置执行并汇报：

1. 格式化和 lint；
2. strict type check；
3. 全量 pytest 与覆盖率；
4. Home Assistant/HACS validation（仓库采用后）；
5. 检查 `git diff`，确认没有改动 reference 压缩包或提交凭据。

若某项因环境缺失不能运行，明确写出“未运行”、原因和风险，不能用“应该通过”代替。

## 开发决策和待验证清单

开始首版实现前，优先取得以下证据；若用户暂时无法提供，则把假设隔离为 profile/fixture，并保持保守默认：

1. EigenStone 实机的 `f=17&p=0` 脱敏原始响应及最后一页响应；
2. 实际 HTTP 状态行/headers，确认是否真的需要 HTTP/0.9 兼容；
3. `f=18` 成功与失败响应，以及是否必须发送所有字段；
4. 设备支持的温度范围、fan `6`、mode `4/5` 的真实含义；
5. `idx` 是否跨重启稳定，`oa + ia` 是否在单网关内永久稳定；
6. 是否存在网关序列号、型号、固件版本或能力 endpoint；
7. `nm` 的编码、是否为空、是否由用户在 Admin Panel 中可修改；
8. 是否确有“新风机只允许开关”的 EigenStone 场景，以及如何可靠识别；
9. Admin Panel 是否允许非空密码或不同用户名；
10. 合理轮询周期和设备可承受请求速率。

在这些问题未确认前，可以实现严格、可测试的框架和已一致的映射，但不要声称覆盖所有中弘/日立/Aqara 变体。

## 提交和变更纪律

- 每次改动围绕一个清晰目标，协议、HA 平台和无关重构不要混成一个提交；
- 不修改用户已有的无关文件，不覆盖未提交工作；
- 新增依赖前说明必要性。优先使用 HA 已提供的 aiohttp 和 helper，避免为很小的协议引入重量级库；
- 不为尚未观察到的型号建立复杂兼容层；出现真实差异后以 fixture 驱动 profile/strategy；
- 协议字段与 HA 领域字段只在一个映射模块转换，禁止复制 magic numbers；
- 修复失败测试时先判断是协议、领域、HA 生命周期还是工具链问题，在对应层修复；
- 总结必须列出修改、验证、未验证风险以及是否提交。
