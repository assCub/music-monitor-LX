# LX 音源下载后端改造 PRD

> 实施状态（2026-09-20）：核心改造已完成；IKUN 音源已完成 QQ 搜索 → master 取链 → 真实 FLAC 落盘验证，并验证酷我候选会跳过不支持的 master/Atmos 档、在 Hi-Res 首次取链成功。旧引擎链路已删除。

> 可视化配置补充：设置页已覆盖下载后端切换、调度心跳、并发、重试、批次、超时、全局上限、平台优先级、最低音质、搜索数量、匹配阈值、文件名模板和挂载根目录下的子目录；音源支持 URL/文件/文本导入、启停、重载与删除。宿主机根目录及权限仍由 Compose 管理。

## 1. 背景

`music-monitor` 当前由 Python 服务负责订阅和调度，并把搜索、音质探测、下载全部委托给 `go-music-dl`。该引擎的下载接口没有音质参数，当前项目只能通过 `/inspect` 观察码率后择优，无法使用落雪（LX Music）自定义音源的“同档轮询、逐档降级”能力。

本次改造保留现有监控产品能力，新增一个独立的 Node.js LX 网关，用与 `lemon-muisc` 相同的 LX Music 音源脚本运行协议取得音频链接并落盘。monitor 只负责发现、匹配、去重、调度和结果记录。

## 2. 目标

### 必须实现

1. 现有榜单、歌单、收藏夹、歌手订阅功能不退化。
2. 下载时不调用 go-music-dl 的官方音源下载接口，改为调用 LX 网关。
3. 支持 LX Music 自定义音源脚本的 `lx.send('inited')`、`lx.on('request')`、`lx.request` 和常用 `lx.utils` 能力。
4. 对每首歌按以下顺序尝试音质：`master → atmos_plus → atmos → hires → flac24bit → flac → 320k → 128k`。
5. 同一音质档位内轮询所有已加载且声明支持该平台/动作的 LX 音源。
6. 音源失败、链接无效、试听片段或无损格式校验失败时，自动换音源；全部失败后自动降级。
7. 继续使用当前下载目录、文件去重和监控 SQLite；容器重启后任务状态可恢复。
8. 没有 LX 音源脚本时，系统能启动、健康检查和运行发现预览，但下载给出明确错误。

### 暂不实现

- 不在本次改造中复制 Lemon Music 的完整 Web UI、用户体系和音乐库管理。
- 不把各音乐平台的搜索接口误认为 LX 音源；搜索仍用于获取歌曲元数据，音频链接必须来自 LX 音源。
- 不默认绕过平台会员、地区限制或版权限制；实际可用音质取决于已安装的 LX 音源。
- 不在没有真实音源脚本前宣称所有平台和音质都可用。

## 3. 用户流程

```text
订阅榜单/歌单/歌手
        ↓
发现歌曲元数据（歌名、歌手、专辑、时长、平台 ID）
        ↓
按歌名 + 歌手 + 时长匹配 LX 支持平台的候选歌曲
        ↓
最高音质取链
        ↓失败
同档切换下一个 LX 音源
        ↓全部失败
降低一个音质档位并重复
        ↓
下载、试听片段/容器校验、写入共享目录
```

发现平台和 LX 平台解耦。当前平台名映射如下：

| 当前 monitor / go-music-dl | LX / Lemon 平台 key |
|---|---|
| `netease` | `wy` |
| `qq` | `tx` |
| `kugou` | `kg` |
| `kuwo` | `kw` |
| `migu` | `mg` |

如果订阅来源不是上述五个平台，则使用歌名和歌手在 LX 支持的平台中重新搜索匹配。

## 4. 技术方案

### 4.1 组件

- `monitor`：保留现有 FastAPI、调度器、监控数据库和 Web UI。
- `lx-gateway`：新增 Node 20 服务，负责加载 LX 脚本、搜索候选、轮询音源、音质降级、下载校验和写文件。
- 共享音乐目录：`lx-gateway` 可写，`monitor` 只读。

第一阶段仍保留 `music-dl` 服务作为发现/回滚后端；通过 `DOWNLOAD_BACKEND=lx` 切换下载后端。等 LX 网关稳定后再评估移除 `music-dl`。

### 4.2 LX 网关 API

- `GET /healthz`：健康状态、已加载音源和音质能力。
- `GET /api/sources`：列出已加载音源的声明能力。
- `POST /api/sources/reload`：从 `LX_SOURCES_DIR` 重新加载脚本。
- `POST /api/search`：按平台和关键词返回歌曲候选。
- `POST /api/download`：传入歌曲元数据、目标音质和降级策略，下载并返回实际音质、音源和文件路径。

下载 API 一次处理一首歌，避免 monitor 与两个队列同时调度。网关内部每个音质档最多依次尝试所有可用 LX 音源。

### 4.3 质量策略

- `preferred_quality`：默认 `master`，兼容旧配置的 `standard/high/lossless/hires` 映射为 `128k/320k/flac/hires`。
- `quality_floor`：可选最低档位；为空表示允许降到 `128k`。
- `cascade=true`：失败后自动降级；监控自动下载固定为 true。
- `qualitys` 声明用于缩短尝试列表，但声明不完整时仍按标准阶梯兜底。

## 5. 数据和错误处理

网关返回：

```json
{
  "ok": true,
  "path": "data/downloads/Artist - Title.flac",
  "quality": "flac",
  "source": "wy",
  "source_id": "lx-source-1",
  "attempts": [
    {"quality": "master", "source_id": "lx-source-1", "ok": false, "error": "..."},
    {"quality": "flac", "source_id": "lx-source-1", "ok": true}
  ]
}
```

失败必须区分：没有激活音源、没有匹配歌曲、所有音质失败、试听片段、假无损、网络错误和目标文件已存在。monitor 记录最终质量、LX 音源 ID、实际路径和失败原因。

## 6. 安全和部署

- LX 脚本只从挂载的 `LX_SOURCES_DIR` 加载，不接受未认证的远程上传。
- 网关只绑定 Compose 内网，monitor 通过服务名访问；如需暴露，必须加共享 token。
- 脚本运行采用 Lemon Music 的受限 VM：白名单 `require`、超时、受限全局对象。
- `lx-gateway` 不直接访问 monitor SQLite，避免两个服务并发写同一数据库。

Compose 新增：

```yaml
lx-gateway:
  build: ./lx-gateway
  volumes:
    - ./config/lx:/config
    - ./data/downloads:/downloads
    - ./config/lx-sources:/sources
```

## 7. 分阶段实施

### Phase 1：契约和骨架

- 新增本 PRD。
- 新增 LX 质量阶梯、平台映射、候选匹配和网关 HTTP 客户端。
- 新增 lx-gateway 健康检查、音源加载和下载 API。
- LX 网关成为唯一下载后端。

### Phase 2：主流程接线

- monitor 统一调用 LX 网关，不再存在旧下载分支。
- 候选歌曲转换成 LX `musicInfo`，保留 QQ songmid、酷狗 hash、酷我 rid、咪咕 copyrightId 等字段。
- 记录 LX 返回的最终质量和音源信息。

### Phase 3：真实音源验证

- 使用用户提供的 LX 音源脚本导入 `config/lx-sources`。
- 验证脚本初始化、各平台取链、最高音质、自动降级、试听片段过滤、假 FLAC 过滤和重启恢复。
- 根据真实脚本修正字段映射和请求头，不修改 LX 协议兼容层的基本行为。

### Phase 4：切换和清理

- 默认切换到 `DOWNLOAD_BACKEND=lx`。
- 文档补充音源安装和质量策略说明。
- 连续稳定运行后再决定是否删除 `music-dl` 服务和旧 Engine 下载代码。

## 8. 验收标准

1. 无 LX 脚本时，`docker compose up`、monitor 健康检查和发现预览均成功。
2. 有测试音源时，可由 monitor 触发一首歌下载到共享目录。
3. 最高档失败时，日志能看到同档换源和下一档降级。
4. 试听片段不会写入最终音乐目录。
5. 伪 FLAC 不会以 FLAC 文件保存。
6. 已下载歌曲不会因平台 ID 变化重复下载。
7. 音源服务重启后已落盘文件仍能被 monitor 正确识别。
8. `DOWNLOAD_BACKEND=go` 仍能走旧链路，便于回滚。
