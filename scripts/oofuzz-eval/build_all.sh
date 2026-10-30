#!/bin/bash
# Builds each OSS-Fuzz project once with ASan, one at a time, records the build
# container's peak memory, and on success copies its /src tree out for header
# scanning. Before each build, waits for the load average to fall below
# MAX_LOAD and for 20 GB of memory to be available.
# Usage: build_all.sh OUT_DIR PROJECT...
out=$1; shift
MAX_LOAD=300
mkdir -p "$out"
cd ~/ofg-oss-fuzz || exit 1

for p in "$@"; do
  while [ "$(cut -d. -f1 /proc/loadavg)" -ge $MAX_LOAD ] ||
        [ "$(awk '/MemAvailable/ {print int($2 / 1048576)}' /proc/meminfo)" -lt 20 ]; do
    sleep 30
  done
  log="$out/$p.build.log"; start=$(date +%s); peak=0
  # A clean work dir, as OSS-Fuzz-gen's trial builds have, so the peak is real.
  docker run --rm -v ~/ofg-oss-fuzz/build/work:/w gcr.io/oss-fuzz-base/base-builder \
    rm -rf "/w/$p" 2> /dev/null
  if python3 infra/helper.py build_image --no-pull "$p" > "$log" 2>&1; then
    docker rm -f "ofgbuild-$p" > /dev/null 2>&1
    OSS_FUZZ_SAVE_CONTAINERS_NAME="ofgbuild-$p" \
      python3 infra/helper.py build_fuzzers --sanitizer address "$p" >> "$log" 2>&1 &
    pid=$!
    while kill -0 $pid 2> /dev/null; do
      for c in $(docker ps -q --filter ancestor=gcr.io/oss-fuzz/$p); do
        m=$(docker stats --no-stream --format '{{.MemUsage}}' $c 2> /dev/null |
            awk '{v=$1; if (v ~ /GiB/) {sub("GiB","",v); v*=1024} else {sub("MiB","",v)}; printf "%d", v}')
        [ "${m:-0}" -gt $peak ] && peak=$m
      done
      sleep 2
    done
    wait $pid && rc=ok || rc=FAIL
  else
    rc=FAIL
  fi
  if [ $rc = ok ]; then
    # Headers and static archives of the built /src, from the kept container
    # (in-source builds leave their archives only there).
    rm -rf "$out/$p-src" && mkdir -p "$out/$p-src"
    docker commit "ofgbuild-$p" "ofgbuild-$p:snap" > /dev/null
    docker run --rm -v "$out/$p-src:/dst" "ofgbuild-$p:snap" bash -c \
      'cd /src && find . -type f \( -name "*.h" -o -name "*.hh" -o -name "*.hpp" -o -name "*.hxx" -o -name "*.h++" -o -name "*.inc" -o -name "*.a" -o -name "*.c" -o -name "*.cc" -o -name "*.cpp" -o -name "*.cxx" \) -print0 | tar --null -cf - -T - | tar -xf - -C /dst && chown -R '"$(id -u):$(id -g)"' /dst' >> "$log" 2>&1
    docker rmi -f "ofgbuild-$p:snap" > /dev/null
  fi
  docker rm -f "ofgbuild-$p" > /dev/null 2>&1
  echo "$p $rc $(( $(date +%s) - start ))s peak_mb=$peak"
done
echo ALL_DONE
