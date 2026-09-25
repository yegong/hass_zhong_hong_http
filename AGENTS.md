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

当前资料同时包含 `references/` 下的历史 Node-RED 流程和用户提供的 EigenStone 实机结果。开始实现或改变协议语义前，必须继续区分：

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

## 协议事实和参考观察

### 已由 EigenStone 实机确认

- 所有接口使用 HTTP Basic Auth；默认用户名为 `admin`、默认密码为空；
- 网关响应没有 HTTP 状态行或 headers，连接中直接返回 JSON body。请求本身仍发送普通 HTTP/1.1 GET；
- 网关信息请求为 `GET /cgi-bin/api.html?f=1`，确认响应字段：
  - `model`：型号代码；
  - `sw`：软件版本，可能含尾随空格；
  - `id`：设备编号，用作网关稳定标识；
  - `hwerror`：VRF 错误码；
  - `moduleerror`：第三方模块错误码；
- 网关信息每 5 分钟刷新一次，不随室内机状态密集轮询；
- VRF 网关和每台室内机在 HA device registry 中分别表示，室内机通过 `via_device_id` 关联到网关；
- 室内机状态请求为 `GET /cgi-bin/api.html?f=17&p=<page>`，页码从 `0` 开始；实机每个非空页最多返回 5 台室内机，遇到空 `unit` 数组结束；
- 实机成功响应中的 `err` 是数字 `0`；为兼容参考流程，解析也接受字符串 `"0"`；
- 室内机状态轮询可由 config flow/options 配置为 5–300 秒；
- 室内机字段语义：
  - `oa`：空调系统/外机编号；
  - `ia`：室内机编号；
  - `nm`：Admin Panel 设置的室内机名称；
  - `on`：开关状态；
  - `mode`：`1` 制冷、`2` 除湿、`4` 送风、`8` 制热；
  - `tempSet`：目标温度；
  - `tempIn`：当前室内温度；
  - `fan`：`1` 高、`2` 中、`4` 低；
  - `idx`：控制请求使用的室内机索引；
- `alarm` 已确认与滤网无关，Integration 直接忽略，不用于实体、诊断或控制；
- `grp`、各类 lock、`highestVal`、`lowestVal`、`FlowDirection1/2` 和 `MainRmc` 当前均不使用；
- 同一 `oa` 下不能同时运行不同 HVAC 模式，但约束由空调主机处理，Integration 不预判或联动其它室内机；
- 已确认控制请求为 `GET /cgi-bin/api.html?f=18&on=...&mode=...&tempSet=...&fan=...&idx=...`，参数使用普通单个 `&`；`FlowDirection1/2` 不需要发送；成功响应为 body-only `{"err":0}`；
- 控制按完整状态提交。修改一个字段时优先使用该内机已提交但尚未确认的状态补齐其它字段，否则使用最新轮询缓存；同一网关的控制请求不做全局串行化。控制成功后在 1 秒和 2 秒分别触发一次整网关回读。

以下未被实机覆盖的内容仍只能视为“参考实现观察”。

### 查询

- 参考流程用 `ac_<oa>_<ia>` 标识室内机；在 HA 中还必须加入网关/config-entry 范围，防止多个网关发生 entity unique ID 冲突。

分页实现必须有最大页数、总超时、重复页检测和异常数据保护，不能只依赖空数组结束，否则设备异常时可能形成无限轮询。

### 控制

- 一个较新的流程对指定新风内机只发送 `idx` 和 `on`，但这依赖硬编码内机地址 `6`。不得把该地址或特殊行为直接写死；先由实机数据或可配置能力确认。

### 认证和 HTTP 兼容性

- 认证信息不得出现在日志、diagnostics 或异常字符串中；diagnostics 必须脱敏 host 之外的敏感字段，是否脱敏 host 按 HA 官方规范处理；
- 实机已证明响应是 body-only，不能交给严格的标准 HTTP parser；正式 transport 使用 `asyncio.open_connection` 手工读取，兼容 body-only 和标准 HTTP 响应；
- 禁止把 `curl`、shell、Node-RED 或同步 `requests` 作为 Integration 的运行时方案；禁止为了兼容畸形 HTTP 而污染 coordinator/entity 层。

### 已观察枚举映射

模式代码：

| 设备值 | 候选 HA 模式 | 置信度/差异 |
| --- | --- | --- |
| `1` | `HVACMode.COOL` | 实机确认 |
| `2` | `HVACMode.DRY` | 实机确认 |
| `4` | `HVACMode.FAN_ONLY` | 实机确认 |
| `8` | `HVACMode.HEAT` | 实机确认 |
| `5` | `HVACMode.DRY` | 仅 2022 状态解析出现，待验证 |

风速代码：

| 设备值 | HA 风速字符串 | 置信度/差异 |
| --- | --- | --- |
| `0` | `auto` | 三份流程一致 |
| `1` | `high` | 实机确认 |
| `2` | `medium` | 实机确认 |
| `4` | `low` | 实机确认 |
| `6` | `silent` | 2019/2022 有，2024 HITACHI 流程没有 |

温度范围存在 `18–30 °C` 与 `16–32 °C` 两组参考值。未确认前，不得仅凭流程年份选择；优先从设备能力或实测确定。若协议没有能力字段，再设计明确、可迁移的设备 profile，避免在 entity 中写型号分支。

未知枚举值不得静默伪装成常见状态。解析层应保留足够诊断信息，entity 层采取安全 fallback，并以节流日志记录一次可操作的警告。

## 目标架构

本项目按 standalone custom integration 组织，domain 为 `zhong_hong_http`，与官方 `zhong_hong` 区分。若后续需要提交 Home Assistant Core，保持业务和协议模块可迁移，不依赖 HACS 专有运行时 API。

建议结构：

```text
custom_components/zhong_hong_http/
├── __init__.py          # config entry 生命周期、runtime_data、平台转发
├── manifest.json        # hub / local_polling / config_flow / version
├── const.py             # domain、配置键和稳定常量
├── config_flow.py       # UI 配置、连接验证、去重、reconfigure/reauth（如适用）
├── client.py            # 面向领域的异步 API；不依赖 HA entity
├── transport.py         # 基于 asyncio 的 body-only/标准 HTTP 兼容传输
├── models.py            # 严格类型、不可变或可比较的网关/室内机状态
├── profile.py           # 协议未提供的温度范围等显式、可迁移能力假设
├── coordinator.py       # 室内机/网关信息轮询、可用性与错误映射
├── monitor.py           # 可选的外部 climate 变化刷新触发器
├── entity.py            # 共享 CoordinatorEntity 基类（有真实共性时才创建）
├── button.py            # 网关级室内机立即刷新动作
├── climate.py           # 纯内存属性和异步控制方法
├── diagnostics.py       # 脱敏诊断（达到相应阶段时）
├── strings.json
└── translations/
    ├── en.json
    └── zh-Hans.json
tests/
├── fixtures/
├── test_button.py
├── test_client.py
├── test_config_flow.py
├── test_coordinator.py
├── test_climate.py
└── conftest.py
```

职责边界：

- `transport.py` 只处理 HTTP 字节、认证、超时和响应兼容；
- `client.py` 负责 URL 参数、分页协议、原始响应校验和枚举转换；
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

- 所有网络 I/O 使用 async；因实机返回 body-only 响应，transport 使用 `asyncio.open_connection`，不得切换为阻塞客户端；
- 室内机状态和网关慢速信息分别由 `DataUpdateCoordinator` 协调，并在 setup 时调用 `async_config_entry_first_refresh()`；
- 数据模型可比较时设置 `always_update=False`，减少无变化状态写入；
- 网关状态快照只以室内机数据判等；coordinator 通知后，每个 climate 实体还需独立比较自身室内机快照，只为真实变化或可用性变化写入 HA state；
- 室内机轮询间隔由 options 限制为 5–300 秒；网关身份、版本和错误码固定每 5 分钟轮询；
- VRF 网关 device 提供室内机状态刷新 button；按下后立即刷新室内机 coordinator 并重置其下一次轮询计时，不强制刷新网关信息 coordinator；
- coordinator 负责把通信错误转换为 `UpdateFailed`，首次连接失败交由 `ConfigEntryNotReady` 路径；认证失败使用相应 auth flow；
- entity 继承 `CoordinatorEntity`，由 coordinator 可用性驱动 unavailable；
- 动态新增室内机必须能在不重载 Integration 的情况下添加 entity。室内机暂时缺失时先标记不可用；删除 stale device 前需要明确、保守的策略；
- 设置适当的 `PARALLEL_UPDATES`；控制请求无需按网关串行化，分页查询只保证单次查询内的页面顺序。

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

1. `f=18` 失败响应；
2. 设备支持的温度范围、fan `0/6`、mode `5` 的真实含义；
3. `idx` 是否跨重启稳定，`oa + ia` 是否在单网关内永久稳定；
4. `nm` 的编码边界、是否为空、重命名后的行为；
5. 是否确有“新风机只允许开关”的 EigenStone 场景，以及如何可靠识别；
6. Admin Panel 是否允许非空密码或不同用户名；
7. `hwerror`、`moduleerror` 非零值的完整码表与恢复语义。

在这些问题未确认前，可以实现严格、可测试的框架和已一致的映射，但不要声称覆盖所有中弘/日立/Aqara 变体。

## 提交和变更纪律

- 每次改动围绕一个清晰目标，协议、HA 平台和无关重构不要混成一个提交；
- 不修改用户已有的无关文件，不覆盖未提交工作；
- 新增依赖前说明必要性。当前协议 transport 只使用 Python 标准库 asyncio，避免为很小的协议引入重量级库；
- 不为尚未观察到的型号建立复杂兼容层；出现真实差异后以 fixture 驱动 profile/strategy；
- 协议字段与 HA 领域字段只在一个映射模块转换，禁止复制 magic numbers；
- 修复失败测试时先判断是协议、领域、HA 生命周期还是工具链问题，在对应层修复；
- 总结必须列出修改、验证、未验证风险以及是否提交。
