"""分析 py-spy speedscope profile: 按完整调用栈聚合."""
import json
import sys
from collections import Counter

d = json.load(open(sys.argv[1]))
frames = d['shared']['frames']
prof = d['profiles'][0]
samples = prof['samples']
weights = prof.get('weights', [1] * len(samples))

total = sum(weights)
stacks = Counter()
for s, w in zip(samples, weights):
    if not s:
        continue
    names = []
    for fi in s:
        f = frames[fi]
        fn = (f.get('file') or '?').replace('\\', '/').split('/')[-1]
        names.append(f"{f['name']}({fn}:{f.get('line', '?')})")
    stacks[' <- '.join(names)] += w

print(f'total weight: {total:.1f}  ({len(samples)} samples)')
for st, w in stacks.most_common(12):
    print(f'{100 * w / total:5.1f}%  {st}')
