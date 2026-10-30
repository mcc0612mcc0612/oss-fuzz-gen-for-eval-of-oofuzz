#!/usr/bin/env python3
"""Deletes the OSS-Fuzz build dirs of OSS-Fuzz-gen trials that have finished.

A trial's generated project is rectify_docker_tag('<benchmark id>-<trial>');
once its results/<p>-opus55/output-<id>/status/<trial>/result.json says
finished, its build/out, build/work and projects dirs are no longer read
(its results were copied to results/). Dirs touched in the last 5 minutes are
kept. Run from the OSS-Fuzz-gen checkout with its virtualenv.
"""
import glob
import json
import os
import subprocess
import sys
import time

sys.path.insert(0, os.path.expanduser('~/oss-fuzz-gen'))
from experiment.oss_fuzz_checkout import rectify_docker_tag  # pylint: disable=wrong-import-position

OSSFUZZ = os.path.expanduser('~/ofg-oss-fuzz')
RESULTS = os.path.expanduser('~/oss-fuzz-gen/results')

stale = []
for status in glob.glob(f'{RESULTS}/*-opus55/output-*/status/*/result.json'):
  try:
    if not json.load(open(status)).get('finished'):
      continue
  except (OSError, ValueError):
    continue
  bench = status.split('/output-')[1].split('/')[0]
  trial = int(status.split('/status/')[1].split('/')[0])
  name = rectify_docker_tag(f'{bench}-{trial}')
  dirs = [f'build/out/{name}', f'build/work/{name}', f'projects/{name}']
  paths = [os.path.join(OSSFUZZ, d) for d in dirs]
  if not any(os.path.exists(p) for p in paths):
    continue
  if any(os.path.exists(p) and time.time() - os.path.getmtime(p) < 300
         for p in paths):
    continue
  stale.extend(dirs)

if stale:
  # Build outputs are root-owned; delete them from a container.
  subprocess.run(['docker', 'run', '--rm', '-v', f'{OSSFUZZ}:/o',
                  'gcr.io/oss-fuzz-base/base-builder', 'rm', '-rf'] +
                 [f'/o/{d}' for d in stale], check=False)
print(f'pruned {len(stale) // 3} finished trials', flush=True)
