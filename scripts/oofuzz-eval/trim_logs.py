#!/usr/bin/env python3
"""Keeps OSS-Fuzz-gen run logs from filling the disk.

A harness that prints on every input (shaderc's printing glslang's built-ins)
grew one run's main log to 27 GB overnight. For results/<p>-opus55:
  - logs/run/*.log over 50 MB, untouched for 10 minutes: first and last 5 MB.
  - status/*/result.json over 50 MB, untouched for 10 minutes and finished:
    each string field over 10 MB cut to its first and last 5 MB.
  - <p>-opus55.log over 2 GB: lines under 100 KB appended to
    <p>-opus55.trimmed.log, then the log truncated in place (its writer keeps
    its offset, leaving a hole that takes no disk). The byte offset already
    copied is kept in <p>-opus55.log.offset.
"""
import glob
import json
import os
import time

RESULTS = os.path.expanduser('~/oss-fuzz-gen/results')
KEEP = 5_000_000


def idle(path, seconds=600):
  return time.time() - os.path.getmtime(path) > seconds


for log in glob.glob(f'{RESULTS}/*-opus55/output-*/logs/run/*.log'):
  size = os.path.getsize(log)
  if size > 50_000_000 and idle(log):
    with open(log, 'rb') as f:
      head = f.read(KEEP)
      f.seek(size - KEEP)
      tail = f.read()
    with open(log + '.tmp', 'wb') as f:
      f.write(head + f'\n\n[... {size} bytes trimmed ...]\n\n'.encode() + tail)
    os.replace(log + '.tmp', log)

for result in glob.glob(f'{RESULTS}/*-opus55/output-*/status/*/result.json'):
  if os.path.getsize(result) <= 50_000_000 or not idle(result):
    continue
  try:
    data = json.load(open(result))
  except ValueError:
    continue
  if not data.get('finished'):
    continue
  for key, value in data.items():
    if isinstance(value, str) and len(value) > 2 * KEEP:
      data[key] = (value[:KEEP] + f'\n\n[... {len(value)} chars trimmed ...]\n\n'
                   + value[-KEEP:])
  with open(result + '.tmp', 'w') as f:
    json.dump(data, f)
  os.replace(result + '.tmp', result)

for log in glob.glob(f'{RESULTS}/*-opus55.log'):
  if os.path.getsize(log) <= 2_000_000_000:
    continue
  offset_file = log + '.offset'
  start = int(open(offset_file).read()) if os.path.exists(offset_file) else 0
  trimmed = log.removesuffix('.log') + '.trimmed.log'
  with open(log, 'rb') as src, open(trimmed, 'ab') as dst:
    try:
      # Skip the hole an earlier truncation left before the writer's offset.
      src.seek(os.lseek(src.fileno(), start, os.SEEK_DATA))
    except OSError:
      continue  # No data after |start|.
    for line in src:
      if len(line) < 100_000:
        dst.write(line.replace(b'\0', b''))
    end = src.tell()
  os.truncate(log, 0)
  with open(offset_file, 'w') as f:
    f.write(str(end))
