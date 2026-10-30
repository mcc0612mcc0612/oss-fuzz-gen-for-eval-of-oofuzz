#!/bin/bash
# Runs OSS-Fuzz-gen over the projects listed in QUEUE_FILE, in order, two
# projects at a time. For each: wait for its pinned build to finish (a line
# "<p> ok|FAIL ..." in a BUILD_STATUS file), write its benchmark YAML with
# prepare.py, run OSS-Fuzz-gen, then delete its generated OSS-Fuzz projects,
# build dirs and images (results/ keeps harnesses, corpora and coverage).
#
# A project starts only when a slot is free, the disk has MIN_FREE_GB, and the
# summed spend ledgers leave room for one more project under SPEND_LIMIT. A
# started project is never stopped. QUEUE_FILE is re-read, so projects can be
# appended while it runs; a line END stops the queue once it is reached.
#
# Usage: run_queue.sh STATUS_FILE QUEUE_FILE BUILD_STATUS...
status=$1; queue=$2; shift 2; build_status=("$@")
SLOTS=2; MIN_FREE_GB=110; SPEND_LIMIT=395; PER_PROJECT=22
# Memory one project's concurrent trials may use. A trial needs its build's
# peak or a running ASan fuzzer's (libFuzzer's 2.5 GB RSS limit), whichever is
# larger. A project starts only once MIN_AVAIL_MB has been available for
# AVAIL_MINUTES in a row, since the VM's balloon driver shrinks its memory.
BUILD_MEM_MB=45000; FUZZ_MEM_MB=2500; MIN_AVAIL_MB=50000; AVAIL_MINUTES=3
TOOLS=$(dirname "$(readlink -f "$0")")
OFG=~/oss-fuzz-gen; OSSFUZZ=~/ofg-oss-fuzz; SPEND=~/ofg-spend
log() { echo "$(date '+%m-%d %H:%M') $*" | tee -a "$status"; }

total_spend() { awk '{s+=$1} END {printf "%.2f", s}' $SPEND/*.txt 2>/dev/null; }
free_gb() { df -BG / | awk 'NR==2 {gsub("G","",$4); print $4}'; }
avail_mb() { awk '/MemAvailable/ {print int($2 / 1024)}' /proc/meminfo; }
memory_settled() {
  for i in $(seq $AVAIL_MINUTES); do
    [ "$(avail_mb)" -ge $MIN_AVAIL_MB ] || return 1; sleep 60
  done
}
build_line() { grep -h "^$1 \(ok\|FAIL\) " "${build_status[@]}" 2>/dev/null | tail -1; }

cleanup() {
  docker run --rm -v $OSSFUZZ/build:/b -v $OSSFUZZ/projects:/p \
    gcr.io/oss-fuzz-base/base-builder bash -c \
    "rm -rf /b/out/$1-* /b/work/$1-* /p/$1-*" 2> /dev/null
  docker images --format '{{.Repository}}' | grep "^gcr.io/oss-fuzz/$1-" |
    xargs -r docker rmi -f > /dev/null 2>&1
}

run_project() {
  # Concurrent trials = BUILD_MEM_MB / one build's peak, 4 to 50; two per target.
  p=$1; peak=$2
  per=$(( peak > FUZZ_MEM_MB ? peak : FUZZ_MEM_MB ))
  trials=$(( BUILD_MEM_MB / per ))
  [ $trials -gt 50 ] && trials=50; [ $trials -lt 4 ] && trials=4
  exp=$(( trials / 2 ))
  log "START $p (LLM_NUM_EXP=$exp, build peak ${peak}MB, total spend \$$(total_spend))"
  cd $OFG && . .venv/bin/activate
  ANTHROPIC_API_KEY="$(tr -d '[:space:]' < ~/token.txt)" \
  OFG_SPEND_LEDGER=$SPEND/$p.txt LLM_NUM_EXP=$exp LLM_NUM_EVA=2 \
    ./run_all_experiments.py --model=claude-opus-5-5 \
    -y benchmark-sets/oofuzz-eval/$p.yaml --oss-fuzz-dir $OSSFUZZ \
    -mr 5 -to 1800 -w results/$p-opus55 > results/$p-opus55.log 2>&1
  rc=$?
  targets=$(grep -c "build success rate" results/$p-opus55.log)
  built=$(grep "build success rate" results/$p-opus55.log | grep -vc "build success rate: 0.0,")
  cleanup $p
  log "DONE $p rc=$rc spend=\$$(cat $SPEND/$p.txt 2>/dev/null) targets_with_a_build=$built/$targets"
}

# The bloaty run started before this queue holds one slot until it ends.
bloaty_pid=$(pgrep -of -- '-w results/bloaty-opus55')
if [ -n "$bloaty_pid" ]; then
  ( while kill -0 $bloaty_pid 2> /dev/null; do sleep 60; done
    targets=$(grep -c "build success rate" $OFG/results/bloaty-opus55.log)
    built=$(grep "build success rate" $OFG/results/bloaty-opus55.log | grep -vc "build success rate: 0.0,")
    cleanup bloaty
    log "DONE bloaty spend=\$$(cat $SPEND/bloaty.txt) targets_with_a_build=$built/$targets" ) &
fi

done_list=" "
while true; do
  p=$(grep -v '^\s*$' "$queue" | while read q; do
        case "$done_list" in *" $q "*) ;; *) echo "$q"; break;; esac; done)
  [ -z "$p" ] && { sleep 60; continue; }
  [ "$p" = END ] && break
  done_list="$done_list$p "
  until line=$(build_line $p) && [ -n "$line" ]; do sleep 60; done
  if [ "$(echo $line | cut -d' ' -f2)" != ok ]; then
    log "SKIP $p: pinned build failed (see builds/$p.build.log)"
    continue
  fi
  verdict=$($OFG/.venv/bin/python -I $TOOLS/prepare.py $p 2>&1 | tail -1)
  case "$verdict" in READY*) log "$verdict";; *) log "SKIP $verdict"; continue;; esac
  while [ "$(jobs -rp | wc -l)" -ge $SLOTS ] || [ "$(free_gb)" -lt $MIN_FREE_GB ] ||
        ! memory_settled; do
    sleep 60
  done
  if awk -v s="$(total_spend)" -v n=$PER_PROJECT -v l=$SPEND_LIMIT 'BEGIN {exit !(s + n > l)}'; then
    log "STOP before $p: total spend \$$(total_spend) leaves no room under \$$SPEND_LIMIT"
    break
  fi
  peak=$(echo $line | sed -n 's/.*peak_mb=\([0-9]*\).*/\1/p')
  run_project $p ${peak:-0} &
  sleep 600  # Let this project's first build wave pass before the next starts.
done
wait
log "QUEUE_DONE total spend \$$(total_spend)"
