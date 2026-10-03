"""Policy-scoped Judge with deterministic authorization and actual delivery gates."""
import hashlib
import json
import sys
import time
import uuid
from pathlib import Path
from .errors import JudgeError
from .documents import extract_bounded as extract,ExtractionError
from .schemas import Action,Decision,Finding,Policy,RunRequest
from .scenarios import resolve
from .delivery import DeliveryError

# Reuse the established checked patterns, rather than invent another PII implementation.
sys.path.insert(0,str(Path(__file__).resolve().parents[2]/'agent'))
from aidlp.detectors import detect,redact

SECRET_IDS={'private_key','anthropic_key','openai_key','aws_access_key','github_token','slack_token','google_api_key'}

class Engine:
    def __init__(self,store,judge,delivery):
        self.store=store;self.judge=judge;self.delivery=delivery

    def run(self,request:RunRequest):
        start=time.perf_counter()
        snapshot=self.store.current();revision=snapshot['revision']
        scenario,facts,text=resolve(request.scenario_id,request.variant)
        preblocked = not facts['acl_allowed'] or not facts['tool_allowed'] or facts['document_label']=='ai_processing_prohibited'
        if request.text is not None:text=request.text
        extraction={'format':'text','complete':True,'characters':len(text)}
        error=None
        if request.file_base64 is not None and not preblocked:
            try:text,extraction=extract(request.filename,request.file_base64)
            except Exception as exc:
                error=str(exc) if isinstance(exc,ExtractionError) else 'document_extraction_failed'
                text='';extraction={'complete':False,'format':'unsupported_or_invalid'}
        if preblocked:
            extraction={'format':'not_read','complete':False,'skipped_by_authorization':True}
        content_hash=hashlib.sha256((request.file_base64 if request.file_base64 is not None else text).encode()).hexdigest()
        digest=hashlib.sha256(json.dumps({'content':content_hash,'facts':facts,'scenario':scenario.id,
            'policy_revision':revision,'filename_hash':hashlib.sha256((request.filename or '').encode()).hexdigest()},sort_keys=True).encode()).hexdigest()
        result=Decision(id=str(uuid.uuid4()),action=Action.ALLOW,reason='policy_allowed',
                        policy_revision=revision,request_digest=digest,scenario_id=scenario.id,extraction=extraction)
        output=text
        if error:
            result.action=Action.BLOCK;result.reason=error
        elif not facts['acl_allowed']:
            result.action=Action.BLOCK;result.reason='source_acl_denied_before_retrieval'
        elif not facts['tool_allowed']:
            result.action=Action.BLOCK;result.reason='tool_not_delegated'
        elif facts['document_label']=='ai_processing_prohibited':
            result.action=Action.BLOCK;result.reason='label_prohibits_ai_processing'
        elif not text.strip():
            result.action=Action.REVIEW;result.reason='empty_content'
        else:
            patterns=detect(text)
            result.detector_ids=sorted({f.rule for f in patterns})
            if any(f.rule in SECRET_IDS for f in patterns):
                result.action=Action.BLOCK;result.reason='secret_detected'
            elif scenario.id=='cloud_summary' and patterns:
                output=redact(text,patterns)
                result.action=Action.REDACT;result.reason='supported_identifiers_redacted'
            elif facts['destination'] in {'personal_ai','external_mail'} and patterns:
                result.action=Action.BLOCK;result.reason='direct_identifier_detected'
            if result.action in {Action.ALLOW,Action.REDACT}:
                policies=[Policy.model_validate(p) for p in snapshot['policies']]
                applicable=[p for p in policies if p.enabled and scenario.id in p.scenarios and facts['destination'] in p.destinations]
                for policy in applicable:
                    try:
                        value=self.judge.evaluate(policy.model_id,text,policy.question,{
                            'match':policy.match_description,
                            'no_match':policy.no_match_description,
                            'insufficient':'The supplied content is insufficient or genuinely ambiguous for this distinction.'})
                        finding=Finding(policy_id=policy.id,**value)
                        if finding.choice != max(finding.scores,key=finding.scores.get) or set(finding.scores)!={'match','no_match','insufficient'} or not all(0<=v<=1 for v in finding.scores.values()) or abs(sum(finding.scores.values())-1)>0.001:
                            raise JudgeError('invalid_judge_scores')
                        result.findings.append(finding)
                        confidence=finding.scores[finding.choice]
                        if finding.choice=='insufficient' or confidence<policy.threshold:
                            candidate=Action(policy.uncertainty_action);reason='judge_uncertain'
                        elif finding.choice=='match':
                            candidate=Action(policy.effect);reason='semantic_policy_match'
                        else:continue
                        if candidate==Action.BLOCK or result.action!=Action.BLOCK:
                            result.action=candidate;result.reason=reason
                        if result.action==Action.BLOCK:break
                    except Exception as exc:
                        result.action=Action.BLOCK
                        result.reason=str(exc) if isinstance(exc,JudgeError) else 'judge_unavailable'
                        break
        # Publication and final dispatch are serialized within this single-worker service.
        with self.store.dispatch_lock:
            if self.store.current()['revision']!=revision:
                result.action=Action.BLOCK;result.reason='policy_changed_during_inspection'
            if request.approval_id:
                # Approvals can resolve review only; never override an ACL/label/secret block.
                if result.action==Action.REVIEW and self.store.consume_approval(request.approval_id,digest,revision):
                    result.action=Action.ALLOW;result.reason='bound_human_approval';result.approval_id=request.approval_id
                elif result.action!=Action.BLOCK:
                    result.action=Action.BLOCK;result.reason='approval_context_invalid_or_consumed'
            elif result.action==Action.REVIEW:
                result.approval_id=self.store.create_approval(digest,revision)
            result.elapsed_ms=round((time.perf_counter()-start)*1000,3)
            self.store.audit('inspection.decided',result.model_dump(mode='json'))
            if result.action in {Action.ALLOW,Action.REDACT}:
                try:
                    receipt=self.delivery.send(digest,scenario.id,output)
                    result.delivered=True;result.delivery_id=receipt['id']
                except DeliveryError as exc:
                    result.reason=str(exc)
                    # Preserve allowed inspection verdict while marking failed/unknown delivery separately.
                    result.delivered=False
                self.store.audit('delivery.result',{'decision_id':result.id,'request_digest':digest,
                    'delivered':result.delivered,'delivery_id':result.delivery_id,'reason':result.reason})
        result.elapsed_ms=round((time.perf_counter()-start)*1000,3)
        return result
