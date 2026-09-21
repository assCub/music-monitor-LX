# music-monitor

多用户音乐订阅与 LX 音源自动下载服务。

> 本项目基于 [Baey666/music-monitor](https://github.com/Baey666/music-monitor) 二次开发，保留原项目的 Git 提交历史、版权声明和 MIT License。当前分支重构了下载后端、平台登录、多用户隔离与可视化配置；详细说明见 [NOTICE](NOTICE)。

## 默认架构

```text
music-monitor (9090)
  ├── 用户、Session、权限
  ├── 多平台发现与个人歌单适配
  ├── 榜单、歌单、歌手和收藏夹订阅
  └── 调度、过滤、记录
       ├── platform-auth：网易云/QQ/酷狗/哔哩哔哩/汽水扫码协议
       └── lx-gateway：LX 音源、取链、降档、下载、进度
```

平台发现、扫码和下载均由本项目自身服务完成，不写入任何全局平台 Cookie。

## 宿主机路径

```env
CONFIG_DIR=./config
MUSIC_DIR=./data/downloads
```

```text
CONFIG_DIR/
├── monitor/              SQLite、Session、加密主密钥
├── users/<uuid>/         用户独立 credentials.enc
├── lx/                   LX 网关状态和下载历史
└── lx-sources/           管理员安装的 LX 音源

MUSIC_DIR/users/<uuid>/<歌手>/
```

宿主机根目录和权限只通过 Compose 授权；网页只能选择音乐根目录下的相对子目录。

## 启动

```bash
cp .env.example .env
mkdir -p config data/downloads
chmod -R 777 config data
docker compose up -d --build
docker compose ps
```

打开 `http://NAS-IP:9090`，首次访问创建管理员。管理员可在“设置 → 用户与权限”创建、停用和重置用户。

预构建的 `amd64/arm64` 镜像统一发布到
[Docker Hub：yua0712/music-monitor-lx](https://hub.docker.com/r/yua0712/music-monitor-lx)，主服务、LX 网关和扫码服务使用不同标签；Compose 已配置对应默认地址。

NAS 只拉镜像部署可直接使用 [docker-compose.nas.yml](docker-compose.nas.yml) 和
[.env.nas.example](.env.nas.example)，无需克隆源码或在 NAS 上构建。

## 平台登录

每位用户在“设置 → 我的平台账号”中独立登录：

- 网易云音乐、QQ 音乐（QQ/微信）、酷狗音乐、哔哩哔哩、汽水音乐扫码；
- 支持扫码的平台各自显示独立标签；
- 酷我、咪咕等没有扫码协议的平台在“其他平台”中用 Cookie JSON 配置；
- “其他平台”保存成功后会自动追加独立平台标签。

扫码由独立 `platform-auth` 调用平台协议，成功后 Cookie 立即加密写入：

```text
CONFIG_DIR/users/<user-uuid>/credentials.enc
```

Cookie 不写入共享引擎。生产建议设置 `CREDENTIALS_MASTER_KEY`；留空时会在 `CONFIG_DIR/monitor/.credentials.key` 自动生成。

## 订阅和过滤

- QQ、酷狗、酷我排行榜；
- 网易云官方榜单；
- QQ、网易云公开歌单；
- 用户自己的 QQ、网易云、酷狗、汽水歌单；
- 歌手关注。

歌手关注会自动分页，默认最多 100 首，可配置到 1000（例如 600）。同时过滤歌手不符、Live、Demo、Remix、DJ、翻唱、伴奏、试听短片段和跨平台重复曲目，并在日志中显示过滤统计。

## LX 下载

音质阶梯：

```text
master → atmos_plus → atmos → hires → flac24bit → flac → 320k → 128k
```

同档轮询 LX 音源，失败后逐档降级；试听片段和伪无损不会写入音乐库。文件默认保存为：

```text
MUSIC_DIR/users/<user-uuid>/<歌手>/<歌手> - <歌名>.<ext>
```

“下载记录 → LX 实时下载”显示取链、音质、平台、音源、字节进度、校验、降档和最终路径。

## 安全与权限

- 密码使用 scrypt 哈希；
- Session 数据库只保存 SHA-256 token；
- 平台 Cookie 使用 Fernet 加密；
- 普通用户只能访问自己的监控、记录、下载和凭据；
- 管理员才能管理用户和 LX 音源；
- LX 网关和扫码服务只在 Compose 内网开放。

## 测试

```bash
cd lx-gateway
npm test

cd ../monitor
python -m unittest tests.test_lx_gateway tests.test_pipeline_lx tests.test_multi_user
```

设计文档：

- [LX 下载后端 PRD](docs/prd-lx-download-backend.md)
- [多用户与独立平台账号 PRD](docs/prd-multi-user.md)

## 来源与许可证

- 上游项目：[Baey666/music-monitor](https://github.com/Baey666/music-monitor)
- 当前派生仓库：[assCub/music-monitor-LX](https://github.com/assCub/music-monitor-LX)
- 许可证：[MIT License](LICENSE)

感谢原作者 Baey666 及相关依赖项目的工作。本项目的修改不代表原作者对派生版本提供维护或背书。
