import requests, json
from pathlib import Path

files = [
    r'C:\Users\GRahu\.gemini\antigravity\brain\f53c3bc7-c3a9-40ad-9c23-5613bd3faa5e\broken_laptop_1782217924834.png',
    r'C:\Users\GRahu\.gemini\antigravity\brain\f53c3bc7-c3a9-40ad-9c23-5613bd3faa5e\bare_pcb_1782217939337.png',
    r'C:\Users\GRahu\.gemini\antigravity\brain\f53c3bc7-c3a9-40ad-9c23-5613bd3faa5e\coffee_mug_1782217951215.png'
]

for f in files:
    print(f'\n--- Testing {Path(f).name} ---')
    with open(f, 'rb') as img:
        resp = requests.post('http://127.0.0.1:8000/api/teardown', files={'images': img})
        print(f'Status: {resp.status_code}')
        if resp.status_code == 200:
            data = resp.json()
            parts = data.get('parts', [])
            print(f'Parts found: {len(parts)}')
        else:
            print(f'Error: {resp.text}')
