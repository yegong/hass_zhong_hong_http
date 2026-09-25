# Zhonghong VRF for Home Assistant

将 Aqara EigenStone 版使用的中弘 VRF Admin Panel 本地 HTTP 协议接入 Home Assistant。

> [!IMPORTANT]
> 本项目目前处于协议验证和 Integration 设计阶段，尚未提供可安装版本。当前结论来自本地 Node-RED 参考流程，不代表已经验证的厂商协议。

## 项目目标

项目计划以 Home Assistant 自定义 Integration 的形式工作，不依赖 Node-RED 或 MQTT。一个中弘网关作为 hub，每台由网关枚举出的室内机对应一个 `climate` 实体。

计划支持：

- 自动枚举网关下的室内机；
- 显示开关状态、运行模式、目标温度、室内温度和风速；
- 控制开关、制冷/制热/除湿/送风模式、目标温度和风速；
- 通过 Home Assistant UI 完成配置和重新配置；
- 本地异步通信与集中轮询；
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

实现将遵循 Home Assistant 当前的 Integration 规范：

- Integration 类型：`hub`；
- IoT 类型：`local_polling`；
- 通过 config flow 配置，不提供新的 YAML 配置入口；
- 使用 `ConfigEntry.runtime_data` 保存有类型的运行时对象；
- 使用 `DataUpdateCoordinator` 对网关进行集中轮询；
- 首选 Home Assistant 提供的共享 `aiohttp.ClientSession`；
- entity 属性只读取内存状态，不执行网络 I/O；
- 控制操作完成后主动回读，以设备返回状态为准。

详细的工程约束、模块边界和测试要求见 [AGENTS.md](AGENTS.md)。

## 协议研究现状

参考流程中观察到两个主要请求：

| 用途 | 请求形式 | 当前理解 |
| --- | --- | --- |
| 查询 | `GET /cgi-bin/api.html?f=17&p=<page>` | 分页读取室内机状态，空 `unit` 数组表示结束 |
| 控制 | `GET /cgi-bin/api.html?f=18&...` | 按室内机索引提交开关、模式、温度和风速 |

目前较一致的候选映射：

| 能力 | 设备值 |
| --- | --- |
| 制冷 / 除湿 / 送风 / 制热 | `1` / `2` / `4` / `8` |
| 自动 / 高 / 中 / 低风 | `0` / `1` / `2` / `4` |
| 静音风 | `6`，并非所有参考流程均支持 |

仍需实机确认：

- 设备响应是否为非标准 HTTP/0.9 风格；
- 成功和失败响应的准确 JSON 结构；
- 控制请求是否必须始终提交完整状态；
- EigenStone 设备实际支持 `16–32 °C` 还是 `18–30 °C`；
- 模式值 `5`、静音风速和新风机特殊控制的真实含义；
- 可用于稳定标识网关及室内机的字段；
- 合理轮询周期和网关可承受的请求速率。

在上述差异确认前，项目不会宣称兼容所有中弘、日立或 Aqara 设备变体。

## HTTP 兼容策略

参考资料中的较新流程使用了 HTTP Basic Auth 和 `curl --http0.9`。正式 Integration 不会依赖 `curl`、shell、Node-RED 或同步 `requests`：

1. 优先使用 Home Assistant 共享的 `aiohttp` session；
2. 只有实机响应或测试 fixture 证明标准客户端无法解析响应时，才在独立 transport 层增加最小的异步兼容实现；
3. transport、协议模型和 Home Assistant entity 保持分层，设备兼容逻辑不会散落到实体代码中。

## 仓库内容

```text
.
├── AGENTS.md          # 项目范围、协议观察、架构与验收要求
├── COLLABORATION.md   # 开发协作和结构设计原则
├── README.md          # 项目介绍与开发状态
├── LICENSE            # GNU GPL v3
└── .gitignore         # Python 工具及本地研究资料忽略规则
```

计划中的 Integration domain 为 `zhong_hong`，代码将放在 `custom_components/zhong_hong/`。

## 安装

目前没有可安装版本。本项目的本地参考流程也不是 Home Assistant Integration，不能作为集成安装。

首个可用版本完成后，本节将提供手动安装、UI 配置、升级和卸载步骤；如果项目加入 HACS，也会在此补充对应安装方式。

## 开发路线

- [x] 整理现有 Node-RED 参考实现；
- [x] 明确 Integration 边界和 Home Assistant 架构约束；
- [ ] 获取 EigenStone 实机的脱敏查询与控制样本；
- [ ] 实现并测试异步协议 client；
- [ ] 实现 config flow、coordinator 和 `climate` 平台；
- [ ] 验证动态室内机发现、控制回读和不可用状态；
- [ ] 完成翻译、diagnostics、文档与安装验证；
- [ ] 发布首个可安装版本。

## 提供实机资料

欢迎提供 EigenStone 环境的脱敏测试资料。请优先提供：

- `f=17&p=0` 和最后一页的原始响应；
- HTTP 状态行与响应 headers；
- `f=18` 成功及失败响应；
- 设备实际支持的温度、模式和风速范围；
- 型号、固件版本及对应行为。

提交前请移除公网地址、认证信息、家庭位置、设备名称等敏感内容。不要在 issue、日志或 fixture 中上传 Authorization header 或密码。

## 开发约定

协议样本必须脱敏并转成最小测试 fixture。新增行为应同时覆盖协议 client 和 Home Assistant 生命周期测试，不依赖真实网关或公网。

开始贡献前请阅读：

- [AGENTS.md](AGENTS.md)：项目特定规范；
- [COLLABORATION.md](COLLABORATION.md)：通用协作原则；
- [Home Assistant Integration 开发文档](https://developers.home-assistant.io/docs/creating_component_index/)；
- [Home Assistant Climate entity 文档](https://developers.home-assistant.io/docs/core/entity/climate/)；
- [Home Assistant Integration Quality Scale](https://developers.home-assistant.io/docs/core/integration-quality-scale/)。

## 免责声明

本项目是社区项目，与 Aqara、绿米、中弘或日立没有隶属或授权关系。使用未公开的本地接口可能受设备型号和固件版本影响，请在了解风险后自行测试。

## 许可证

本项目使用 [GNU General Public License v3.0](LICENSE)。
