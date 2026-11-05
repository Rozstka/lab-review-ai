import os
from pathlib import Path
import httpx
base = os.getenv('API_URL', 'http://127.0.0.1:8000')
with httpx.Client(base_url=base, headers={'Authorization': 'Bearer ' + os.environ['API_TOKEN']}, timeout=90) as c:
    with Path('examples/synthetic-lab.png').open('rb') as scan:
        r = c.post('/documents/image', data={'patient_id': 'SYN-001'}, files={'file': ('scan.png', scan, 'image/png')})
    r.raise_for_status()
    doc = r.json()
    print('OCR text:', doc['text'])
    print('Review required:', doc['id'], doc['issues'])
    print('Approved records before review:', len(c.get('/patients/SYN-001/records').json()))
    print('Open /docs, inspect original and extracted fields, then explicitly approve or reject.')
