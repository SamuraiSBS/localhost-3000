#!/usr/bin/env bash
# Explicit invocation on a new dedicated Ubuntu VPS only. Does not create a VM.
set -euo pipefail
[[ $EUID = 0 ]] || { echo 'Run through sudo on the intended VPS' >&2; exit 2; }
ADMIN_CIDR=${1:?Usage: sudo bash bootstrap-ubuntu.sh ADMIN_IP/32 /path/to/deploy.pub}
PUBLIC_KEY=${2:?existing public key file required}
[[ "$ADMIN_CIDR" =~ ^[0-9a-fA-F.:]+/[0-9]{1,3}$ ]] || exit 2
test -f "$PUBLIC_KEY"
ssh-keygen -l -f "$PUBLIC_KEY" >/dev/null
. /etc/os-release
[[ "$ID" = ubuntu && ( "$VERSION_ID" = 24.04 || "$VERSION_ID" = 22.04 ) ]] || { echo 'Supported: Ubuntu 22.04/24.04 LTS' >&2; exit 2; }
apt-get update
apt-get install -y ca-certificates curl gnupg ufw python3 util-linux
if ! command -v docker >/dev/null; then
  install -m 0755 -d /etc/apt/keyrings
  curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
  chmod a+r /etc/apt/keyrings/docker.asc
  printf 'deb [arch=%s signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/ubuntu %s stable\n' "$(dpkg --print-architecture)" "$VERSION_CODENAME" > /etc/apt/sources.list.d/docker.list
  apt-get update
  apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
fi
docker compose version
systemctl enable --now docker
id deploy >/dev/null 2>&1 || useradd --create-home --shell /bin/bash deploy
usermod -aG docker deploy
install -d -m 0700 -o deploy -g deploy /home/deploy/.ssh
touch /home/deploy/.ssh/authorized_keys
while IFS= read -r key; do
  [[ -z "$key" ]] || grep -qxF "$key" /home/deploy/.ssh/authorized_keys || printf '%s\n' "$key" >> /home/deploy/.ssh/authorized_keys
done < "$PUBLIC_KEY"
chmod 0600 /home/deploy/.ssh/authorized_keys
chown deploy:deploy /home/deploy/.ssh/authorized_keys
install -d -m 0750 -o deploy -g deploy /opt/localhost-3000 /opt/localhost-3000/releases /opt/localhost-3000/state
ufw allow from "$ADMIN_CIDR" to any port 22 proto tcp
ufw allow 80/tcp
ufw allow 443/tcp
ufw allow 443/udp
# CI runners have variable source IPs: use a VPN/self-hosted runner or an explicit agreed SSH rule.
ufw --force enable
echo 'Verify deploy key login in a SECOND SSH session before tightening existing SSH settings.'
echo 'Docker group is root-equivalent. Keep this dedicated VM and deploy key project-scoped.'
echo 'Configure Yandex security group too; published Docker ports may bypass UFW.'
