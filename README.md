# MTA DATA TEST — Stay / Switch 初步数据探索

每 30 秒采集 MTA 列车与官方服务通知，默认运行 5 分钟。新采集数据保存在本机 `data/`，不会自动提交；已整理发布的数据位于 `datasets/`。

## 已发布数据

- [11 月 12 日 6:30–9:30AM](datasets/2026-11-12_0630-0930_America-New_York/README.md)：430,920 条预测记录，720 次请求全部成功。
- [11 月 12 日 12–3PM](datasets/2026-11-12_1200-1500_America-New_York/README.md)：372,293 条预测记录，720 次请求全部成功。
- [三时段 ETA 重算结果及不同计算口径](datasets/recalculations/README.md)。

- [2026 年 11 月 11 日 18:00–21:00，纽约时间](datasets/2026-11-11_1800-2100_America-New_York/README.md)：720 次成功请求、完整原始响应与 SQLite 压缩包，以及可直接查看的 CSV。
- [本批数据分析](datasets/2026-11-11_1800-2100_America-New_York/analysis/README.md)：采集质量、ETA 偏差估计、线路和大站比较、官方事件、候选训练数据及质量复核。

原始数据不作覆盖修改；派生分析可用 `scripts/analyze_collection.py` 重算。候选到站时间仍是 API 推断，尚未经过现场核验。

## 研究范围

- B / D / F / M，南北两个方向。实时接口中的 `FX` 作为 F express 变体另外保留；`FS` 等其他线路不纳入分析。
- 曼哈顿研究区域，北端包含 **59 St–Columbus Circle (A24)**、**Lexington Av/63 St (B08)**、**Roosevelt Island (B06)**。
- 每次启动读取 MTA 官方车站清单，以曼哈顿行政区、Columbus Circle 纬度上限以及实时线路共同筛选。正常 BDFM 服务下对应上述边界；发生特殊改道时，请查看 `scope.json` 和实际观测站点，确认是否仍属于研究范围。
- 不假定 F/M 的固定走向。当前官方车站清单将 B08/B06 列为 M 服务站点。
- 原始列车响应保留整个 feed，结构化列车表只保留范围内观测。服务通知保留涉及 BDFM 的全线通知，因为曼哈顿外的故障也可能影响区内列车。
- 不做计划时刻表对照。车站元数据用于识别范围、站名和方向，不是时刻表比较。

## 安装和运行

需要 Python 3.9+ 和网络连接；两个公开实时接口无需在代码里填写 API key。

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
python -m unittest -v
python mta_explore.py collect --minutes 5 --interval 30
```

测试 10 分钟改为 `--minutes 10`。运行 24 小时可用 `--minutes 1440`；电脑需要保持开机、联网、不休眠。每次运行创建独立目录，不覆盖已有实验。Ctrl-C 会保留数据并生成报告。

重算已有数据，不访问网络：

```bash
python mta_explore.py analyze data/你的运行目录
```

## 本地输出

| 文件 | 内容 |
|---|---|
| `raw/*.pb.gz` / `*.json.gz` | 每次请求的原始响应；可重新解析 NYCT/Mercury 扩展字段 |
| `observations.sqlite` | 请求日志、逐车逐站预测、车辆状态、服务通知；每次请求单独提交 |
| `manifest.json` / `stations_source.json` / `scope.json` | 采集配置、原始车站清单、地理范围映射 |
| `eta_changes.csv` | 每个车次×方向站点的预测变化范围；不是实际延误 |
| `observed_stations.csv` | 实际采到的线路、方向站点、站名、不同车次和记录数量，便于核对边界 |
| `arrival_candidates.csv` | 车站编号、时间戳及剩余站点一致的候选到站记录、估计时间及观测区间 |
| `arrival_evidence.csv` | 每次车站编号变化的前后列表、时间戳及采用/拒绝原因 |
| `station_summary.csv` | 候选记录的 ETA 偏差估计、已检测到站事件间隔；不是核验后的真实班次 |
| `alerts.csv` | 每次观察到的官方延误、改道及其他相关通知，包括原文、线路、可选车站、生效时间、创建和更新时间 |
| `summary.json` / `report.md` | 本次数据概况、源时间新鲜度和解释限制 |

所有时间戳是 UTC Unix 秒；报告中的 ISO 时间也为 UTC。纽约本地时间可按 `America/New_York` 转换。SQLite 无需另外安装数据库服务，CSV 可用表格软件打开。

## 数据结构与解释

列车通过 `start_date + route_id + trip_id + start_time` 跟踪，不使用会变化的 `entity.id` 作为跨快照身份。车站保留 `N` / `S` 后缀区分方向。身份变化或重复站点的复杂改道可能导致保守漏计。

ETA 来自 `stop_time_update.arrival.time`。没有填充的数值型 `delay` 不等于零延误。车辆状态来自 `vehicle.current_status` / `stop_id` / `timestamp`；按 GTFS 规则，缺省状态在提供 stop sequence 时解释为 `IN_TRANSIT_TO`，且另存是否显式提供状态。

v2 候选到站要求（三项信号共同支持）：

1. 同一车次的车站编号从 A 变为此前停靠列表中的下一站 B，新记录明确标为 `STOPPED_AT`。不再要求先看到 `IN_TRANSIT_TO`。A 已从旧列表移除时，B 必须是旧列表第一站。
2. 两个源时间递增且间隔不超过 90 秒；重复或回退的源时间不重复计数。
3. feed 相对下载完成时间的年龄在 -5 至 120 秒以内；两条车辆状态时间均不得晚于各自源时间，且年龄不超过 120 秒。新车辆时间戳还必须晚于上一次源时间。
4. 新剩余列表必须是旧列表的后缀，以 B 开头且不再含 A；B 不能被明确标为跳站。方向反转、跨越中间站、列表重排、插入新站、重复站点或缺少列表均不采用。
5. 第一次就看到停站的列车不算新到站；同一车次同一站不重复计数。研究区域边界外的车辆位置未纳入结构化表，因此首次进入区域也可能被保守漏计。

该规则的候选时间使用**首次满足条件的新站车辆时间戳**，不是官方确认的精确到站时间。它有可能代表站点位置更新，仍需现场核验。原有同站行进→停站转换作为另一种候选方式保留，使用观测中点；`method` 字段区分两者。ETA 偏差与间隔统一使用 `estimated_event_ts`，不使用旧的 `midpoint` 字段。

预测偏差使用事件观测区间之前显示约 4–6 分钟到站的 ETA；没有这种参考就留空。正值表示估计到达晚于该预测，负值表示更早。漏掉列车会放大相邻候选事件间隔，因此不将其自动解释为实际发车间隔，也不输出“实际每小时班次”。

`trip_paths` 表保留 BDFM 完整、有序的剩余站点（含研究范围外站点），防止地理筛选隐藏中间站。旧数据运行 `analyze` 时，会从已有原始快照补充这一表，无需重新下载。所有原始数据保留；`arrival_evidence.csv` 提供车站变化的判定证据。陈旧 feed 会中断跟踪，无法判断的观测会被保守排除。

服务通知包括 `alert_type`、`created_at`、`updated_at`、`active_period`、`informed_entity` 和通知原文。`lmm:alert` ID 尾部的状态等级可能变化，因此另存稳定的 `event_key`。一个事件的多次快照不是多个独立事件。计划改道和未来施工保留，`active_at_source` 标明其在**该份通知源时间**是否生效，`active_at_collection` 按采集时刻判断公布的生效区间；不能把所有通知都当作当前故障。公告源时间不更新，可能是发布端没有新通知，也可能是旧数据，不能仅据此断定实际信息过期。

**本轮只能探索 ETA 修订、官方通知，以及可核验的到站代理信号。** 5–10 分钟不足以估计代表性延误、训练算法或证明换线省时。正式计算逐车/逐站平均偏差和真实频次前，应在少量车站记录实际到站，检验状态推断准确性。官方通知的时间是发布/更新信息，不是故障开始/结束的地面真值。

## 数据源

- [MTA developer resources](https://www.mta.info/developers)
- [BDFM GTFS-RT protobuf](https://api-endpoint.mta.info/Dataservice/mtagtfsfeeds/nyct%2Fgtfs-bdfm)
- [Subway service alerts JSON](https://api-endpoint.mta.info/Dataservice/mtagtfsfeeds/camsys%2Fsubway-alerts.json)
- [MTA Subway Stations](https://data.ny.gov/Transportation/MTA-Subway-Stations/39hk-dx4f/about_data)
- [GTFS vehicle state definitions](https://gtfs.org/documentation/realtime/reference/#message-vehicleposition)
- [MTA alert status IDs](https://github.com/nymta/gtfs-documentation/blob/main/feeds/service_changes.md)

接口失败会记录错误并继续下一个固定采样时点，不进行密集重试。两个 feed 并行读取，但**下载时间相近不保证源时间一致**。每次记录源时间、内容摘要、HTTP 响应头和完整原始数据，用于识别缓存和陈旧数据。
