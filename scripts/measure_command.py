#!/usr/bin/env python3
"""Measure one artifact command and bind timing/RAM/disk to its JSON evidence.

Use --evidence path/to/result.json --output timing.json -- COMMAND ARGS...
The child command's result JSON supplies the actual input checksum. No database
is opened by this wrapper. Exit status is preserved and failed runs are marked.
"""
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import platform
import resource
import shutil
import subprocess
import time


def main():
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument('--evidence', type=Path, required=True)
    cli.add_argument('--output', type=Path, required=True)
    cli.add_argument('command', nargs=argparse.REMAINDER)
    args = cli.parse_args()
    command = args.command[1:] if args.command[:1] == ['--'] else args.command
    if not command:
        cli.error('Supply a command after --')
    before = shutil.disk_usage('.')
    started = time.monotonic()
    completed = subprocess.run(command)
    elapsed = time.monotonic() - started
    raw = resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss
    after = shutil.disk_usage('.')
    origin = json.loads(args.evidence.read_text())['provenance'] if completed.returncode == 0 else {}
    result = {'provenance': {**origin, 'generated_at': datetime.now(timezone.utc).isoformat(),
                             'measurement_script': Path(__file__).name,
                             'measurement_script_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest()},
              'results': {'command': command, 'returncode': completed.returncode,
                          'wall_seconds': elapsed,
                          'peak_rss_bytes': raw if platform.system() == 'Darwin' else raw * 1024,
                          'free_bytes_before': before.free, 'free_bytes_after': after.free,
                          'host': {'system': platform.system(), 'machine': platform.machine(),
                                   'python': platform.python_version()},
                          'scope': 'Child process wall time and peak RSS, including startup, checksum and outputs; one run, no cache flushing',
                          'result_evidence_sha256': hashlib.sha256(args.evidence.read_bytes()).hexdigest() if completed.returncode == 0 else None}}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2, sort_keys=True) + '\n')
    raise SystemExit(completed.returncode)


if __name__ == '__main__':
    main()
