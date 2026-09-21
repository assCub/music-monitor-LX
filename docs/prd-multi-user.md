# 多用户与独立平台账号 PRD（下一阶段）

## 目标

让家庭成员或团队用户各自登录 music-monitor，并拥有互相隔离的：

- 平台登录凭据与 Cookie；
- 个人收藏夹、歌单和歌手订阅；
- 监控任务、运行记录和失败重试；
- 可选的个人音乐目录；
- LX 音源启用范围和下载策略。

## 实施状态（2026-09-20）

已完成：

- 首次管理员创建、登录/退出、30 天 HttpOnly Session；
- 管理员创建、停用用户与重置密码；
- monitors/tracks/runs 和 LX 下载列表按用户隔离；
- Cookie 使用 Fernet 加密保存到 `CONFIG_DIR/users/<uuid>/credentials.enc`；
- QQ、网易云公开歌单和个人收藏夹直连发现；
- 下载写入 `MUSIC_DIR/users/<uuid>/<歌手>/`；
- 旧数据首次初始化时迁移给管理员；
- 已删除旧全局引擎链路，默认架构只包含 monitor、platform-auth、lx-gateway。

后续扩展：酷狗/酷我/咪咕个人收藏夹、扫码登录、磁盘配额和审计日志。

## 历史限制与重构原则

当前 monitor 没有用户登录，所有监控共用一个 SQLite；go-music-dl 也只有一份全局 Cookie。仅把 Cookie 文件移动到 `config/users/<id>` 并不能隔离请求，因为调用 go-music-dl 时仍会使用它当前加载的全局 Cookie，用户之间会串号。

因此多用户必须同时完成“权限隔离”和“平台请求凭据隔离”，不能只改目录结构。

## 配置目录

宿主机仍只配置 `CONFIG_DIR` 和 `MUSIC_DIR`：

```text
CONFIG_DIR/
├── auth/
│   └── users.db                 # 用户、密码哈希、Session、角色
├── shared/
│   └── lx-sources/              # 管理员安装、可授权给用户的音源
└── users/
    └── <user-uuid>/
        ├── monitor.db            # 或中央库中所有表带 user_id（二选一）
        ├── credentials.enc       # 加密后的平台 Cookie/令牌
        ├── settings.json
        └── lx-state.json

MUSIC_DIR/
├── shared/                       # 可选公共音乐库
└── users/<user-uuid>/<artist>/   # 可选个人音乐库
```

用户登录密码不以明文文件保存，只保存 Argon2id/bcrypt 哈希。平台 Cookie 使用服务端主密钥加密后写入 `credentials.enc`；主密钥通过 Docker secret 或环境变量提供，不能放进同一个配置备份。

## 角色

- `admin`：用户管理、安装/删除 LX 音源、设置共享目录和系统限制。
- `user`：管理自己的平台账号、订阅、下载和个人设置。

所有 API、WebSocket、监控、曲目、运行记录必须携带并校验 `user_id`。普通用户不能读取其他用户的 Cookie、任务、文件路径或日志。

## 平台登录实现

已实现用户作用域的 `DirectDiscovery` 与独立 `platform-auth`：

1. 用户在 monitor 页面为 QQ/网易云扫码或粘贴 Cookie；
2. monitor 加密保存到 `CONFIG_DIR/users/<uuid>/credentials.enc`；
3. 获取个人收藏夹时，把当前用户凭据放入当前请求上下文；
4. DirectDiscovery 只使用该用户对应平台的 Cookie 发起请求；
5. Cookie 不写入 go-music-dl 的全局配置。

platform-auth 直接复用 `music-lib` 的 QQ/网易云扫码协议，只返回当前扫码会话的 Cookie；服务本身不持久化用户凭据。

## 数据迁移

现有全局数据迁移给首个管理员用户：

- 原 monitors/tracks/runs → 管理员；
- 原 go-music-dl Cookie 不自动复制给其他用户；
- 现有音乐默认放入 shared，管理员可决定是否迁入个人目录；
- LX 音源脚本默认属于 shared，由管理员授权用户使用。

## 分期

1. 用户注册/登录、角色和 API `user_id` 隔离。✅
2. 监控、记录、下载列表和目录隔离。✅
3. 加密凭据库与每用户平台登录。✅（QQ/网易云扫码）
4. 个人收藏夹发现适配器，移除对全局 go-music-dl Cookie 的依赖。✅（QQ/网易云）
5. 酷狗/酷我/咪咕个人收藏夹、配额和审计日志。

## 验收

- A 用户不能看到或运行 B 用户的任务；
- A/B 登录不同 QQ 或网易云账号时，各自只看到自己的收藏夹；
- 服务日志、API 和数据库查询不输出明文 Cookie；
- 删除用户可选择保留共享音乐、转移音乐或删除个人目录；
- 管理员可限制每用户并发、单轮数量和磁盘配额。
