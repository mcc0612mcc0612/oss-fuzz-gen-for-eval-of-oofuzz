#!/bin/bash
# Every 3 minutes, removes what this session's OSS-Fuzz-gen runs leave behind:
# stopped containers created from a gcr.io/oss-fuzz/ image since SINCE that
# exited over 3 minutes ago, and untagged images created since SINCE that no
# container uses, the build dirs of finished trials (prune_finished.py), and
# oversized run logs (trim_logs.py).
# Older containers and images on the machine are left.
SINCE=2026-10-30T08:00
while true; do
  cutoff=$(date -u -d '3 minutes ago' +%Y-%m-%dT%H:%M:%S)
  docker ps -a --filter status=exited -q |
    xargs -r docker inspect -f '{{.Id}} {{.Created}} {{.State.FinishedAt}} {{.Config.Image}}' |
    awk -v s=$SINCE -v c=$cutoff '$2 >= s && $3 < c && $4 ~ /^gcr\.io\/oss-fuzz\//' |
    awk '{print $1}' | xargs -r docker rm > /dev/null 2>&1
  docker images -q --filter dangling=true |
    xargs -r docker inspect -f '{{.Id}} {{.Created}}' |
    awk -v s=$SINCE '$2 >= s {print $1}' | xargs -r docker rmi > /dev/null 2>&1
  ~/oss-fuzz-gen/.venv/bin/python -I "$(dirname "$(readlink -f "$0")")/prune_finished.py" > /dev/null 2>&1
  ~/oss-fuzz-gen/.venv/bin/python -I "$(dirname "$(readlink -f "$0")")/trim_logs.py" > /dev/null 2>&1
  sleep 180
done
