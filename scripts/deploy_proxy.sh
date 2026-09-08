#!/bin/bash
# 公開入口 php/kseido.php を heteml (kurage.exbridge.jp) へ FTP 配置する。
# 認証情報は aixec/.env の FTP_HOST / FTP_USER / FTP_PASS。バックエンド設定は kseido_config.php（リポジトリ外）。
set -euo pipefail
cd "$(dirname "$0")/.."
set -a; . /home/kojima/work/aixec/.env; set +a
BACKEND="${KSEIDO_BACKEND_URL:-http://exbridge.ddns.net:18385}"
TMP=$(mktemp)
printf '<?php define("KSEIDO_BACKEND", "%s");\n' "$BACKEND" > "$TMP"
curl -sS -T php/kseido.php "ftp://${FTP_USER}:${FTP_PASS}@${FTP_HOST}/web/kurage_exbridge_jp/kseido.php"
curl -sS -T "$TMP" "ftp://${FTP_USER}:${FTP_PASS}@${FTP_HOST}/web/kurage_exbridge_jp/kseido_config.php"
rm -f "$TMP"
echo "deployed: https://kurage.exbridge.jp/kseido.php/"
curl -s -o /dev/null -w "public: %{http_code}\n" "https://kurage.exbridge.jp/kseido.php/"
