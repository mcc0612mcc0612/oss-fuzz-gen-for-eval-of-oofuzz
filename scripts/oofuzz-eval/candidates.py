#!/usr/bin/env python3
"""Ranks OSS-Fuzz-gen target candidates for one project.

A candidate is a strong text symbol of the project's fuzzer binary (so not an
inline or template instantiation), outside anonymous namespaces, std and
third-party code, whose name is declared in a header of the project source
(tests and third_party excluded). Candidates also present in HarnessGenerator
YAMLs are marked. Output: TSV of size, in_hg, mangled, demangled, header,
declaration line.

Usage: [CANDIDATE_ARCHIVES_FILE=list.txt] candidates.py BINARY SRC_DIR PREFIX
           [HG_YAML ...] > out.tsv
list.txt names one static archive per line.
NAMESPACE_PREFIX is a comma list of required name prefixes, or '' for any
name outside the dependency namespaces in |EXCLUDED|.
"""
import collections
import glob
import os
import re
import subprocess
import sys

import yaml

binary, src, prefixes = sys.argv[1], sys.argv[2], sys.argv[3].split(',')
EXCLUDED = ('std::', '__', 'absl::', 'google::', 'testing::', 'boost::',
            'fuzzer::', 'icu_', 'icu::', 're2::', 'Json::', 'pugi::',
            'fmt::', 'nlohmann::', 'llvm::', 'cs_', 'tinyxml2::')
# Protobuf-generated members and other bookkeeping, not parsing logic.
GENERATED = re.compile(r'::(_Internal\w*|InternalSwap|MergeFrom|CopyFrom|'
                       r'ByteSizeLong|SerializeWithCachedSizes\w*|'
                       r'IsInitialized|SharedCtor|SharedDtor|SetCachedSize|'
                       r'Clear|New|GetMetadata|descriptor)$')
hg_yamls = sys.argv[4:]

def text_symbols(path):
  """Returns {mangled: size} of the strong text symbols defined in |path|."""
  out = subprocess.run(['nm', '-S', '--defined-only', path],
                       capture_output=True, text=True).stdout
  found = {}
  for line in out.splitlines():
    parts = line.split()
    if len(parts) == 4 and parts[2] == 'T' or (
        len(parts) == 4 and parts[2] == 't' and parts[3].startswith('_Z')):
      if parts[3].startswith('_Z') or re.fullmatch(r'[A-Za-z]\w*', parts[3]):
        found[parts[3]] = max(found.get(parts[3], 0), int(parts[1], 16))
  return found


# The fuzzer's own symbols, plus every symbol of each static archive it links
# (an archive counts as linked when one of its symbols is in the binary): a
# generated harness linking the same archives can reach any of them.
pool = text_symbols(binary)
in_binary = set(pool)
archive_list = os.environ.get('CANDIDATE_ARCHIVES_FILE')
archive_paths = open(archive_list).read().split('\n') if archive_list else []
for archive in filter(None, archive_paths):
  members = text_symbols(archive)
  # A stub fuzzer references nothing, but its build links these archives.
  if in_binary & set(members) or os.environ.get('CANDIDATE_STUB_FUZZER'):
    for m, size in members.items():
      pool[m] = max(pool.get(m, 0), size)
syms = sorted(pool.items())
dem_all = subprocess.run(['c++filt'], input='\n'.join(m for m, _ in syms),
                         capture_output=True, text=True).stdout.splitlines()
pool_list = list(syms)


def qualified(d):
  depth = 0
  for i, ch in enumerate(d):
    if ch in '<':
      depth += 1
    elif ch in '>':
      depth -= 1
    elif ch == '(' and depth == 0:
      return d[:i]
  return d


def arity(d):
  args = d[len(qualified(d)):]
  if args.startswith('()'):
    return 0
  depth, n = 0, 1
  for ch in args[1:]:
    if ch in '<(':
      depth += 1
    elif ch in '>)':
      depth -= 1
      if depth < 0:
        break
    elif ch == ',' and depth == 0:
      n += 1
  return n


skip_dir = re.compile(r'/(third_party|thirdparty|3rdparty|external|test|tests|'
                      r'unittest|googletest|gtest|fuzz|fuzzing|examples?|build)/',
                      re.I)
headers = {}
for p in glob.glob(os.path.join(src, '**', '*.h*'), recursive=True):
  rel = os.path.relpath(p, src)
  if skip_dir.search('/' + rel) or not os.path.isfile(p):
    continue
  try:
    headers[rel] = open(p, errors='replace').read()
  except OSError:
    pass
decl_index = collections.defaultdict(list)
c_params = {}
for rel, text in headers.items():
  for m in re.finditer(r'^(?![ \t]*\*)[^/\n#]*?\b(~?\w+)\s*\(', text, re.M):
    line = text[m.start():text.find('\n', m.end())].strip()
    if not re.search(r'[A-Za-z]', text[m.start():m.start(1)]):
      # Return type on the line above: `std::string\nName(...)`.
      prev_end = m.start() - 1
      prev = text[text.rfind('\n', 0, prev_end) + 1:prev_end].strip()
      line = prev + ' ' + line
    if line.startswith(('return', 'if', 'for', 'while', 'switch')):
      continue
    decl_index[m.group(1)].append((rel, line[:160]))
    prefix = line[:line.find(m.group(1))]
    # A C declaration: a type before the name, not a call or an expression.
    looks_declared = (re.search(r'[A-Za-z_]\w*[\s*&]*$', prefix) and
                      not re.search(r'[=(,.]|->|\breturn\b', prefix))
    if looks_declared and m.group(1) not in c_params:
      depth, i = 0, m.end() - 1
      while i < len(text):
        depth += {'(': 1, ')': -1}.get(text[i], 0)
        i += 1
        if depth == 0:
          break
      c_params[m.group(1)] = re.sub(r'\s+', ' ', text[m.end():i - 1]).strip()

def class_body_decl(text, owner, short):
  """Returns (declaration line, access) of |short| inside class |owner|'s body.

  Access is the last specifier before the declaration at the body's top
  nesting level; a class starts private, a struct public.
  """
  for m in re.finditer(r'\b(class|struct)\s+(\w+\s+)?' + re.escape(owner) +
                       r'\b[^;{()]*\{', text):
    depth, i = 1, m.end()
    while i < len(text) and depth:
      depth += {'{': 1, '}': -1}.get(text[i], 0)
      i += 1
    body = text[m.end():i]
    for dm in re.finditer(r'^(?![ \t]*\*)[^/\n#]*?\b' + re.escape(short) + r'\s*\(', body,
                          re.M):
      access, level = ('public' if m.group(1) == 'struct' else 'private'), 0
      for tok in re.finditer(r'[{}]|\b(public|private|protected)\s*:',
                             body[:dm.start()]):
        if tok.group(0) == '{':
          level += 1
        elif tok.group(0) == '}':
          level -= 1
        elif level == 0:
          access = tok.group(1)
      line = body[dm.start():body.find('\n', dm.end())].strip()
      if not re.search(r'[A-Za-z]', line[:line.find(short)]):
        prev_end = dm.start() - 1
        prev = body[body.rfind('\n', 0, prev_end) + 1:prev_end].strip()
        line = prev + ' ' + line
      return line[:160], access
  return None


hg_keys = set()
for y in hg_yamls:
  names = [f['name'] for f in yaml.safe_load(open(y)).get('functions', [])]
  for d in subprocess.run(['c++filt'], input='\n'.join(names),
                          capture_output=True, text=True).stdout.splitlines():
    hg_keys.add((qualified(d), arity(d)))

keep = [m.startswith('_Z') or (m in c_params and m != 'main' and
                                not m.startswith('LLVMFuzzer'))
        for m, _ in syms]
syms = [x for x, k in zip(syms, keep) if k]
dem = [d if m.startswith('_Z') else f'{m}({c_params[m]})'
       for (m, _), d, k in zip(pool_list, dem_all, keep) if k]
best = {}
for (m, size), d in zip(syms, dem):
  q = qualified(d)
  if not any(q.startswith(p) for p in prefixes) or q.startswith(EXCLUDED):
    continue
  if ('anonymous namespace' in d or 'operator' in q or '<' in q or
      GENERATED.search(q) or '::~' in q or '[abi:' in d or ')::' in d):
    continue
  parts = q.split('::')
  short = parts[-1]
  decls = decl_index.get(short, [])
  if len(parts) > 1:
    # The enclosing class or namespace must be declared in the same header.
    # A class must be defined there (not just forward-declared).
    owner = re.compile(r'\b((class|struct)\s+(\w+\s+)?' +
                       re.escape(parts[-2]) + r'\b[^;{()]*\{|namespace\s+' +
                       re.escape(parts[-2]) + r'\s*\{)')
    decls = [d for d in decls if owner.search(headers[d[0]])]
    # Of the headers that qualify, the one that also defines the most outer
    # scopes (`Outer` for Outer::Builder::Build) holds the right declaration.
    def scopes_defined(decl):
      text = headers[decl[0]]
      return sum(bool(re.search(r'\b(class|struct|namespace)\s+(\w+\s+)?' +
                                re.escape(scope) + r'\b', text))
                 for scope in parts[:-2])
    decls.sort(key=scopes_defined, reverse=True)
  if not decls:
    continue
  if len(parts) > 1:
    # Prefer the declaration inside the class body, so overloads of the same
    # name in other classes do not lend their return type.
    scoped = class_body_decl(headers[decls[0][0]], parts[-2], short)
    if scoped:
      if scoped[1] != 'public':
        continue  # A harness cannot call a private or protected member.
      decls = [(decls[0][0], scoped[0])] + decls
  key = (q, arity(d))
  if key not in best or size > best[key][1]:
    best[key] = (m, size, d, decls[0])

rows = sorted(best.items(), key=lambda kv: -kv[1][1])
for (q, n), (m, size, d, (rel, line)) in rows:
  fields = [str(size), str(int((q, n) in hg_keys)), m, d, rel, line]
  print('\t'.join(re.sub(r'\s+', ' ', f) for f in fields))
