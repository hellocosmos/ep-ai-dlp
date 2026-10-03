"""Explicit controlled-adapter identities, never inferred from submitted text.

Changing a variant is a lab operation, not authentication to a real provider.
Production adapters must construct these facts from validated provider sessions.
"""
from dataclasses import dataclass,asdict

@dataclass(frozen=True)
class Scenario:
    id:str
    title:str
    description:str
    checkpoint:str
    protected_text:str
    allowed_text:str
    destination:str
    requirement:str

SCENARIOS=[
 Scenario('customer_upload','고객 엑셀 → 외부 AI','라벨 없는 고객별 거래정보를 업로드 전에 검사합니다.','before_upload',
  '고객명 | 최근 구매 내역 | 미납액\n김가람 | 보안 솔루션 50석 | 340만원\n이누리 | 서버 유지보수 | 80만원',
  '상품군 | 전체 판매량\n보안 솔루션 | 150\n서버 유지보수 | 80','personal_ai','민감 파일 수신 0건; 익명 집계 파일은 수신'),
 Scenario('m365_grounding','M365 기밀문서 → Copilot','제공자 내부 처리 전 레이블·의미 정책을 적용하는 어댑터 계약입니다.','before_grounding',
  '인수 검토 대상: 가온시스템. 제안 인수가 300억원. 협상 상한 350억원. 이사회 검토안.',
  '회사의 공개 소개: 정보보호 소프트웨어를 개발하고 고객 지원을 제공합니다.','internal_ai','AI 처리 금지 레이블은 모델 입력 전 제외'),
 Scenario('drive_personal','Drive 문서 → 개인 AI','출처가 확인된 회사 문서의 외부 전송을 검사합니다.','before_upload',
  '한빛상사 개별 계약: 공급단가 8,000원, 내부 원가 4,200원. 재협상 시 할인 상한 12%.',
  '공개 제품 카탈로그: 기본 요금 월 20,000원, 전문가 요금 월 40,000원. 누구나 동일 요금.','personal_ai','기밀 또는 개별 거래조건은 차단; 공개 카탈로그 허용'),
 Scenario('cloud_summary','상담 앱 → 클라우드 모델','원문이 외부 모델에 도달하기 전에 지원 개인정보를 제거합니다.','before_model_api',
  '고객 문의: 배송을 변경해주세요. 연락처 010-1234-5678, 이메일 test.customer@example.test. 주민등록번호 900101-1234567.',
  '고객 문의: 배송 일정을 내일로 변경할 수 있나요?','approved_ai','모델 수신 내용에 원본 식별자가 없음'),
 Scenario('rag_acl','사내 RAG → 인사자료','사용자 ACL을 확인한 후 검색 결과를 모델에 제공합니다.','before_retrieval',
  '직원 김가람: 연봉 8,400만원. 성과등급 B. 다음 연도 보상 협의 대상.',
  '직원 김가람: 연봉 8,400만원. 성과등급 B. 다음 연도 보상 협의 대상.','internal_ai','일반 직원은 검색·모델 수신 모두 0건; HR 권한은 허용'),
 Scenario('agent_mail','Agent → 외부 메일','도구 실행 직전에 고객별 거래조건과 위임 범위를 검사합니다.','before_tool_call',
  '한빛상사 공급단가 8,000원, 원가 4,200원, 협상 상한 할인 12%. 이 자료를 외부 수신자에게 보냅니다.',
  '공개 웨비나 안내: 보안 운영 자동화 사례를 소개합니다. 행사 신청은 공개 홈페이지에서 가능합니다.','external_mail','차단 시 메일 도구 실행 0건; 공개 안내는 실행'),
]

def list_scenarios():
    return [dict(**asdict(s),evidence_scope='controlled_local_adapter',live_provider_connected=False) for s in SCENARIOS]

def resolve(scenario_id,variant):
    s=next(x for x in SCENARIOS if x.id==scenario_id)
    protected=variant=='protected'
    facts=dict(adapter='local-lab-v1',actor='demo-employee',destination=s.destination,account_verified=True,
               document_label='unlabelled',source_verified=False,acl_allowed=True,tool_allowed=True,
               checkpoint=s.checkpoint,variant=variant)
    if scenario_id=='m365_grounding':facts['document_label']='ai_processing_prohibited' if protected else 'public'
    if scenario_id=='drive_personal':facts['source_verified']=True;facts['source']='controlled_drive_repository'
    if scenario_id=='rag_acl':facts['acl_allowed']=not protected;facts['actor']='demo-employee' if protected else 'demo-hr'
    return s, facts, s.protected_text if protected else s.allowed_text
