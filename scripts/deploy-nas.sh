#!/usr/bin/env bash
# ============================================================================
#  在 NAS / Linux 上用「现成镜像」部署 music-monitor
#
#  适用：镜像已经在仓库里（Docker Hub / ACR / TCR / 私有 registry），
#        NAS 只需拉取运行，**不在本地构建**。也就是 docs/deploy.md 的「方案 B」。
#
#  用法：
#     ./scripts/deploy-nas.sh                                 # 按 .env 的 APP_VERSION 拉 v<版本号>
#     ./scripts/deploy-nas.sh yua0712/music-monitor-lx:v1.2.2 # 指定主服务镜像地址
#     MONITOR_IMAGE=xxx docker compose ...                    # 也可直接改 .env
#
#  脚本是幂等的：重复执行只会重新拉取并重启容器，不会丢数据。
# ============================================================================
set -euo pipefail

# 镜像仓库地址（不含 tag）。tag 由 .env 里的 APP_VERSION 派生，见下面第 2 步。
DEFAULT_REPO="yua0712/music-monitor-lx"

# 可选：第一个参数直接指定完整镜像地址；不传则自动用 <DEFAULT_REPO>:v<APP_VERSION>。
ARG_IMAGE="${1:-}"

info()  { printf '\033[36m[deploy]\033[0m %s\n' "$*"; }
warn()  { printf '\033[33m[deploy]\033[0m %s\n' "$*" >&2; }
fatal() { printf '\033[31m[deploy]\033[0m %s\n' "$*" >&2; exit 1; }

# ── 0. 切到仓库根目录（脚本可能在 scripts/ 下被调用）────────────────────────
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$HERE/.." && pwd)"
cd "$ROOT"
[ -f docker-compose.yml ] || fatal "没找到 docker-compose.yml，请在仓库根目录执行（当前：$ROOT）"

# ── 1. 检查 docker / compose ────────────────────────────────────────────────
command -v docker >/dev/null 2>&1 || fatal "没装 docker，或当前用户没有 docker 命令权限"

if docker compose version >/dev/null 2>&1; then
  DC=(docker compose)
elif command -v docker-compose >/dev/null 2>&1; then
  DC=(docker-compose)          # 老版本独立二进制
else
  fatal "找不到 docker compose 插件，也没有 docker-compose 命令"
fi
info "compose 命令：${DC[*]}"

# ── 2. 生成 / 更新 .env ────────────────────────────────────────────────────
if [ ! -f .env ]; then
  [ -f .env.example ] || fatal "缺少 .env.example，无法生成 .env"
  cp .env.example .env
  info "已从 .env.example 生成 .env"
else
  info "复用已有 .env（不会被覆盖）"
fi

# 从 .env 读取宿主机配置（容忍行尾注释与空白），缺省回落到给定默认值。
# 注意：这里读的是「宿主机」路径 / 版本号；容器内路径程序内部写死，不在这里改。
envval() {
  local v
  v="$(sed -n "s/^$1=//p" .env | head -1 | sed 's/[[:space:]]*#.*$//' | tr -d '[:space:]')"
  printf '%s' "${v:-$2}"
}

# 镜像地址：
#   传了参数   → 用参数指定的地址（写进 .env 固定下来）
#   没传参数   → 移除 .env 里固定的 MONITOR_IMAGE，交给 compose 按 APP_VERSION 派生
#                （这样以后 bump 版本号，即使不重跑本脚本也能跟着变）
APP_VERSION="$(envval APP_VERSION 1.2.2)"

if [ -n "$ARG_IMAGE" ]; then
  IMAGE="$ARG_IMAGE"
  # 原地替换 / 追加，不重排其它内容；用 | 作分隔符，避免地址里的 / 冲突
  if grep -q '^MONITOR_IMAGE=' .env; then
    sed -i.bak "s|^MONITOR_IMAGE=.*|MONITOR_IMAGE=${IMAGE}|" .env && rm -f .env.bak
  else
    printf '\nMONITOR_IMAGE=%s\n' "$IMAGE" >> .env
  fi
  info "APP_VERSION   = $APP_VERSION"
  info "MONITOR_IMAGE = $IMAGE   （已在 .env 固定，不再跟随 APP_VERSION）"
else
  if grep -q '^MONITOR_IMAGE=' .env; then
    sed -i.bak '/^MONITOR_IMAGE=/d' .env && unlink .env.bak
    info "已移除 .env 里固定的 MONITOR_IMAGE"
  fi
  IMAGE="${DEFAULT_REPO}:v${APP_VERSION}"
  info "APP_VERSION   = $APP_VERSION"
  info "MONITOR_IMAGE = $IMAGE   （按版本号自动派生）"
fi

# ── 3. 数据目录与权限 ──────────────────────────────────────────────────────
# 从 .env 读取宿主机路径，缺省回落到 compose 里的默认值。
CONFIG_DIR="$(envval CONFIG_DIR ./config)"
MUSIC_DIR="$(envval MUSIC_DIR ./data/downloads)"

# monitor 与 LX 网关以 uid=1000 运行，宿主机目录必须可写
mkdir -p "$CONFIG_DIR" "$MUSIC_DIR"
chmod -R 777 "$CONFIG_DIR" "$MUSIC_DIR" 2>/dev/null \
  || warn "chmod 失败，若容器报权限错误请手动处理：sudo chmod -R 777 '$CONFIG_DIR' '$MUSIC_DIR'"
info "全部配置与状态 : $CONFIG_DIR/"
info "音乐库         : $MUSIC_DIR/"

# ── 4. 拉取镜像 ────────────────────────────────────────────────────────────
info "拉取镜像（国内直连 Docker Hub 常超时，下面若失败请看脚本末尾的加速器提示）..."
if ! "${DC[@]}" pull; then
  warn "--------------------------------------------------------------------"
  warn "镜像拉取失败。国内网络下 registry-1.docker.io 经常不可达，需要配镜像加速："
  warn ""
  warn "  sudo mkdir -p /etc/docker"
  warn "  sudo tee /etc/docker/daemon.json >/dev/null <<'EOF'"
  warn "  { \"registry-mirrors\": [\"https://docker.1ms.run\"] }"
  warn "  EOF"
  warn "  sudo systemctl restart docker"
  warn ""
  warn "飞牛 / 群晖 / 威联通也可在「Docker 设置 → 镜像加速」里填 https://docker.1ms.run"
  warn "注意：只有 docker.1ms.run 实测能代理「用户命名空间」镜像（如 yua0712/xxx），"
  warn "      daocloud 只代理官方库镜像，拉用户镜像会 403。"
  warn ""
  warn "不想改全局配置的话，也可以把镜像地址写成带前缀的形式，"
  warn "也可以把镜像地址写成带前缀的形式："
  warn "  docker.1ms.run/yua0712/music-monitor-lx:latest"
  warn "--------------------------------------------------------------------"
  exit 1
fi

# ── 5. 启动 ────────────────────────────────────────────────────────────────
info "启动容器..."
"${DC[@]}" up -d

# ── 6. 检查 ────────────────────────────────────────────────────────────────
info "容器状态："
"${DC[@]}" ps

PORT="$(envval MONITOR_PORT 9090)"
cat <<EOF

================================================================
部署完成  （删容器不丢数据；配置全部集中在 config/，备份就打包它）

  配置根目录         $CONFIG_DIR/
                     ├── monitor/      用户、监控、Session 与加密主密钥
                     ├── users/        用户独立加密平台凭据
                     ├── lx/           LX 网关状态
                     └── lx-sources/   LX 音源脚本
  音乐库             $MUSIC_DIR/

  监控控制台   http://<NAS-IP>:${PORT}
  首次使用：打开 :${PORT} 创建管理员 → 导入 LX 音源 → 每位用户独立扫码 → 建监控

  查看日志   ${DC[*]} logs -f monitor lx-gateway platform-auth
  停止       ${DC[*]} down
  更新       ${DC[*]} pull && ${DC[*]} up -d
  备份配置   tar czf config-backup-\$(date +%F).tar.gz '$CONFIG_DIR'
================================================================
EOF
