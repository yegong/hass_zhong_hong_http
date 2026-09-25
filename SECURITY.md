# 安全策略

## 报告安全问题

请不要使用公开 GitHub issue 报告可能泄露凭据、设备标识、家庭网络信息或能够未经授权控制设备的问题。

优先使用 GitHub 的私密漏洞报告入口：

<https://github.com/yegong/hass_zhong_hong_http/security/advisories/new>

报告中请提供足以复现问题的最小信息，但不要提交真实密码、完整 Authorization header、未脱敏 diagnostics 或包含家庭位置/房间名称的原始响应。如果私密漏洞报告功能不可用，请先通过仓库维护者公开资料联系并约定安全的传输方式，不要直接公开敏感附件。

## 支持范围

安全修复以当前仓库版本为目标。尚未发布的旧提交、非官方分支、修改后的固件及未经确认的中弘协议变体不承诺安全更新。

本 Integration 仅连接用户配置的本地 HTTP 网关，不提供 TLS，也不能改善设备固件本身的认证或网络安全。建议将网关置于可信局域网，限制不必要的跨网段访问，并为 Home Assistant 主机和备份设置适当的访问控制。

## 敏感数据

以下内容应视为敏感数据：

- 网关用户名和密码；
- HTTP Authorization header；
- 网关 IP/hostname 和设备编号；
- 房间及室内机名称；
- 未经脱敏的 diagnostics、抓包或完整原始响应。

Integration diagnostics 会脱敏配置中的 host、用户名、密码、设备编号和室内机名称，但提交资料前仍应由报告者再次检查。
