# 贡献指南

感谢你改进 Zhonghong HTTP。本项目逆向接入未公开的本地协议，因此可复现的设备证据和清晰的兼容边界比推测性的通用化更重要。

## 开始之前

- 阅读 [AGENTS.md](AGENTS.md) 中的协议事实、模块边界和待验证清单；
- 不要提交密码、Authorization header、家庭网络地址、房间名称或未脱敏的设备编号；
- `references/` 是本地只读研究资料，不属于公开仓库内容；
- 新协议行为应说明是实机确认、参考实现观察还是待验证假设；
- 不要把 Node-RED、`curl`、同步 HTTP 客户端或 shell 命令加入 Integration 运行路径。

## 提交问题

功能或兼容性问题请使用 GitHub Issues。报告设备差异时，尽量包含：

- Home Assistant 版本和 Integration 版本；
- 网关型号代码和软件版本；
- 预期行为与实际行为；
- 已脱敏的最小响应样本；
- 问题是否能由 `tools/query_gateway.py` 重现。

安全问题不要提交公开 issue，请按 [SECURITY.md](SECURITY.md) 处理。

## 修改代码

- 每个变更围绕一个明确目标，避免混入无关重构；
- 协议兼容放在 `transport.py`、`client.py`、`models.py` 或 `profile.py`，不要散落到 entity 属性；
- `alarm` 及当前明确忽略的集中管理字段不得重新暴露，除非有新的实机证据和明确需求；
- 新行为应有不依赖真实网关或公网的测试；
- 不修改或提交 `references/`、本地脚本、缓存和凭据。

## 本地检查

开发检查使用 Ruff、mypy、pytest 和 Coverage；它们都是开发工具，不是 Integration 运行依赖。

```bash
ruff format --check custom_components tools tests
ruff check custom_components tools tests

mypy --strict --ignore-missing-imports --follow-imports=skip \
  custom_components/zhong_hong_http/const.py \
  custom_components/zhong_hong_http/profile.py \
  custom_components/zhong_hong_http/models.py \
  custom_components/zhong_hong_http/transport.py \
  custom_components/zhong_hong_http/client.py \
  tools tests

pytest -q
coverage run --branch -m pytest -q
coverage report -m
git diff --check
```

上述 mypy 命令覆盖不依赖 Home Assistant 安装的协议层、验证工具和测试。涉及 config flow、coordinator、entity、device registry 或 unload/reload 的改动，还应在 Home Assistant 官方测试环境中增加并运行相应生命周期测试；若本地环境不具备该条件，应在变更说明中明确写为“未运行”。

## 提交说明

提交或 pull request 应说明：

- 修改了什么以及原因；
- 使用了哪些已确认协议证据；
- 运行了哪些检查；
- 哪些行为仍未验证；
- 是否影响 entity `unique_id`、device identifiers 或配置迁移。

提交代码即表示你同意按仓库的 [GNU GPL v3](LICENSE) 许可证发布该贡献。
