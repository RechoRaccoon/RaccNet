import re

with open('raccnet_page.html','r',encoding='utf-8',errors='replace') as f:
    html = f.read()

start = html.find('<script>\n// Apply')
end = html.find('</script>', start)
js = html[start+8:end]
lines = js.split('\n')

# Track brace depth per line (simplified - ignores strings for speed)
depths = []
brace = 0
for line in lines:
    for ch in line:
        if ch == '{': brace += 1
        elif ch == '}': brace -= 1
    depths.append(brace)

# Find all const/let with their line number and brace depth
decls = {}
for i, line in enumerate(lines):
    for m in re.finditer(r'\b(const|let)\s+(\w+)\b', line):
        name = m.group(2)
        depth = depths[i]
        if name not in decls:
            decls[name] = []
        decls[name].append((i+1, depth))

# Report names declared at the same depth more than once, within 500 lines
found = False
for name, occurrences in sorted(decls.items()):
    by_depth = {}
    for lineno, depth in occurrences:
        if depth not in by_depth:
            by_depth[depth] = []
        by_depth[depth].append(lineno)

    for depth, linenos in by_depth.items():
        if len(linenos) > 1:
            for j in range(len(linenos)-1):
                if linenos[j+1] - linenos[j] < 500:
                    print(f'SAME-SCOPE DUP: {name!r} at lines {linenos[j]} and {linenos[j+1]} (depth={depth})')
                    print(f'  L{linenos[j]}: {lines[linenos[j]-1].strip()[:80]}')
                    print(f'  L{linenos[j+1]}: {lines[linenos[j+1]-1].strip()[:80]}')
                    found = True

if not found:
    print('No same-scope duplicates found.')
