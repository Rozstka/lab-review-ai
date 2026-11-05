from pathlib import Path
import io
import pytest
from fastapi.testclient import TestClient
from PIL import Image
from app.main import create_app
from app.extraction import parse, extract
from app.core import ProviderError

TEXT = Path('examples/synthetic-lab.txt').read_text()
def ingest(client, text=TEXT):
    response = client.post('/documents/text', json={'patient_id': 'SYN-001', 'text': text, 'synthetic': True})
    assert response.status_code == 201
    return response.json()

def test_review_gate_and_audit(client):
    doc = ingest(client)
    assert len(doc['measurements']) == 3
    assert client.get('/patients/SYN-001/records').json() == []
    body = {'expected_revision': 1, 'decision': 'approve', 'note': 'Checked against synthetic source'}
    response = client.post('/documents/' + doc['id'] + '/review', json=body)
    assert response.status_code == 200
    assert response.json()['status'] == 'approved'
    assert len(client.get('/patients/SYN-001/records').json()) == 1
    assert client.post('/documents/' + doc['id'] + '/review', json=body).status_code == 409
    audit = client.get('/documents/' + doc['id'] + '/audit').json()
    assert len(audit) == 1 and audit[0]['before']['status'] == 'pending_review'

def test_bad_units_require_correction(client):
    doc = ingest(client, 'Glucose: 5.2 mg/L [3.9-6.1]')
    path = '/documents/' + doc['id'] + '/review'
    body = {'expected_revision': 1, 'decision': 'approve', 'note': 'Manual review'}
    assert client.post(path, json=body).status_code == 422
    fixed = doc['measurements']
    fixed[0]['unit'] = 'mmol/L'
    body['measurements'] = fixed
    assert client.post(path, json=body).status_code == 200

def test_rejection_and_stale_version(client):
    doc = ingest(client)
    path = '/documents/' + doc['id'] + '/review'
    body = {'expected_revision': 2, 'decision': 'reject', 'note': 'Wrong source'}
    assert client.post(path, json=body).status_code == 409
    body['expected_revision'] = 1
    assert client.post(path, json=body).json()['status'] == 'rejected'
    assert client.get('/patients/SYN-001/records').json() == []

def test_unparsed_requires_acknowledgement(client):
    doc = ingest(client, TEXT + '\nUnrecognized marker: 100')
    body = {'expected_revision': 1, 'decision': 'approve', 'note': 'Ignored unsupported marker after inspection'}
    path = '/documents/' + doc['id'] + '/review'
    assert client.post(path, json=body).status_code == 422
    body['acknowledge_unparsed'] = True
    assert client.post(path, json=body).status_code == 200

@pytest.mark.parametrize('text', ['Glucose: 5.2 mmol/L', '', 'WBC: 6 10^9/L [10-4]', 'Glucose: 5.2 mmol/L [3-6]\nGlucose: 5 mmol/L [3-6]'])
def test_invalid_or_ambiguous_cannot_approve(client, text):
    response = client.post('/documents/text', json={'patient_id': 'SYN-001', 'text': text, 'synthetic': True})
    if not text:
        assert response.status_code == 422
        return
    doc = response.json()
    body = {'expected_revision': 1, 'decision': 'approve', 'note': 'Review', 'acknowledge_unparsed': True}
    assert client.post('/documents/' + doc['id'] + '/review', json=body).status_code == 422

def test_decimal_comma():
    assert parse('Glucose: 5,2 mmol/L [3,9-6,1]')[0].value == 5.2

def test_invalid_and_large_upload(client):
    assert client.post('/documents/image', data={'patient_id': 'SYN-001'}, files={'file': ('bad.png', b'bad')}).status_code == 422
    assert client.post('/documents/image', data={'patient_id': 'SYN-001'}, files={'file': ('big.png', b'x' * 2_000_001)}).status_code == 413

def test_real_ocr(client):
    with Path('examples/synthetic-lab.png').open('rb') as f:
        r = client.post('/documents/image', data={'patient_id': 'SYN-001'}, files={'file': ('scan.png', f, 'image/png')})
    assert r.status_code == 201, r.text
    doc = r.json()
    assert {m['name']: m['value'] for m in doc['measurements']} == {'Hemoglobin': 145, 'Glucose': 5.2, 'WBC': 6.0}
    source = client.get('/documents/' + doc['id'] + '/source')
    assert source.content.startswith(b'\x89PNG')

def test_llm_hallucination_rejected():
    class Fake:
        def chat(self, *a, **kw):
            return {'content': '{"measurements":[{"name":"Glucose","value":999,"unit":"mmol/L","reference_low":3.9,"reference_high":6.1,"source_line":"Glucose: 5.2 mmol/L [3.9-6.1]"}]}'}
    with pytest.raises(ProviderError):
        extract(TEXT, Fake())

def test_provider_error_preserves_source_for_manual_review(settings):
    class Fake:
        def chat(self, *a, **kw):
            raise ProviderError('LLM unavailable')
    with TestClient(create_app(settings, Fake()), headers={'Authorization': 'Bearer ' + settings.api_token}) as c:
        response = c.post('/documents/text', json={'patient_id': 'SYN-001', 'text': TEXT, 'synthetic': True})
        assert response.status_code == 201
        doc = response.json()
        assert doc['extraction_status'] == 'needs_manual_entry'
        assert doc['measurements'] == [] and doc['text'] == TEXT
        assert c.post('/documents/' + doc['id'] + '/review', json={'expected_revision': 1, 'decision': 'approve', 'note': 'Review', 'acknowledge_unparsed': True}).status_code == 422
        assert c.post('/documents/' + doc['id'] + '/review', json={'expected_revision': 1, 'decision': 'approve', 'note': 'Manually transcribed from source', 'acknowledge_unparsed': True, 'measurements': [m.model_dump() for m in parse(TEXT)]}).status_code == 200


def test_patient_mismatch_rejected(client):
    assert client.post('/documents/text', json={'patient_id':'SYN-WRONG','text':TEXT,'synthetic':True}).status_code == 422
