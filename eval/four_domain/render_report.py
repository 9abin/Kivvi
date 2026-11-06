"""User-facing Markdown: overall task success only; raw JSON stays auditable."""
import json
def markdown(results):
    summary=json.loads((results/'summary.json').read_text(encoding='utf-8'))
    names={'rag':'RAG','pr_graph_rag':'PR #11 그래프 + RAG','domain_graph_rag':'복원·재검토 도메인 그래프 + RAG'}
    lines=['# 전체 평가 결과','', '| 방식 | 전체 120문항 결과 |','|---|---:|']
    for m in summary['metrics']:
        count=m['case_correct']; total=m['cases']; pending=m.get('case_pending_review',0)
        suffix=f' · 추가 발견 {pending}문항 검토 대기' if pending else f' · {100*count/total:.1f}%'
        lines.append(f"| {names[m['method']]} | {count}/{total}{suffix} |")
    lines+=['','문항 성공은 그 문항의 알려진 새 문제·해소 문제를 모두 맞혔을 때 인정한다. 정답은 사람 검수 전 AI 초안이다. 정답 목록 밖 발견은 자동 오탐 처리하지 않고 검토 대기로 둔다.','']
    return '\n'.join(lines)
def write_report(results):
    (results/'report.md').write_text(markdown(results),encoding='utf-8')
