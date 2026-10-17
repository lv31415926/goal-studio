#!/usr/bin/env bash
# 沪上错题 安装脚本（已 clone 本仓库场景；远程一键安装命令见本目录 README.md）
set -e
SRC="$(cd "$(dirname "$0")" && pwd)"
DST="${AGENTS_SKILLS_DIR:-$HOME/.agents/skills}/沪上错题"
mkdir -p "$(dirname "$DST")" && rm -rf "$DST" && cp -R "$SRC" "$DST"
python3 "$DST/scripts/cuoti.py" init
echo "✅ 沪上错题 已安装到 $DST —— 在 Agent 会话里说「记错题」+发照片即可"
