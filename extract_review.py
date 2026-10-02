from pathlib import Path
import struct, zlib, re, zipfile, xml.etree.ElementTree as ET, json
BASE=Path(__file__).parent
def hwp(p):
 b=p.read_bytes(); ss=1<<struct.unpack_from('<H',b,30)[0]; ms=1<<struct.unpack_from('<H',b,32)[0]
 def sec(i):return b[(i+1)*ss:(i+2)*ss]
 dif=list(struct.unpack_from('<109I',b,76)); nxt=struct.unpack_from('<I',b,68)[0]
 for _ in range(struct.unpack_from('<I',b,72)[0]):
  vals=struct.unpack('<%dI'%(ss//4),sec(nxt));dif+=vals[:-1];nxt=vals[-1]
 fat=[]
 for i in dif:
  if i<0xfffffffa:fat+=struct.unpack('<%dI'%(ss//4),sec(i))
 def chain(i, table=fat):
  out=[];seen=set()
  while i<0xfffffffa and i not in seen:
   seen.add(i);out.append(i);i=table[i]
  return out
 directory=b''.join(sec(i) for i in chain(struct.unpack_from('<I',b,48)[0]));entries=[]
 for k in range(0,len(directory),128):
  d=directory[k:k+128];n=struct.unpack_from('<H',d,64)[0]
  if n>=2:entries.append((d[:n-2].decode('utf-16le'),d[66],struct.unpack_from('<I',d,116)[0],struct.unpack_from('<Q',d,120)[0]))
 root=next(e for e in entries if e[1]==5); mini=b''.join(sec(i) for i in chain(root[2]))[:root[3]]
 mfraw=b''.join(sec(i) for i in chain(struct.unpack_from('<I',b,60)[0]));mf=list(struct.unpack('<%dI'%(len(mfraw)//4),mfraw))
 def stream(e):
  if e[3]<4096:return b''.join(mini[i*ms:(i+1)*ms] for i in chain(e[2],mf))[:e[3]]
  return b''.join(sec(i) for i in chain(e[2]))[:e[3]]
 header=stream(next(e for e in entries if e[0]=='FileHeader'));compressed=struct.unpack_from('<I',header,36)[0]&1
 lines=[]
 for e in sorted((e for e in entries if re.fullmatch(r'Section\d+',e[0])),key=lambda e:int(e[0][7:])):
  data=stream(e)
  if compressed:data=zlib.decompress(data,-15)
  pos=0
  while pos+4<=len(data):
   v=struct.unpack_from('<I',data,pos)[0];pos+=4;tag=v&1023;size=v>>20
   if size==4095:size=struct.unpack_from('<I',data,pos)[0];pos+=4
   raw=data[pos:pos+size];pos+=size
   if tag==67:
    units=list(struct.unpack('<%dH'%(len(raw)//2),raw));out=[];j=0
    while j<len(units):
     c=units[j]
     if c in (1,2,3,11,12,14,15,16,17,18,19,20,21,22,23):j+=8;continue
     if c>=32:out.append(c)
     elif c in (9,10,13):out.append(32)
     j+=1
    s=struct.pack('<%dH'%len(out),*out).decode('utf-16le',errors='replace')
    if s.strip():lines.append(s)
 return '\n'.join(lines)
def extract(p):
 if p.suffix=='.hwp':return hwp(p)
 with zipfile.ZipFile(p) as z:
  lines=[]
  for name in sorted(z.namelist()):
   if re.fullmatch(r'Contents/section\d+.xml',name):
    tree=ET.fromstring(z.read(name))
    for el in tree.iter():
     if el.tag.endswith('}p'):
      s=''.join(t.text or '' for t in el.iter() if t.tag.endswith('}t'))
      if s:lines.append(s)
  return '\n'.join(lines)
out=BASE/'검토자료';out.mkdir(exist_ok=True)
files=sorted((p for p in (BASE/'한국벤처투자_내부규정').glob('*/*') if p.suffix in ('.hwp','.hwpx')),key=lambda p:p.parent.name)
selected=[p for p in files if '감사규정'<=p.parent.name.split('_')[0]<='벤처투자회사 등에 관한 행정자료 보관·관리 지침']
for p in files:
 name=p.parent.name.rsplit('_',1)[0]
 (out/(name+'.txt')).write_text(extract(p),encoding='utf-8')
(out/'대상목록.json').write_text(json.dumps([p.parent.name.rsplit('_',1)[0] for p in selected],ensure_ascii=False,indent=2),encoding='utf-8')
print(len(selected));print('\n'.join(p.parent.name for p in selected))
