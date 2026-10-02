from pathlib import Path
import re,json,csv,html,collections
B=Path(__file__).parent; D=B/'검토자료'
names=json.loads((D/'대상목록.json').read_text(encoding='utf8'))
texts={p.stem:p.read_text(encoding='utf8') for p in D.glob('*.txt')}
norm=lambda s:re.sub(r'\s+','',s).replace('ㆍ','·')
internal={norm(n):n for n in texts}
extras='''감사기준시행세칙
리스크관리규정
상벌규정
민원사무처리규정
민원사무 처리요령
수수료 및 수당지급규정
개인정보처리 내부지침
공무직 관리규정
근무평정시행세칙
제규정관리규정
국가정보원 방첩업무규정
중소벤처기업부 방첩업무규정 시행지침
행정업무의 효율적 운영에 관한 규정
행정 효율과 협업 촉진에 관한 규정
공공기록물 관리에 관한 법률 시행령
벤처투자 촉진에 관한 법률 시행령
공공기관의 운영에 관한 법률
국가를 당사자로 하는 계약에 관한 법률
근로기준법
변호사법
근로자참여 및 협력증진에 관한 법률
남녀고용평등과 일ㆍ가정 양립 지원에 관한 법률
물품관리법
공공기록물 관리에 관한 법률
개인정보 보호법
개인정보 보호법률
개인정보 보호법 시행령
공익신고자 보호법
특정범죄신고자 등 보호법
부패방지 및 국민권익위원회의 설치와 운영에 관한 법률
공공기관의 정보공개에 관한 법률
민원 처리에 관한 법률
개인정보의 안전성 확보조치 기준
개인정보 안전성 확보조치 기준 고시
표준 개인정보 보호지침
개인정보 보호지침
개인정보파일 관리업무 지침서
국가정보보안기본지침
공공기관의 통합공시에 관한 기준
공공기관의 혁신에 관한 지침
공공기관 통합공시 매뉴얼
전자문서 작성지침
고등교육법
국가재정법
별정우체국법
사립학교법
지방공기업법
지방자치단체 기금관리기본법
지방재정법'''.splitlines()
unprovided=set(extras[:10])
titlemap=dict(internal)
for x in extras:titlemap[norm(x)]=x
titlemap['정관']='한국벤처투자 정관'
aliases={
'감사지침':{'규정':'감사규정'},
'개인정보 보호 및 관리지침':{'법':'개인정보 보호법','개보법':'개인정보 보호법','시행령':'개인정보 보호법 시행령'},
'개인정보 침해사고 대응 및 복구지침':{'법':'개인정보 보호법','시행령':'개인정보 보호법 시행령'},
'공익신고 처리 및 신고자 보호 등에 관한 운영지침':{'법':'공익신고자 보호법','시행령':'공익신고자 보호법 시행령'},
'기록물관리규정':{'공공기록물법':'공공기록물 관리에 관한 법률','공공기록물법 시행령':'공공기록물 관리에 관한 법률 시행령'},
'민원 처리지침':{'법':'민원 처리에 관한 법률','민원처리법':'민원 처리에 관한 법률','시행령':'민원 처리에 관한 법률 시행령'},
'방첩업무 시행지침':{'규정':'국가정보원 방첩업무규정','지침':'중소벤처기업부 방첩업무규정 시행지침'},
'벤처투자회사 등에 관한 행정자료 보관·관리 지침':{'법':'벤처투자 촉진에 관한 법률〔약칭 미정의〕','시행령':'벤처투자 촉진에 관한 법률 시행령'},
'계약사무처리지침':{'법':'국가를 당사자로 하는 계약에 관한 법률','시행령':'국가를 당사자로 하는 계약에 관한 법률 시행령'},
'노사협의회규정':{'동법시행령':'근로자참여 및 협력증진에 관한 법률 시행령','동법시행규칙':'근로자참여 및 협력증진에 관한 법률 시행규칙'},
}
pat=lambda s:r'\s*'.join('[ㆍ·]' if c=='·' else re.escape(c) for c in norm(s))
title_re=re.compile('|'.join(pat(s) for s in sorted(titlemap,key=len,reverse=True)))
article=re.compile(r'제\s*\d+\s*조(?:\s*의\s*\d+)?(?:\s*제?\s*\d+\s*항)?(?:\s*제\s*\d+\s*호)?')
ref_re=re.compile(r'제\s*\d+\s*조(?:\s*의\s*\d+)?(?:\s*제?\s*\d+\s*항)?(?:\s*제\s*\d+\s*호)?|제\s*\d+\s*장|별표\s*(?:제\s*)?\d*|별지\s*(?:제\s*)?\d+\s*(?:호\s*)?(?:서식)?')
issues=list(csv.DictReader((B/'23개_내부규정_검토목록.csv').open(encoding='utf-8-sig')))
issues=[r for r in issues if r['유형'] in ('인용','법령 정합성','규정 정합성')]
rows=[]
def append(doc,loc,line,raw,target,ref,kind,source='明示'):
 relevant=[r for r in issues if r['문서']==doc and str(line) in r['근거행'].split(',')]
 if target in texts:
  check='対象文書あり・内容未照合'
  if re.match(r'제\d+조',norm(ref)):
   jo=re.match(r'제\d+조(?:의\d+)?',norm(ref))[0]
   found=re.search(r'^\s*(?:楴䵴)?'+pat(jo)+r'\s*(?:[（(]|<삭제)',texts[target],re.M)
   check='조번호 존재·항호/내용 확인 필요' if found else '조번호 검색 미발견·원문 확인 필요'
  else:check='대상 문서 있음·적용 내용 확인 필요'
 elif kind=='자기 문서':check='자기 문서 내 연결·본문/서식 대조 필요'
 elif kind=='외부 근거':check='외부 원문·적용 기준일 대조 필요'
 elif kind=='포괄 근거':check='적용 법령·내규의 구체적 대상 확인 필요'
 else:check='폴더 미수록·내규대장/승인본 확인 필요'
 if relevant:check=' / '.join(sorted(set(r['판정'] for r in relevant)))+' — 기존 검토 연계'
 rows.append({'문서':doc,'인용위치':loc,'근거행':str(line),'구분':kind,'인용대상':target,'대상조문·서식':ref or '문서 전체/조문 특정 없음','관계':source,'확인상태':check,'기존검토ID':','.join(r['ID'] for r in relevant),'정비·확인방법':' / '.join(r['해결방법'] for r in relevant) or ('번호 존재 확인과 별도로 항·호·내용·시행일을 대조하고 개정 시 연결 인용을 함께 점검. 削除・移動の履歴注記は当時の条文で判断。'),'원문':raw.strip()})
for doc in names:
 loc='머리말'; a=aliases.get(doc,{})
 ar=re.compile('|'.join(pat(s) for s in sorted(a,key=len,reverse=True))) if a else None
 for ln,s in enumerate(texts[doc].splitlines(),1):
  stripped=s.strip()
  heading=re.match(r'^\s*(?:楴䵴)?(제\s*\d+\s*조(?:\s*의\s*\d+)?)\s*(?:[（(]([^）)]+)[）)]|(<삭제[^>]*>))',s)
  is_attachment=bool(re.match(r'^\s*[\[<【]?\s*(별표|별지)\s*(?:제\s*)?\d+',s)) and len(stripped)<100
  if heading:loc=norm(heading[1])+'('+str(heading[2] or heading[3])+')'
  if re.match(r'^부\s*칙',stripped):loc=stripped
  if is_attachment:loc=stripped
  if loc=='머리말' or is_attachment or re.match(r'^\s*(?:楴䵴)?제\s*\d+\s*장',s):continue
  matches=[]
  for m in title_re.finditer(s):
   target=titlemap[norm(m[0])]
   if target==doc and heading and m.start()<heading.end()+8:continue
   matches.append((m.start(),m.end(),target,'명시 문서명'))
  # 약칭은 조문 인용 바로 앞에 오는 경우와 이하 정의문에서만 해석한다.
  if ar:
   for m in ar.finditer(s):
    if any(x<=m.start()<y for x,y,_,_ in matches):continue
    following=s[m.end():].lstrip(' 」”"\'()')
    if ref_re.match(following) or re.match(r'(?:및|에서|에\s*따|에\s*정|의\s*규정)',following) or m[0].startswith('동법') or '이라 한다' in s[m.end():m.end()+20] or '라고 한다' in s[m.end():m.end()+20]:
     target=next(v for k,v in a.items() if norm(k)==norm(m[0])); matches.append((m.start(),m.end(),target,'약칭 해석'))
  matches.sort()
  refs=[m for m in ref_re.finditer(s) if not (heading and m.start()<heading.end())]
  used=set()
  for m in refs:
   # 개정 이력의 본조 이동은 실체 인용과 구분하여 그대로 표시.
   preceding=[x for x in matches if x[1]<=m.start() and m.start()-x[1]<32]
   last=max(preceding,key=lambda x:x[1]) if preceding else None
   if last and re.search(r'[。.;；]|따라|따른|말한다',s[last[1]:m.start()]):last=None
   target=last[2] if last else doc
   if not last and '같은 법' in s[max(0,m.start()-12):m.start()] and matches:target=matches[0][2]
   # 앞 인용의 범위표시(부터/및/또는)인 경우 같은 대상 유지.
   prior=[(r,t) for r,t in used if r<m.start()]
   if not last and prior:
    end,t=max(prior)
    if re.fullmatch(r'\s*(?:부터|내지|및|또는|,|까지의?|[」”])\s*',s[end:m.start()]):target=t
   used.add((m.end(),target))
   kind='자기 문서' if target==doc else ('다른 내규' if target in texts or target in unprovided else '외부 근거')
   append(doc,loc,ln,s,target,m[0],kind,'약칭 해석' if last and last[3]=='약칭 해석' else '조문·별표·서식 인용')
  for start,end,target,how in matches:
   if any(t==target for _,t in used):continue
   if target==doc:continue
   if not refs and not re.search(r'따라|따른|정한|정하는|준용|의하|위반|관련|근거|경우|사항|폐지|개정|규정상|제정|시행|내규|법률|규정」',s):continue
   kind='다른 내규' if target in texts or target in unprovided else '외부 근거'
   append(doc,loc,ln,s,target,'',kind,how+' / 포괄 준용·참조')
  # 법령명·내규명을 특정하지 않은 준용을 별도로 보존한다.
  if not matches and re.search(r'관계\s*법령|관련\s*법령|다른\s*규정|관련\s*규정|관할.*법령|규정\s*등이',s) and re.search(r'따른|따라|준수|의한|의하여|정하는|준용|우선|제외',s):
   append(doc,loc,ln,s,'특정하지 않은 관계 법령·내규','','포괄 근거','적용범위·우선관계 / 개별 대상 미특정')
 # 문서명 축약 병렬 인용을 보완한다.
 if doc.startswith('글로벌'):
  s=texts[doc].splitlines()[8]
  append(doc,'제2조제2항',9,s,'자금세탁행위 방지를 위한 업무지침','','다른 내규','“업무규정ㆍ업무지침” 병렬 인용 보완')

# 개인정보 본문 반복도 근거행을 보존하되 완전히 같은 인용은 한 행으로 묶는다.
merged={}
for r in rows:
 k=tuple(r[x] for x in ('문서','인용위치','인용대상','대상조문·서식','원문'))
 if k in merged:
  merged[k]['근거행']+=','+r['근거행']
 else:merged[k]=r
rows=list(merged.values())
for i,r in enumerate(rows,1):r['ID']=f'C{i:04}'
for r in rows:
 r['정비·확인방법']=r['정비·확인방법'].replace('削除・移動の履歴注記は当時の条文で判断。','삭제·이동의 이력 주석은 당시 조문으로 판단.')
 r['확인상태']=r['확인상태'].replace('対象文書あり・内容未照合','대상 문서 있음·내용 미대조')
 if r['문서']=='내규관리규정' and r['인용위치'].startswith('제16조') and r['대상조문·서식']=='제1조의2':
  r['관계']='가지 조번호 작성 예시';r['확인상태']='작성 예시·실제 적용 조문 인용 아님'
 if re.search(r'제\d+조의\d+',r['대상조문·서식']) and '부' in r['인용위치'] and '칙' in r['인용위치']:
  r['확인상태']='부칙의 과거 개정 지시·당시 시행본으로 확인'
with (B/'23개_내부규정_인용관계목록.csv').open('w',encoding='utf-8-sig',newline='') as f:
 w=csv.DictWriter(f,fieldnames=['ID']+[x for x in rows[0] if x!='ID']);w.writeheader();w.writerows(rows)
def esc(s):return s.replace('|','／').replace('\n',' ')
report=['# 23개 내부규정 인용관계','',
'기준: 작업 폴더의 저장본. 감사규정부터 벤처투자회사 등에 관한 행정자료 보관·관리 지침까지 23개 문서. 원문은 수정하지 않았다.','',
'명시된 문서명과 약칭, 본문·부칙·별표·서식의 조문 인용을 찾아 연결했다. **조번호 존재는 내용의 적합성이나 현행 법적 효력 확인을 뜻하지 않는다.** 기존 문제점 보고서의 검토 ID(R번호)를 연결했으며, 새 인용관계 ID는 C번호다. 추출문 기준의 목록이므로 한글 화면의 자동번호·숨김 편집요소·문장 줄바꿈은 원본 대조가 필요하다. 원문에 없는 단순 업무상 연관은 인용으로 추가하지 않았다. 조문번호 없는 포괄 준용도 별도로 표시했다. 자기 문서의 “제1항”, “전항”, “본조” 등 상대적 인용만 있는 문장은 별도 목록에 포함하지 않았다. 외부 법령은 기존 문제점 보고서에서 확인한 쟁점 외에는 현행 조문을 추가 전수 검증하지 않았다.','',
'개인정보 보호 및 관리지침은 본문이 반복 추출되어 동일 문구·위치·대상 인용을 묶고 근거행을 모두 남겼다. 부칙과 서식은 같은 제1조 등이 반복되므로 근거행의 앞뒤를 함께 확인한다. 타 문서의 머리말 잔존도 원문 언급으로 잡힐 수 있으므로 말미 항목은 공표본에서 확인한다.','',
'## 인용망을 읽는 방법','',
'- **문서별 요약:** 어떤 내부 규정과 외부 근거를 참조하는지, 그 문서를 다른 대상 문서가 어디에서 참조하는지 함께 표시한다.','- **상세 목록:** 인용 출발 위치 → 인용 대상 → 대상 조문·서식 → 확인상태 → 기존 문제점 보고서 항목으로 연결한다. CSV에는 인용 원문과 정비·확인 방법도 포함한다.','- **개정 영향:** 대상 조문을 고칠 때 아래 역방향 인용 목록에서 인용하는 문서도 함께 수정한다. 역방향은 이번 23개 문서가 인용한 관계만 집계하며 폴더 전체의 모든 역참조를 검색한 결과는 아니다.','',
'## 핵심 연결 관계','',
'```mermaid','flowchart LR',' A[감사규정] --> B[감사지침]',' B --> A',' B --> C[정관·이사회규정]',' D[개인정보 관리지침] --> E[침해사고 대응지침]',' E --> D',' D --> F[개인정보 보호법·시행령·고시]',' E --> F',' G[기록물관리규정] --> H[문서관리규정]',' G --> I[공공기록물법·시행령]',' J[내규관리규정] --> C',' K[법인카드 사용지침] --> L[여비규정·징계지침]','```','',
'화살표는 원문의 참조·위임·준용 방향이다. 기록물관리규정 부칙의 문서관리규정 참조는 **기록물관리규정 우선 적용**을 정하는 관계다. 개인정보 관리지침은 보고서에서 쓰는 짧은 표시이며 원문 제명은 개인정보 보호 및 관리지침이다.','',
'## 문서별 인용 요약','',
'|번호|문서|다른 내규|외부 근거|자기 문서 인용 행 수|','|---|---|---|---|---:|']
for i,n in enumerate(names,1):
 rr=[r for r in rows if r['문서']==n]
 report.append('|'+ '|'.join(map(esc,[str(i),n,' · '.join(sorted(set(r['인용대상'] for r in rr if r['구분']=='다른 내규'))) or '특정 문서 인용 없음',' · '.join(sorted(set(r['인용대상'] for r in rr if r['구분']=='외부 근거'))) or '특정 문서 인용 없음',str(sum(r['구분']=='자기 문서' for r in rr))]))+'|')
for i,n in enumerate(names,1):
 rr=[r for r in rows if r['문서']==n]
 report +=['',f'## {i}. {n}','']
 incoming=[r for r in rows if r['인용대상']==n and r['문서']!=n]
 report.append('**이 문서를 인용하는 대상 문서:** '+(' · '.join(sorted(set(r['문서']+'('+r['인용위치']+')' for r in incoming))) if incoming else '이번 23개 문서에서 다른 문서의 명시적 인용이 발견되지 않음.'))
 report+=['','|ID|출발 위치·근거|구분|인용 대상|대상 조문·서식|확인상태·검토 연결|','|---|---|---|---|---|---|']
 for r in rr:
  line=r['근거행'].split(',')[0];link=f"[근거 {r['근거행']}행]({(D/(n+'.txt')).as_posix()}:{line})"
  status=r['확인상태']+(' / '+r['기존검토ID'] if r['기존검토ID'] else '')
  report.append('|'+ '|'.join(map(esc,[r['ID'],r['인용위치']+' · '+link,r['구분'],r['인용대상'],r['대상조문·서식'],status]))+'|')
report+=['','## 개정 시 함께 확인할 항목','',
'1. 기존 오류 R번호를 기준으로 인용 대상을 확정하고 원문 제목·조·항·호·별표·서식명을 함께 정정한다. 인용망의 조번호 존재 표시만으로 오류가 없다고 판단하지 않는다.','2. 감사규정 개정 시 감사지침, 개인정보 지침 개정 시 침해사고 대응지침·위탁계약서, 서식번호 개정 시 해당 서식을 인용하는 본문을 함께 대조한다.','3. 리스크관리규정·상벌규정·감사기준시행세칙 및 구명칭 내규는 내규대장·승인본으로 유효성을 확인한다. 미수록 자체는 폐지를 뜻하지 않는다.','4. 외부 법령·시행령·시행규칙·고시·부처 시행지침은 적용 기준일의 원문을 확보하여 항호별로 대조한다. 특히 “법”·“시행령”·“규정”·“지침” 약칭의 원문 정의가 맞는지 확인한다.','5. 부칙의 과거 개정명령·삭제 주석·조문 이동 기록은 당시 시행본을 기준으로 확인한다. 현재 문서 제목·번호로 기계적으로 바꾸지 않는다.','']
(B/'23개_내부규정_인용관계.md').write_text('\n'.join(report)+'\n',encoding='utf8')
e=html.escape
parts=['<!doctype html><html lang="ko"><meta charset="utf-8"><title>23개 내부규정 인용관계</title><style>body{font-family:Malgun Gothic,sans-serif;margin:30px;color:#172638;line-height:1.55}table{border-collapse:collapse;width:100%;font-size:13px}td,th{border:1px solid #ccd4dd;padding:8px;vertical-align:top;text-align:left}th{background:#e8eef5}input,select{padding:10px;margin:5px}tr:nth-child(even){background:#f8fafc}.long{max-width:460px}@media print{input,select{display:none}thead{display:table-header-group}}</style><h1>23개 내부규정 인용관계</h1><p>원문 위치 → 대상 문서 → 대상 조문·서식 → 확인상태 → 기존 검토 ID(R번호).</p><p>조번호 존재는 내용 적합성·현행성 확인을 뜻하지 않습니다. 외부 근거는 추가 전수 검증하지 않았습니다. 추출문 기준이므로 자동번호·편집 흔적은 원본 확인이 필요합니다. 상대적 인용(제1항·전항·본조)만 있는 문장은 제외했습니다. 동일하게 반복 추출된 인용은 근거행을 합쳤습니다.</p><input id="q" placeholder="문서명·조문·R번호 검색" oninput="f()"><select id="k" onchange="f()"><option value="">전체 인용</option><option>다른 내규</option><option>외부 근거</option><option>자기 문서</option><option>포괄 근거</option></select><p id="count"></p><table><thead><tr><th>ID</th><th>문서·위치</th><th>구분</th><th>대상·조문</th><th>확인상태</th><th>정비·확인 방법</th><th>원문·근거행</th></tr></thead><tbody>']
for r in rows:
 cells=[r['ID'],r['문서']+' / '+r['인용위치'],r['구분'],r['인용대상']+' / '+r['대상조문·서식'],r['확인상태']+' / '+r['기존검토ID'],r['정비·확인방법'],r['원문']+' (추출 근거 '+r['근거행']+'행)']
 parts.append('<tr data-kind="'+e(r['구분'])+'">'+''.join('<td class="long">'+e(c)+'</td>' for c in cells)+'</tr>')
parts.append('</tbody></table><script>function f(){let q=document.querySelector("#q").value.toLowerCase(),k=document.querySelector("#k").value,n=0;document.querySelectorAll("tbody tr").forEach(r=>{let ok=r.textContent.toLowerCase().includes(q)&&(!k||r.dataset.kind===k);r.hidden=!ok;n+=ok});document.querySelector("#count").textContent=n+"개 인용 항목"}f()</script></html>')
(B/'23개_내부규정_인용관계.html').write_text(''.join(parts),encoding='utf8')
print(json.dumps({'문서수':len(names),'인용행':len(rows),'구분':dict(collections.Counter(r['구분'] for r in rows)),'역참조':len([r for r in rows if r['구분']=='다른 내규'])},ensure_ascii=False))
