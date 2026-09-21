# 部署

## 目录

只配置两个宿主机路径：

```env
CONFIG_DIR=/vol1/docker/music-monitor/config
MUSIC_DIR=/vol2/media/Music
MONITOR_PORT=9090
TZ=Asia/Shanghai
```

创建并授权给 uid 1000：

```bash
mkdir -p "$CONFIG_DIR" "$MUSIC_DIR"
chmod -R 777 "$CONFIG_DIR" "$MUSIC_DIR"
```

## 启动

```bash
docker compose up -d --build
docker compose ps
docker compose logs -f monitor lx-gateway platform-auth
```

健康状态应包含：

```text
music-monitor                 healthy
music-monitor-lx-gateway      healthy
music-monitor-platform-auth   running
```

访问 `http://NAS-IP:9090`，创建首位管理员。

## 首次配置

1. 管理员在“设置 → LX 音源管理”导入可信音源；
2. 每位用户在“设置 → 我的平台账号”使用 QQ/网易云/酷狗/哔哩哔哩/汽水扫码；没有扫码协议的平台在“其他平台”中配置 Cookie JSON；
3. 创建榜单、歌单、收藏夹或歌手监控；
4. 先干跑预览，再开启自动下载。

## 备份

必须备份完整 `CONFIG_DIR`，尤其是：

```text
monitor/monitor.db
monitor/.credentials.key
users/*/credentials.enc
lx-sources/
lx/gateway.json
```

若设置了 `CREDENTIALS_MASTER_KEY`，还必须通过独立安全渠道备份该密钥。没有主密钥无法恢复加密 Cookie。

音乐库单独备份 `MUSIC_DIR`。

## 更新

```bash
docker compose build --pull
docker compose up -d --force-recreate
```

## 排错

```bash
docker compose logs --tail=200 monitor
docker compose logs --tail=200 lx-gateway
docker compose logs --tail=200 platform-auth
curl -fsS http://127.0.0.1:9090/api/healthz
```
