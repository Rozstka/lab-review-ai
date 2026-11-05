from contextlib import asynccontextmanager
from datetime import datetime, timezone
import hashlib
import re
import uuid
from fastapi import Depends, FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import Response
from sqlalchemy import Column, Integer, JSON, LargeBinary, MetaData, String, Table, Text, insert, select, update, text as sqltext
from .core import Settings, auth, database, Ollama, ProviderError, BodyLimitMiddleware
from .schemas import TextDocument, Review, Measurement
from .extraction import extract, validate, ocr

metadata = MetaData()
documents = Table('lab_documents', metadata,
    Column('id', String, primary_key=True), Column('patient_id', String, nullable=False),
    Column('status', String, nullable=False), Column('revision', Integer, nullable=False),
    Column('text', Text, nullable=False), Column('measurements', JSON, nullable=False),
    Column('issues', JSON, nullable=False), Column('unparsed', JSON, nullable=False),
    Column('source', LargeBinary), Column('source_type', String), Column('source_sha256', String),
    Column('extraction_status', String, nullable=False), Column('mode', String, nullable=False), Column('created_at', String, nullable=False))
audits = Table('lab_audits', metadata, Column('id', String, primary_key=True),
    Column('document_id', String, nullable=False), Column('actor', String, nullable=False),
    Column('decision', String, nullable=False), Column('note', Text, nullable=False),
    Column('before', JSON, nullable=False), Column('after', JSON, nullable=False), Column('at', String, nullable=False))

def public(row):
    return {k: v for k, v in dict(row).items() if k not in ('source', 'source_type')}

def create_app(settings=None, provider=None):
    settings = settings or Settings.env()
    engine = database(settings.database_url)
    require = auth(settings)
    llm = provider or (Ollama(settings) if settings.llm_mode == 'ollama' else None)

    @asynccontextmanager
    async def lifespan(app):
        settings.validate()
        metadata.create_all(engine)
        try:
            yield
        finally:
            engine.dispose()

    app = FastAPI(title='Lab Review AI — synthetic data only', lifespan=lifespan)
    app.add_middleware(BodyLimitMiddleware)
    app.state.engine = engine

    @app.get('/health')
    def health():
        with engine.connect() as conn:
            conn.execute(sqltext('SELECT 1'))
        return {'status': 'ok', 'mode': settings.llm_mode, 'synthetic_only': True}

    def save(payload, source=None, source_type=None):
        declared = re.search(r'^Patient:\s*(\S+)\s*$', payload.text, re.MULTILINE)
        if declared and declared.group(1) != payload.patient_id:
            raise HTTPException(422, 'Patient identifier does not match the source')
        extraction_status = 'ok'
        try:
            values, issues, unparsed = extract(payload.text, llm)
        except ProviderError as exc:
            # Preserve the source for human recovery; never silently invent or substitute values.
            values, issues = [], ['LLM extraction failed: ' + str(exc)]
            unparsed = [line.strip() for line in payload.text.splitlines() if line.strip()]
            extraction_status = 'needs_manual_entry'
        row = dict(id=str(uuid.uuid4()), patient_id=payload.patient_id, status='pending_review', revision=1,
            text=payload.text, measurements=[v.model_dump() for v in values], issues=issues,
            unparsed=unparsed, source=source, source_type=source_type,
            source_sha256=hashlib.sha256(source or payload.text.encode()).hexdigest(),
            mode=settings.llm_mode, extraction_status=extraction_status, created_at=datetime.now(timezone.utc).isoformat())
        with engine.begin() as conn:
            conn.execute(insert(documents).values(**row))
        return public(row)

    @app.post('/documents/text', status_code=201, dependencies=[Depends(require)])
    def ingest_text(payload: TextDocument):
        return save(payload)

    @app.post('/documents/image', status_code=201, dependencies=[Depends(require)])
    def ingest_image(patient_id: str = Form(pattern=r'^SYN-[A-Za-z0-9-]{1,40}$'), file: UploadFile = File()):
        data = file.file.read(2_000_001)
        if len(data) > 2_000_000:
            raise HTTPException(413, 'Image exceeds 2 MB')
        try:
            recognized = ocr(data)
            if not recognized or len(recognized) > 20000:
                raise ValueError('OCR returned empty or excessive text')
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        except (RuntimeError, FileNotFoundError) as exc:
            raise HTTPException(503, 'OCR unavailable or timed out') from exc
        media = 'image/png' if data.startswith(b'\x89PNG') else 'image/jpeg'
        return save(TextDocument(patient_id=patient_id, text=recognized, synthetic=True), data, media)

    @app.get('/documents/{document_id}', dependencies=[Depends(require)])
    def get_document(document_id: str):
        with engine.connect() as conn:
            row = conn.execute(select(documents).where(documents.c.id == document_id)).mappings().first()
        if row is None:
            raise HTTPException(404, 'Document not found')
        return public(row)

    @app.get('/documents/{document_id}/source', dependencies=[Depends(require)])
    def source(document_id: str):
        with engine.connect() as conn:
            row = conn.execute(select(documents).where(documents.c.id == document_id)).mappings().first()
        if row is None:
            raise HTTPException(404, 'Document not found')
        return Response(row['source'] or row['text'], media_type=row['source_type'] or 'text/plain', headers={'X-Content-Type-Options': 'nosniff'})

    @app.post('/documents/{document_id}/review')
    def review(document_id: str, payload: Review, actor=Depends(require)):
        with engine.begin() as conn:
            row = conn.execute(select(documents).where(documents.c.id == document_id)).mappings().first()
            if row is None:
                raise HTTPException(404, 'Document not found')
            if row['status'] != 'pending_review' or row['revision'] != payload.expected_revision:
                raise HTTPException(409, 'Already reviewed or revision changed')
            if payload.decision == 'approve' and row['unparsed'] and not payload.acknowledge_unparsed:
                raise HTTPException(422, 'Explicitly acknowledge every unparsed source line')
            measurements = payload.measurements if payload.measurements is not None else [Measurement(**v) for v in row['measurements']]
            issues = validate(measurements, row['text'])
            if payload.decision == 'approve' and issues:
                raise HTTPException(422, {'message': 'Resolve validation issues before approval', 'issues': issues})
            new = dict(status='approved' if payload.decision == 'approve' else 'rejected',
                       extraction_status='human_corrected' if payload.decision == 'approve' and payload.measurements is not None else row['extraction_status'],
                       revision=row['revision'] + 1, measurements=[m.model_dump() for m in measurements], issues=issues)
            result = conn.execute(update(documents).where(documents.c.id == document_id,
                documents.c.revision == payload.expected_revision, documents.c.status == 'pending_review').values(**new))
            if result.rowcount != 1:
                raise HTTPException(409, 'Concurrent review detected')
            conn.execute(insert(audits).values(id=str(uuid.uuid4()), document_id=document_id, actor=actor,
                decision=payload.decision, note=payload.note, before=public(row), after={**new, "acknowledge_unparsed": payload.acknowledge_unparsed},
                at=datetime.now(timezone.utc).isoformat()))
            return {**public(row), **new}

    @app.get('/documents/{document_id}/audit', dependencies=[Depends(require)])
    def audit(document_id: str):
        with engine.connect() as conn:
            return [dict(r) for r in conn.execute(select(audits).where(audits.c.document_id == document_id)).mappings()]

    @app.get('/patients/{patient_id}/records', dependencies=[Depends(require)])
    def records(patient_id: str):
        with engine.connect() as conn:
            return [public(r) for r in conn.execute(select(documents).where(documents.c.patient_id == patient_id,
                documents.c.status == 'approved')).mappings()]

    return app

app = create_app()
