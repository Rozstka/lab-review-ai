from pathlib import Path
from PIL import Image, ImageDraw, ImageFont
text = Path('examples/synthetic-lab.txt').read_text()
img = Image.new('RGB', (1500, 450), 'white')
draw = ImageDraw.Draw(img)
try:
    font = ImageFont.truetype('DejaVuSans.ttf', 32)
except OSError:
    font = ImageFont.load_default(size=32)
draw.multiline_text((40, 40), text, font=font, fill='black', spacing=20)
img.save('examples/synthetic-lab.png')
print('Created examples/synthetic-lab.png')
