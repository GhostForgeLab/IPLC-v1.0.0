#!/usr/bin/env bash
set -euo pipefail

if [[ ${EUID:-$(id -u)} -ne 0 ]]; then
  echo "错误：请使用 root 运行：sudo bash install.sh"
  exit 1
fi

for cmd in nft python3 ip sysctl systemctl; do
  if ! command -v "$cmd" >/dev/null 2>&1; then
    echo "错误：缺少命令 $cmd"
    echo "Debian 可安装：apt update && apt install -y nftables python3 iproute2 procps"
    exit 1
  fi
done

echo "=== 安装 IPLC Light ==="
echo "原则：不修改现有外部 DNAT 规则；新规则放在独立表 ip iplc_light。"

mkdir -p /usr/local/lib/iplc-light /usr/local/bin /var/lib/iplc-light /var/backups/iplc-light /etc/nftables.d /etc/sysctl.d

stamp="$(date +%Y%m%d-%H%M%S)"
pre="/var/backups/iplc-light/${stamp}_pre-install"
mkdir -p "$pre"
nft list ruleset > "$pre/ruleset.nft" || true
[[ -f /etc/nftables.conf ]] && cp -a /etc/nftables.conf "$pre/nftables.conf"
sysctl -n net.ipv4.ip_forward > "$pre/ip_forward.txt" || true

script_dir="$(cd "$(dirname "$0")" && pwd)"
source_file="$script_dir/iplc.py"
if [[ ! -f "$source_file" ]]; then
  command -v curl >/dev/null 2>&1 || { echo "错误：在线安装需要 curl" >&2; exit 1; }
  download_dir="$(mktemp -d)"
  trap 'rm -rf -- "$download_dir"' EXIT
  source_file="$download_dir/iplc.py"
  curl -fsSL --retry 3 --connect-timeout 10 \
    https://raw.githubusercontent.com/GhostForgeLab/IPLC-v1.0.0/main/iplc.py \
    -o "$source_file"
fi
install -m 0755 "$source_file" /usr/local/lib/iplc-light/iplc.py

for name in iplc-list iplc-add iplc-del iplc-backup iplc-restore iplc-check; do
  ln -sfn /usr/local/lib/iplc-light/iplc.py "/usr/local/bin/$name"
done
ln -sfn /usr/local/lib/iplc-light/iplc.py /usr/local/bin/iplc

cat > /etc/sysctl.d/99-iplc-light.conf <<'EOF'
net.ipv4.ip_forward=1
EOF
sysctl -w net.ipv4.ip_forward=1 >/dev/null

/usr/local/bin/iplc init

# Enable nftables persistence when available. Do not fail installation if service state is unusual.
# 只启用开机加载，不在安装过程中 restart，避免触碰当前正在运行的外部生产规则。
systemctl enable nftables >/dev/null 2>&1 || true

echo
echo "安装完成。"
echo "现有生产规则未被主动删除或改写。"
echo
echo "可用命令："
echo "  iplc-list"
echo "  iplc-add"
echo "  iplc-del"
echo "  iplc-backup"
echo "  iplc-restore"
echo "  iplc-check"
echo
echo "最后统一验收："
echo "  iplc-check"
