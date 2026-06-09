import os

for root, _, files in os.walk('.'):
    for file in files:
        if file.endswith('.py'):
            path = os.path.join(root, file)
            with open(path, 'r', encoding='utf-8') as f:
                content = f.read()
            if 'f"' in content:
                print(f"Fixing {path}")
                content = content.replace('f"', 'f"')
                with open(path, 'w', encoding='utf-8') as f:
                    f.write(content)
