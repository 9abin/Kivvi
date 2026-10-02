from pathlib import Path
import json,re
p=Path(__file__).parent/'검토자료'; names=json.loads((p/'대상목록.json').read_text(encoding='utf8'))
for n in names:
 t=(p/(n+'.txt')).read_text(encoding='utf8')
 refs=sorted(set(re.findall(r'[「⌜“"]([^」”"\n]{2,90})[」”"]',t)))
 print(n+': '+ ' / '.join(s for s in refs if re.search(r'법|규정|지침|기준|강령|정관|세칙|예규|규칙',s)))
