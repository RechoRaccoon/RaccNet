import re

with open('raccnet_server.py','r',encoding='utf-8') as f:
    content = f.read()

m = re.search(r'HTML = r"""(.*?)"""', content, re.DOTALL)
js = m.group(1)
js_m = re.search(r'<script>(.*?)</script>', js, re.DOTALL)
script = js_m.group(1)

paren = 0
bracket = 0
brace = 0
in_str = None

lines = script.split('\n')
paren_issues = []
bracket_issues = []

for lineno, line in enumerate(lines, 1):
    i = 0
    while i < len(line):
        ch = line[i]
        nch = line[i+1] if i+1 < len(line) else ''

        if in_str:
            if ch == chr(92):  # backslash
                i += 2
                continue
            if ch == in_str:
                in_str = None
        elif ch in ('"', "'", '`'):
            in_str = ch
        elif ch == '/' and nch == '/':
            break  # line comment
        else:
            if ch == '(':     paren += 1
            elif ch == ')':   paren -= 1
            elif ch == '[':   bracket += 1
            elif ch == ']':   bracket -= 1
            elif ch == '{':   brace += 1
            elif ch == '}':   brace -= 1
        i += 1

    if paren < -1:
        paren_issues.append((lineno, paren, line[:100]))
    if bracket < -1:
        bracket_issues.append((lineno, bracket, line[:100]))

print(f'Final: paren={paren}, bracket={bracket}, brace={brace}')
print(f'\nLines where paren went deeply negative (first 20):')
for l,v,txt in paren_issues[:20]:
    print(f'  L{l} p={v}: {txt}')
print(f'\nLines where bracket went deeply negative (first 20):')
for l,v,txt in bracket_issues[:20]:
    print(f'  L{l} b={v}: {txt}')
