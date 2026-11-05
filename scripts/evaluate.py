"""Small synthetic OCR regression set; not a clinical accuracy benchmark."""
import io
import json
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from PIL import Image, ImageDraw, ImageFont
from app.extraction import ocr, extract

cases = []
expected_count = correct = extracted_count = 0
for i, (hemoglobin, glucose, wbc) in enumerate([(145,5.2,6.0),(130,4.1,7.2),(160,6.0,8.1),(120,3.9,4.0)]):
    text = f'SYNTHETIC LAB REPORT\nPatient: SYN-{i}\nHemoglobin: {hemoglobin} g/L [120-170]\nGlucose: {glucose} mmol/L [3.9-6.1]\nWBC: {wbc} 10^9/L [4.0-10.0]'
    image = Image.new('RGB', (1500,450), 'white')
    ImageDraw.Draw(image).multiline_text((40,40), text, font=ImageFont.load_default(size=32), fill='black', spacing=20)
    raw=io.BytesIO(); image.save(raw,format='PNG')
    recognized=ocr(raw.getvalue())
    measurements, issues, unparsed=extract(recognized)
    expected={('Hemoglobin',float(hemoglobin),'g/L'),('Glucose',glucose,'mmol/L'),('WBC',wbc,'10^9/L')}
    actual={(m.name,m.value,m.unit) for m in measurements}
    expected_count+=len(expected); extracted_count+=len(actual); correct+=len(expected & actual)
    cases.append({'case':i,'expected_fields':len(expected),'correct_fields':len(expected & actual),'validation_issues':issues,'unparsed':unparsed})
result={'scope':'4 clean synthetic printed scans; no clinical generalization', 'expected_fields':expected_count,
        'precision':correct/extracted_count if extracted_count else 0, 'recall':correct/expected_count, 'cases':cases}
Path('reports').mkdir(exist_ok=True)
Path('reports/ocr-evaluation.json').write_text(json.dumps(result,indent=2)+'\n')
print(json.dumps(result,indent=2))
