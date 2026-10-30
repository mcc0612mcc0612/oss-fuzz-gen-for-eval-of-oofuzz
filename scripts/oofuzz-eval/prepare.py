#!/usr/bin/env python3
"""Writes benchmark-sets/oofuzz-eval/<project>.yaml for one built project.

Picks the fuzz target OSS-Fuzz-gen rewrites (HarnessGenerator's, when that
binary was built and its source exists; otherwise the built fuzzer linking the
most candidate functions), ranks candidates with candidates.py over that
fuzzer and the static archives it links, and keeps up
to 25 of HarnessGenerator's functions, largest first, at most 6 per class; no
other function tops the list up. Prints one status line: READY, or DECIDE with the reason.

Usage: prepare.py PROJECT
"""
import collections
import glob
import os
import subprocess
import sys

import yaml

TOOLS = os.path.dirname(os.path.abspath(__file__))
SCRATCH = os.path.dirname(TOOLS)
BUILDS = os.path.join(SCRATCH, 'builds')
HG_SETS = os.path.expanduser('~/HarnessGenerator/data/benchmark-set')
OUT_ROOT = os.path.expanduser('~/ofg-oss-fuzz/build/out')
OFG = os.path.expanduser('~/oss-fuzz-gen')
PY = os.path.join(OFG, '.venv/bin/python')
MAX_TARGETS, MAX_PER_CLASS, MIN_SIZE = 25, 6, 150
# Fuzz targets whose binary name does not match its source file name, read
# from the project's build files.
TARGET_OVERRIDES = {
    # fuzzing_CMake: add_executable(tink_encrypt_fuzzer tink_encrypt_decrypt_fuzzer.cc)
    'tink-cc': ('tink_encrypt_fuzzer',
                '/src/tink-cc/fuzzing/tink_encrypt_decrypt_fuzzer.cc'),
    # HarnessGenerator's shaderc.yaml names the target 'shaderc_fuzzer.cc'.
    'shaderc': ('shaderc_fuzzer', '/src/shaderc_fuzzer.cc'),
}

# OSS-Fuzz project -> (HarnessGenerator name, source dir under /src).
PROJECTS = {
    'exiv2': ('exiv2', 'exiv2'), 'poppler': ('poppler', 'poppler'),
    'wabt': ('wabt', 'wabt'), 'xpdf': ('xpdf', 'xpdf'),
    'cppcheck': ('cppcheck', 'cppcheck'), 'hunspell': ('hunspell', 'hunspell'),
    'libraw': ('libraw', 'libraw'), 'ogre': ('ogre', 'ogre'),
    'qpdf': ('qpdf', 'qpdf'), 'draco': ('draco', 'draco'),
    'muduo': ('muduo', 'muduo'), 'flatbuffers': ('flatbuffers', 'flatbuffers'),
    'libphonenumber': ('libphonenumber', 'libphonenumber'),
    'shaderc': ('shaderc', 'shaderc'), 'tink-cc': ('tink-cc', 'tink-cc'),
    'tesseract-ocr': ('tesseract', 'tesseract'), 'bento4': ('bento4', 'bento4'),
}


def fuzzers(project):
  out = os.path.join(OUT_ROOT, project)
  found = []
  for f in sorted(os.listdir(out)):
    p = os.path.join(out, f)
    if (os.path.isfile(p) and os.access(p, os.X_OK) and
        f != 'llvm-symbolizer' and '.' not in f):
      found.append(f)
  return found


def archives(project, src_root):
  """Static libraries the project build left in /work or in its source tree."""
  roots = [os.path.expanduser(f'~/ofg-oss-fuzz/build/work/{project}'), src_root]
  engines = ('aflplusplus', 'honggfuzz', 'libfuzzer', 'centipede', 'fuzztest')
  return [p for r in roots for p in glob.glob(os.path.join(r, '**', '*.a'),
                                              recursive=True)
          if os.path.relpath(p, r).split(os.sep)[0] not in engines]


def candidates(binary, src, hg_yamls, tsv, libs=()):
  listing = tsv + '.archives'
  with open(listing, 'w') as f:
    f.write('\n'.join(libs))
  env = dict(os.environ, CANDIDATE_ARCHIVES_FILE=listing)
  if os.path.basename(binary) in STUB_TARGETS:
    env['CANDIDATE_STUB_FUZZER'] = '1'
  with open(tsv, 'w') as f:
    subprocess.run([PY, '-I', os.path.join(TOOLS, 'candidates.py'), binary, src,
                    ''] + hg_yamls, stdout=f, check=True, env=env)
  return [l.rstrip('\n').split('\t') for l in open(tsv)]


def find_source(project_src_root, name):
  """The fuzzer's source nearest /src (build scripts may copy it deeper)."""
  for ext in ('.cc', '.cpp', '.cxx', '.c'):
    hits = glob.glob(os.path.join(project_src_root, '**', name + ext),
                     recursive=True)
    if hits:
      depth = lambda h: h.count(os.sep)
      shallowest = [h for h in hits if depth(h) == min(map(depth, hits))]
      if len(shallowest) == 1:
        return shallowest[0]
  return None


# Projects whose fuzz target is a stub written for this evaluation: every
# archive its build.sh links counts, as the stub itself calls nothing.
STUB_FUZZERS = {'bento4'}
# Benchmarks chosen by hand; prepare.py keeps them as they are.
HAND_PICKED = {'bloaty'}
STUB_TARGETS = {'bento4_fuzzer'}


def main(project):
  if project in HAND_PICKED:
    out = os.path.join(OFG, 'benchmark-sets/oofuzz-eval', f'{project}.yaml')
    n = len(yaml.safe_load(open(out))['functions'])
    return f'READY {project}: {n} hand-picked targets ({out})'
  hg_name, src_dir = PROJECTS[project]
  src_root = os.path.join(BUILDS, f'{project}-src')
  src = os.path.join(src_root, src_dir)
  if not os.path.isdir(src):
    return f'DECIDE {project}: source dir /src/{src_dir} missing'
  hg_main = os.path.join(HG_SETS, f'{hg_name}.yaml')
  hg_yamls = [y for y in (hg_main, os.path.join(HG_SETS, f'{hg_name}_all.yaml'))
              if os.path.isfile(y)]
  if not hg_yamls:
    # No benchmark YAML: the targets of HarnessGenerator's own runs.
    derived = os.path.join(SCRATCH, 'hg_derived', f'{hg_name}.yaml')
    hg_yamls = [derived] if os.path.isfile(derived) else []
  if not hg_yamls:
    return f'DECIDE {project}: no HarnessGenerator target list'
  built = fuzzers(project)
  if not built:
    return f'DECIDE {project}: build produced no fuzzer'

  target_name = target_path = None
  if project in TARGET_OVERRIDES:
    name, tp = TARGET_OVERRIDES[project]
    if name in built and os.path.isfile(
        os.path.join(src_root, tp.removeprefix('/src/'))):
      target_name, target_path = name, tp
  if not target_name and os.path.isfile(hg_main):
    hg = yaml.safe_load(open(hg_main))
    tp = hg.get('target_path', '')
    local = os.path.join(src_root, tp.removeprefix('/src/'))
    if hg.get('target_name') in built and os.path.isfile(local):
      target_name, target_path = hg['target_name'], tp
  tsv = os.path.join(BUILDS, f'{project}.cand.tsv')
  libs = archives(project, src_root)
  if target_name:
    rows = candidates(os.path.join(OUT_ROOT, project, target_name), src,
                      hg_yamls, tsv, libs)
  else:
    best = None
    for f in built:
      rows_f = candidates(os.path.join(OUT_ROOT, project, f), src, hg_yamls,
                          tsv + '.' + f, libs)
      if best is None or len(rows_f) > len(best[1]):
        best = (f, rows_f)
    target_name, rows = best
    os.replace(tsv + '.' + target_name, tsv)
    local = find_source(src_root, target_name)
    if not local:
      return (f'DECIDE {project}: no unique source file for fuzzer '
              f'{target_name} (built: {" ".join(built)})')
    target_path = '/src/' + os.path.relpath(local, src_root)

  per_class, chosen = collections.Counter(), []
  ranked = sorted(rows, key=lambda r: (-int(r[1]), -int(r[0])))
  for size, in_hg, mangled, dem, header, decl in ranked:
    # HarnessGenerator's targets only: no other function tops the list up.
    if in_hg != '1' or int(size) < MIN_SIZE or len(chosen) >= MAX_TARGETS:
      continue
    qual = dem.split('(')[0]
    owner = '::'.join(qual.split('::')[:-1]) or qual
    if per_class[owner] >= MAX_PER_CLASS:
      continue
    per_class[owner] += 1
    chosen.append((mangled, in_hg))
  if not chosen:
    return (f'DECIDE {project}: none of HarnessGenerator\'s targets is callable '
            f'from {target_name}')

  n_hg = sum(int(h) for _, h in chosen)
  comment = (
      f'{project} at the commit HarnessGenerator pins. {len(chosen)} targets, '
      f'all from HarnessGenerator\n'
      f'({", ".join(os.path.basename(y) for y in hg_yamls)}): the largest ones '
      f'OSS-Fuzz-gen can call (public, declared\n'
      f'in a project header, in {target_name} or an archive it links), at most '
      f'{MAX_PER_CLASS} per class.\n'
      f'Selected by script; symbol names are the libc++ ones OSS-Fuzz builds.')
  out = os.path.join(OFG, 'benchmark-sets/oofuzz-eval', f'{project}.yaml')
  subprocess.run([PY, '-I', os.path.join(TOOLS, 'gen_yaml.py'), tsv, project,
                  target_path, target_name, out, comment] +
                 [m for m, _ in chosen], check=True, stdout=subprocess.DEVNULL)
  return (f'READY {project}: {len(chosen)} targets ({n_hg} from HG), fuzzer '
          f'{target_name} ({target_path})')


if __name__ == '__main__':
  print(main(sys.argv[1]), flush=True)
