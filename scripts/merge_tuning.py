"""Merge multiple tuning JSONs into one for filter_strategies.py."""
import json
import sys
from pathlib import Path

if len(sys.argv) < 2:
    print("Usage: python merge_tuning.py file1.json file2.json ... [output.json]")
    sys.exit(1)

files = sys.argv[1:-1] if sys.argv[-1].endswith('.json') and not Path(sys.argv[-1]).exists() else (sys.argv[1:-1] if len(sys.argv) > 2 else sys.argv[1:])
output = sys.argv[-1] if sys.argv[-1].endswith('.json') else 'data/tuning_results/tuning_merged.json'

all_results = []
configs = []
for f in files:
    with open(f, 'r', encoding='utf-8') as fp:
        d = json.load(fp)
    all_results.extend(d.get('results', []))
    configs.append(d.get('config', {}))

merged = {
    'config': {'merged_from': files, 'tags': [c.get('tag', '?') for c in configs]},
    'summary': {'total_results': len(all_results), 'sources': len(files)},
    'results': all_results,
}

with open(output, 'w', encoding='utf-8') as f:
    json.dump(merged, f, indent=2, ensure_ascii=False)

print(f"Merged {len(all_results)} results from {len(files)} files -> {output}")
print("By symbol/interval:")
from collections import Counter
c = Counter((r.get('symbol', '?'), r.get('interval', '?')) for r in all_results)
for k, v in sorted(c.items()):
    print(f"  {k[0]:<10} {k[1]:<4}: {v} strategies")
