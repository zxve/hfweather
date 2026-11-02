# Hfweather — 和风天气 Home Assistant 集成

独立集成域为 `hfweather`，仓库为 `zxve/hfweather`，不是其他 `heweather` 项目的更新。
最低 HA 版本：2026.10.0。本次在 HA 2026.10.4 / Python 3.14 上验证。

## 安装与配置

将本仓库作为 HACS 自定义 Integration 仓库添加，或安装 `custom_components/hfweather` 到同名目录。
重启 HA 后添加 Hfweather，填写 API Key、和风 API 主机名与坐标。
主机名不能含协议、路径或端口。现有 `devapi.qweather.com` v7 配置保留兼容；新账号使用控制台的专属 API Host。
目前认证实现为 API Key，不声称已经支持 JWT。

## 从本项目旧 heweather 域迁移

不要删除原天气集成或实体，也不要先安装别人的 heweather 项目。
添加 Hfweather 时选择“迁移旧版配置（保留实体）”，选择原天气集成。
内部也支持显式 `import` 配置流，输入为旧条目的 `legacy_entry_id`。这个迁移入口不接受或打印密钥，
不会自动扫描接管其他插件。执行前备份原组件、配置条目、实体注册表和设备注册表。

先验证新实现可获取天气，再卸载旧集成，通过 HA 的 `async_update_entity_platform` 和设备注册表 API 转移所有权。
保留 `entity_id`、`unique_id`、设备 ID、禁用状态和用户名称；原实体 ID 不变，已有 Recorder 历史仍归属原实体。
新平台加载成功后才移除旧配置条目。遇到实体或设备冲突则停止；注册表部分修改失败时回滚。
不直接编辑运行中的 `.storage` 文件。

迁移后 HACS 应管理 `zxve/hfweather`；本插件不会静默修改其他仓库的 HACS 记录。

## 数据和位置策略

- 每配置一个协调器，每轮实时天气只请求一次，sensor/weather 共用。
- 空气、预警、逐日、逐小时、生活指数分别记录成功状态。
  可选接口失败不拖垮实时天气；失败产品不可用，不显示为零或“没有预警”。
- 可选产品近期缓存仅用于诊断，不冒充成功数据。
- 使用 API 的 `fxTime` / `fxDate`，未知天气类型保留该时刻，不挪动后续预报。
- 数值保持原生公制，由 HA 按正确设备类转换展示单位。`windDir` 是文本，weather 风向使用 `wind360`。
  CO 使用 API 的 mg/m³，其余对应污染物为 µg/m³。
- 保留 `place` 的提供方链接位置标识（slug），不冒充手机解析地址。
- 新配置默认 30 分钟刷新，范围 10–1440 分钟。迁移保留显式旧值，包括 720 分钟。
- 位置模式：`fixed` 固定坐标；`home` HA 位置；`entity` 定位实体。
  推荐带 latitude/longitude 的 device_tracker，也兼容 geocoded_location 的 location 数组。
  位置失效不静默切换到其他城市。移动 5km 才提前刷新，最短间隔 10 分钟并合并短时变化。
  旧配置迁移保持 fixed，避免失效的地址传感器影响天气。

## 故障和隐私

请求有超时和限流冷却，不跟随重定向，仅向和风域名发送凭据，凭据通过请求头传输。
日志不输出密钥、完整请求 URL 或不可信错误响应。诊断不导出凭据、经纬度或个人定位实体名。
实体带成功更新时间，实时数据带源观测时间。实时更新失败依照 HA 协调器状态不可用。
认证 401 可重新认证；403 等产品访问错误不会简单解释为所有凭据失效。
预警状态限制 255 字符，完整文本（有上限）放在 `warning_text` 属性。

手机地址 geocoded_location 由 Companion App 上报，本插件不负责产生该地址。

## 测试

纯解析测试无需 HA：

```sh
python -m unittest discover -s tests -p test_models.py -v
```

完整测试需匹配 HA/Python 环境，只使用临时配置和模拟网络，不读生产配置、不访问外网：

```sh
python -m unittest discover -s tests -v
```

CI 已配置单元测试、关键语法检查、hassfest 和 HACS 校验。
配置 workflow 不等于已在 GitHub 运行通过，发布前需确认实际结果。
不声明已取得 HA Integration Quality Scale 等级。

## 1.1.0 当前环境验收（2026-11-03）

- 手机原生 HA 2026.10.4 / Python 3.14 上，42 项隔离回归测试全部通过。
- Ruff 的语法、导入和未定义符号检查通过。
- 原生配置流已将旧条目迁移为 `hfweather`；25 个注册实体的实体 ID、唯一 ID、设备 ID、
  名称及禁用状态均与迁移前一致，24 个启用实体全部可用。
- 主动刷新返回成功，实体的成功更新时间推进；原生 `weather.get_forecasts` 返回 7 天和 24 小时，
  首条时间使用提供方日期/时区，不再由查询时间生成。
- 重启后自动加载成功，保留属性兼容警告已清除；剩余是 HA 对自定义集成的通用提示。
- 三路正式录像均恢复 recording，进程各一个，并已写入新文件。
- 主备份在手机 `/config/migration-backups/hfweather-independent-20261102T182033Z`；
  保留属性修正备份为 `hfweather-group-compat-20261102T182436Z`。
- 现有位置模式与显式刷新间隔未静默改变。手机地址上报在验收时已恢复，但不是本插件负责修复。

此记录是本地代码和当前 HA 的验收，不代表已推送 GitHub、发布 release 或运行远端 CI。
远端发布及 HACS 来源整理应在确认分支后单独进行，不能再把第三方 heweather 当作本项目更新。
