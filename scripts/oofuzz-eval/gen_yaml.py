#!/usr/bin/env python3
"""Writes an OSS-Fuzz-gen benchmark YAML from rows of candidates.py output.

Usage: gen_yaml.py CANDIDATES_TSV PROJECT TARGET_PATH TARGET_NAME OUT_YAML
                   HEADER_COMMENT MANGLED_NAME...
Rows are emitted in the order the mangled names are given.
"""
import re
import sys

import yaml

tsv, project, target_path, target_name, out, comment = sys.argv[1:7]
wanted = sys.argv[7:]
rows = {}
for line in open(tsv):
  size, in_hg, mangled, dem, header, decl = line.rstrip('\n').split('\t')
  rows[mangled] = (dem, decl)

STD = [
    (r'std::__1::', 'std::'),
    (r'std::basic_string<char, std::char_traits<char>, std::allocator<char> >',
     'std::string'),
    (r'std::basic_string_view<char, std::char_traits<char> >',
     'std::string_view'),
    (r'std::basic_ostream<char, std::char_traits<char> >', 'std::ostream'),
    (r'std::basic_istream<char, std::char_traits<char> >', 'std::istream'),
    (r', std::default_delete<([\w:]+)> >', r'>'),
    (r', std::allocator<[^<>]*(<[^<>]*>)?[^<>]*> >', '>'),
    (r'unsigned long', 'uint64_t'),
]


def clean(t):
  for pat, rep in STD:
    t = re.sub(pat, rep, t)
  return t.replace(' >', '>').strip()


def split_params(args):
  out, depth, cur = [], 0, ''
  for ch in args:
    if ch in '<(':
      depth += 1
    elif ch in '>)':
      depth -= 1
    if ch == ',' and depth == 0:
      out.append(cur.strip())
      cur = ''
    else:
      cur += ch
  if cur.strip():
    out.append(cur.strip())
  return out


QUALIFIERS = re.compile(
    r'\b(static|virtual|inline|explicit|extern|friend|constexpr|'
    r'[A-Z][A-Z0-9_]*(API|EXPORT|EXPORTS|DLL|PUBLIC|DECL)[A-Z0-9_]*)\b')

functions = []
for mangled in wanted:
  dem, decl = rows[mangled]
  depth = 0
  for i, ch in enumerate(dem):
    if ch == '<':
      depth += 1
    elif ch == '>':
      depth -= 1
    elif ch == '(' and depth == 0:
      break
  qual, rest = dem[:i], dem[i:]
  close = rest.rfind(')')
  args, suffix = rest[1:close], rest[close + 1:].strip()
  short = qual.split('::')[-1]
  m = re.search(r'(.*?)\b' + re.escape(short) + r'\s*\(', decl)
  ret = QUALIFIERS.sub('', m.group(1) if m else '').strip()
  ret = re.sub(r'\s+', ' ', ret).replace('& ', '&').replace('* ', '*')
  # Drop all-caps macros (exports, attributes) when a real type remains.
  words = ret.split()
  typed = [w for w in words if not re.fullmatch(r'[A-Z][A-Z0-9_]{2,}', w)]
  if typed and len(typed) < len(words):
    ret = ' '.join(typed)
  is_ctor = len(qual.split('::')) > 1 and short == qual.split('::')[-2]
  if is_ctor or short.startswith('~'):
    ret = ''
  elif re.search(r'[(){};=]|\breturn\b', ret):
    ret = None  # Not a declaration line; leave the type out, not guessed.
  elif not ret:
    ret = 'void'
  params = [clean(p) for p in split_params(args)]
  sig = (f'{ret} ' if ret else '') + f'{qual}({", ".join(params)})'
  if suffix:
    sig += ' ' + suffix
  functions.append({
      'name': mangled,
      'params': [{'name': '', 'type': p} for p in params],
      'return_type': ret if ret is not None else '',
      'signature': sig,
  })

doc = {
    'functions': functions,
    'language': 'c++',
    'project': project,
    'use_project_examples': False,
    'target_name': target_name,
    'target_path': target_path,
}
with open(out, 'w') as f:
  f.write(''.join(f'# {l}\n' for l in comment.split('\n')))
  yaml.safe_dump(doc, f, sort_keys=False, width=200)
print(f'{out}: {len(functions)} functions')
for fn in functions:
  print('  ', fn['signature'][:150])
