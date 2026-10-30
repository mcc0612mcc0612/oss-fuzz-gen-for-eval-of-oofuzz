# OSS-Fuzz-gen runs for the OOFuzzer comparison

Scripts that set up and ran this fork of OSS-Fuzz-gen as a baseline against
HarnessGenerator (`~/HarnessGenerator`). They expect the OSS-Fuzz checkout at
`~/ofg-oss-fuzz`, with each project's Dockerfile pinned to the commit
HarnessGenerator uses, and write benchmarks to `benchmark-sets/oofuzz-eval/`.

- `build_all.sh` builds each project once with ASan, one at a time, records
  its peak build memory, and copies the built `/src` headers and archives out.
- `candidates.py` lists target candidates for one fuzzer: strong symbols of
  the fuzzer or the static archives it links, public, declared in a project
  header outside tests and third_party, not inline and not in an anonymous
  namespace.
- `prepare.py` picks the fuzz target to rewrite and up to 25 targets
  (HarnessGenerator's functions first, then the rest, largest first, at most
  6 per class) and writes the benchmark YAML with `gen_yaml.py`.
- `run_queue.sh` runs OSS-Fuzz-gen per project with Claude Opus 5.5, OFG
  defaults plus `-mr 5 -to 1800`, sized by memory, and cleans up after each.
- `janitor.sh` removes the stopped containers the runs leave behind.

Paths in the scripts point at the session scratchpad they ran from.
