# Viewer 文档目录

本文档集仅描述当前仓库代码实现。入口按使用角色组织：

## 使用与部署

1. [安装与启动](getting-started.md) — Windows 一键启动、macOS/Linux 手动启动、首次登录。
2. [配置参考](configuration.md) — `VIEWER_` 环境变量、数据库、并发、定时任务和安全配置。
3. [构建、测试与运维](operations.md) — 构建发布、健康检查、备份、升级和故障排查。

## 架构与开发

1. [系统架构](architecture.md) — 前后端边界、请求流、组件生命周期和数据层。
2. [插件开发](plugins.md) — 插件声明、依赖、API、规则引擎、ZIP 安装和插件 UI。
3. [API、认证与权限](api-and-permissions.md) — 接口分组、Bearer Token、权限模型和调试方式。

## 内容与协议

1. [书源、订阅源与规则引擎](sources-and-engines.md) — 三类来源、Legado 引擎接口和导入行为。
2. [协议桥与 Legado 协议](protocols.md) — 两个独立插件、系统协议注册、链接格式和扩展方式。

## 建议阅读路径

- 第一次部署：安装与启动 → 配置参考 → 运维。
- 开发业务功能：系统架构 → API 与权限。
- 开发插件：系统架构 → 插件开发。
- 排查书源或协议：来源与引擎 → 协议文档 → API 文档。
