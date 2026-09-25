# Zhonghong VRF for Home Assistant

将 Aqara EigenStone 版使用的中弘 VRF Admin Panel 本地 HTTP 协议接入 Home Assistant。

> [!IMPORTANT]
> 本项目目前是早期可测试版本，仅针对用户提供的 Aqara EigenStone 实机协议结果实现，尚未宣称兼容所有中弘、日立或 Aqara 设备变体。

## 项目目标

项目以 Home Assistant 自定义 Integration 的形式工作，不依赖 Node-RED 或 MQTT。一个中弘网关作为 hub，每台由网关枚举出的室内机对应一个 `climate` 实体。Integration domain 为 `zhong_hong_http`，与 Home Assistant 官方 `zhong_hong` 区分。

当前实现：

- 自动枚举网关下的室内机；
- 显示开关状态、运行模式、目标温度、室内温度和风速；
- 控制开关、制冷/制热/除湿/送风模式、目标温度和风速；
- 通过 Home Assistant UI 完成配置和重新配置；
- 本地异步通信；室内机集中轮询间隔可设置为 5–300 秒；
- 每 5 分钟读取网关设备编号、型号、软件版本及错误码；
- 在 VRF 网关设备下提供室内机状态刷新按钮；
- 可选监听其他 Integration 的 climate 控制状态变化并触发回读；
- 仅在对应室内机数据或可用性实际变化时向 Home Assistant 写入实体状态；
- 动态发现新增室内机，并正确反映设备离线状态。

这里的“自动枚举”是指从已配置网关读取室内机列表。项目尚未确认设备是否提供 mDNS、SSDP 或 DHCP 自动发现能力。

## 设计概览

```text
中弘 VRF Admin Panel
        │ 本地 HTTP
        ▼
异步协议客户端 ──► DataUpdateCoordinator ──► Climate entities
        ▲                                      │
        └──────────── 控制与状态回读 ──────────┘
```

当前实现遵循 Home Assistant Integration 的通用结构：

- Integration 类型：`hub`；
- IoT 类型：`local_polling`；
- 通过 config flow 配置，不提供新的 YAML 配置入口；
- 使用 `ConfigEntry.runtime_data` 保存有类型的运行时对象；
- 分别使用 `DataUpdateCoordinator` 管理室内机状态和低频网关信息；
- 使用 `asyncio` TCP transport 兼容实机的 body-only 非标准 HTTP 响应；
- entity 属性只读取内存状态，不执行网络 I/O；
- 控制操作成功后在 1 秒和 2 秒分别回读，以设备返回状态为准；控制请求之间不按网关串行化。

详细的工程约束、模块边界和测试要求见 [AGENTS.md](AGENTS.md)。

## 协议研究现状

EigenStone 实机已确认三个主要请求：

| 用途 | 请求形式 | 当前理解 |
| --- | --- | --- |
| 网关信息 | `GET /cgi-bin/api.html?f=1` | 返回设备编号、型号、软件版本和两个错误码，每 5 分钟读取 |
| 查询 | `GET /cgi-bin/api.html?f=17&p=<page>` | 分页读取室内机状态，空 `unit` 数组表示结束 |
| 控制 | `GET /cgi-bin/api.html?f=18&on=...&mode=...&tempSet=...&fan=...&idx=...` | 按室内机索引提交完整开关、模式、温度和风速状态 |

已确认的映射：

| 能力 | 设备值 |
| --- | --- |
| 制冷 / 除湿 / 送风 / 制热 | `1` / `2` / `4` / `8` |
| 高 / 中 / 低风 | `1` / `2` / `4` |

实机还确认：成功响应中的 `err` 为数字 `0`；查询每页最多 5 台室内机；响应直接从 JSON body 开始，没有 HTTP 状态行和 headers；控制请求不需要 `FlowDirection1/2`。`alarm` 已验证与滤网无关，本 Integration 完全忽略该字段。

仍需确认：

- 控制失败响应的准确 JSON 结构；
- EigenStone 设备实际支持 `16–32 °C` 还是 `18–30 °C`；
- 模式值 `5`、静音风速和新风机特殊控制的真实含义；
- `oa + ia` 是否在网关重启或重新编组后仍可作为室内机稳定标识；
- 合理轮询周期和网关可承受的请求速率。

当前默认温度范围 `16–32 °C` 被隔离在设备 profile 中，来源是 EigenStone 参考流程而非能力接口或本次实机验证；未来取得直接证据后可在协议边界集中调整。

在上述差异确认前，项目不会宣称兼容所有中弘、日立或 Aqara 设备变体。

## HTTP 兼容策略

实机返回没有状态行和 headers 的 JSON body，严格的标准 HTTP parser 无法接收。正式 Integration 不依赖 `curl`、shell、Node-RED 或同步 `requests`：

1. 使用 `asyncio.open_connection` 发送普通 HTTP/1.1 GET；
2. transport 同时解析实机 body-only 响应和标准 HTTP 响应；
3. transport、协议模型和 Home Assistant entity 保持分层，兼容逻辑不会散落到实体代码中。

## 仓库内容

```text
.
├── AGENTS.md          # 项目范围、协议观察、架构与验收要求
├── COLLABORATION.md   # 开发协作和结构设计原则
├── CONTRIBUTING.md    # 贡献方式和本地检查命令
├── SECURITY.md        # 安全问题报告方式
├── README.md          # 项目介绍与开发状态
├── LICENSE            # GNU GPL v3
├── custom_components/
│   └── zhong_hong_http/ # 可安装的 Home Assistant Integration
├── tools/             # 不随 Integration 安装的协议验证工具
├── tests/             # 协议工具及后续 Integration 测试
└── .gitignore         # Python 工具及本地研究资料忽略规则
```

## 安装

当前版本可从仓库 checkout 手动安装：

1. 将 `custom_components/zhong_hong_http/` 复制到 Home Assistant 配置目录下同名路径；
2. 重启 Home Assistant；
3. 进入“设置 → 设备与服务 → 添加集成”，搜索 **Zhonghong HTTP**；
4. 输入网关 host、端口和 HTTP Basic Auth 信息，选择 5–300 秒的室内机轮询间隔，并按需启用“其他空调变化时刷新”。

默认用户名为 `admin`、密码为空、端口为 `80`、室内机轮询间隔为 10 秒。“其他空调变化时刷新”默认关闭。安装后可在 Integration 的“配置”入口修改这两个行为选项；连接地址或认证信息使用“重新配置”更新。

HA 中会分别创建一个 VRF 网关 device 和每台室内机 device。网关的设备编号、型号及软件版本写入 device registry；`hwerror` 和 `moduleerror` 当前保留在脱敏 diagnostics 中，不额外创建未经定义码表支持的告警实体。

VRF 网关 device 下的“刷新室内机状态”按钮会立即读取全部室内机，但不会强制刷新 `f=1` 网关信息。本次读取结束后，下一次定时轮询从此刻重新计时。自动化也可以对任意一个本 Integration 的 climate 实体调用 `homeassistant.update_entity` 来请求同一 coordinator 刷新；按钮更适合作为不依赖具体室内机的显式自动化动作。按钮用于 HA 自动化调用，不要求 HomeKit 本身支持 button entity。

每次控制请求成功后，Integration 会在 1 秒和 2 秒各安排一次后台状态读取，给网关留出状态落地时间；第二次读取也会把下一次周期轮询顺延。连续控制时，新请求会优先用同一内机最近一次已提交但尚未被回读确认的状态补齐完整控制参数，不会等待其他内机或同网关请求完成。

启用“其他空调变化时刷新”后，Integration 会监听其他 config entry 创建的 `climate` 实体。其开关/HVAC 模式（entity state）或目标温度变化时，会请求一次室内机状态刷新；本 Integration 自己创建的 climate 会按 config entry 排除，以免形成递归。当前温度等非控制属性变化不会触发刷新。

轮询或手动刷新所得的完整网关快照只以室内机数据判定是否变化；当其中一台内机变化时，也只有那台内机的 climate 实体会写入新 state。通信失败和恢复导致的可用性变化仍会正常发布。

### 升级与卸载

- 升级：替换 `custom_components/zhong_hong_http/` 的全部文件并重启 Home Assistant。不要只覆盖部分文件；
- 卸载：先在“设置 → 设备与服务”中删除该 Integration，再删除 `custom_components/zhong_hong_http/` 并重启 Home Assistant；
- 配置条目中包含访问网关所需的认证信息。分享备份、日志或 diagnostics 前，请确认其中没有凭据和家庭网络信息。

本项目仅跟进当前 Home Assistant Integration API，不保证兼容较旧版本。首次安装或升级前建议备份 Home Assistant 配置。

## 协议验证工具

`tools/query_gateway.py` 是开发阶段使用的独立命令行程序，不属于 Home Assistant Integration，未来也不会放入 `custom_components/`。

使用默认用户名 `admin` 和空密码查询：

```bash
python3 tools/query_gateway.py --host 192.0.2.10
```

显式提供认证信息：

```bash
python3 tools/query_gateway.py \
  --host 192.0.2.10 \
  --user admin \
  --password 'your-password' \
  --verbose
```

`--host` 允许主机名、IPv4、带方括号的 IPv6、可选端口或不含路径的 `http://` URL。程序会从 `p=0` 开始分页查询，直到收到空的 `unit` 数组，然后以 JSON 输出所有室内机原始字段。

`--verbose` 会向 stderr 输出不含认证信息的分页诊断，包括每页请求目标、传输类型、响应大小、耗时、`err` 的值和类型、室内机数量及 `(oa, ia, idx, nm)`。`duplicate_unit_keys` 会列出同一 `(oa, ia)` 在页内或跨页重复出现的位置；它只报告重复，不会从最终结果中删除记录。普通输出和 verbose 诊断都可能包含 host、设备地址及室内机名称，公开分享前仍需脱敏。

密码通过命令行传入时可能出现在系统进程列表或 shell 历史中。测试非空密码时，应注意本机环境的访问权限，并在使用后清理相关历史记录。

## 开发路线

- [x] 整理现有 Node-RED 参考实现；
- [x] 明确 Integration 边界和 Home Assistant 架构约束；
- [x] 获取 EigenStone 实机的查询、控制及网关信息样本；
- [x] 实现并测试异步协议 client；
- [x] 实现 config flow、options、coordinator 和 `climate` 平台；
- [x] 实现动态室内机发现、控制后回读和不可用状态；
- [x] 完成基础翻译、diagnostics 和安装文档；
- [ ] 在 Home Assistant 测试环境完成 config entry/entity 生命周期测试；
- [ ] 实机验证温度范围、异常恢复和长期轮询稳定性；
- [ ] 发布首个可安装版本。

## 提供实机资料

欢迎提供 EigenStone 环境的脱敏测试资料。请优先提供：

- `f=18` 失败响应；
- 设备实际支持的温度、模式和风速范围；
- 非零 `hwerror` / `moduleerror` 的码表和行为。

提交前请移除公网地址、认证信息、家庭位置、设备名称等敏感内容。不要在 issue、日志或 fixture 中上传 Authorization header 或密码。

## 开发约定

协议样本必须脱敏并转成最小测试 fixture。新增行为应同时覆盖协议 client 和 Home Assistant 生命周期测试，不依赖真实网关或公网。

开始贡献前请阅读：

- [CONTRIBUTING.md](CONTRIBUTING.md)：贡献流程、本地检查和协议证据要求；
- [SECURITY.md](SECURITY.md)：安全问题及敏感资料的报告方式；
- [AGENTS.md](AGENTS.md)：项目特定规范；
- [COLLABORATION.md](COLLABORATION.md)：通用协作原则；
- [Home Assistant Integration 开发文档](https://developers.home-assistant.io/docs/creating_component_index/)；
- [Home Assistant Climate entity 文档](https://developers.home-assistant.io/docs/core/entity/climate/)；
- [Home Assistant Integration Quality Scale](https://developers.home-assistant.io/docs/core/integration-quality-scale/)。

本地快速检查：

```bash
ruff format --check custom_components tools tests
ruff check custom_components tools tests
pytest -q
```

协议和验证工具的严格类型检查命令见 [CONTRIBUTING.md](CONTRIBUTING.md)。Home Assistant 生命周期测试尚未建立，不能将当前离线测试解释为已经完成真实 HA 安装或所有固件变体验证。

## 免责声明

本项目是社区项目，与 Aqara、绿米、中弘或日立没有隶属或授权关系。使用未公开的本地接口可能受设备型号和固件版本影响，请在了解风险后自行测试。

## 许可证

本项目使用 [GNU General Public License v3.0](LICENSE)。
