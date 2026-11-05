import json,time,sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from fastapi.testclient import TestClient
from app.core import Settings
from app.main import create_app
settings=Settings(database_url='sqlite:///:memory:',api_token='test-only-image-llm-smoke-token',llm_mode='ollama',ollama_model='qwen2.5:3b')
start=time.time()
with TestClient(create_app(settings),headers={'Authorization':'Bearer '+settings.api_token}) as c:
 r=c.post('/documents/image',data={'patient_id':'SYN-001'},files={'file':('scan.png',Path('examples/synthetic-lab.png').read_bytes(),'image/png')})
 report={'pipeline':'image → Tesseract → qwen2.5:3b → validation','status_code':r.status_code,'seconds':round(time.time()-start,2),'result':r.json()}
 Path('reports/image-llm-smoke.json').write_text(json.dumps(report,indent=2)+'\n')
 print(json.dumps(report,indent=2))
 r.raise_for_status()
 assert r.json()['status']=='pending_review'
