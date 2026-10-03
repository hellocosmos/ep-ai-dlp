import { z } from 'zod';

export const RULE_IDS = ['anthropic_key','aws_access_key','credit_card','email','github_token','google_api_key','kr_phone','kr_rrn','openai_key','private_key','slack_token'] as const;
export const RuleId = z.enum(RULE_IDS);
export const RuleAction = z.enum(['block','monitor']);
export const RulesSchema = z.strictObject(Object.fromEntries(RULE_IDS.map(id => [id, RuleAction])) as Record<typeof RULE_IDS[number], typeof RuleAction>);
export type Rules = z.infer<typeof RulesSchema>;
export const defaultRules = (): Rules => Object.fromEntries(RULE_IDS.map(id => [id, 'block'])) as Rules;
export const safeInteger = z.number().int().min(0).max(Number.MAX_SAFE_INTEGER);
export const PolicyInput = z.strictObject({expected_revision:safeInteger, rules:RulesSchema});
export const PolicyPayloadSchema = z.strictObject({
  schema_version:z.literal(1), tenant_id:z.uuid(), device_id:z.uuid(),
  revision:safeInteger.min(1), issued_at:safeInteger, expires_at:safeInteger, rules:RulesSchema,
});
export type PolicyPayload = z.infer<typeof PolicyPayloadSchema>;
export const EnvelopeSchema = z.strictObject({payload_base64:z.string().max(16384),signature_base64:z.string().length(88),key_id:z.string().regex(/^[a-f0-9]{16}$/)});
export type Envelope = z.infer<typeof EnvelopeSchema>;
export const LoginInput = z.strictObject({username:z.string().regex(/^[a-zA-Z0-9_.@-]{1,80}$/), password:z.string().min(1).max(256)});
export const EnrollmentInput = z.strictObject({token:z.string().regex(/^enr_[a-zA-Z0-9_-]{43}$/), name:z.string().regex(/^[a-zA-Z0-9][a-zA-Z0-9_. -]{0,79}$/), platform:z.enum(['windows','macos'])});
export const Host = z.string().min(1).max(253).regex(/^[a-zA-Z0-9.:[\]-]+$/);
export const EventSchema = z.strictObject({
  event_id:z.uuid(), timestamp_ms:safeInteger, policy_revision:safeInteger,
  host:Host, method:z.enum(['GET','HEAD','POST','PUT','PATCH','DELETE','OPTIONS','CONNECT','TRACE']),
  body_len:safeInteger.max(1048576), reason:z.enum(['clean','sensitive_data','uninspectable_request','policy_unavailable']),
  rules:z.array(RuleId).max(RULE_IDS.length).refine(v=>new Set(v).size===v.length),
  action:z.enum(['allow','monitor','block']),
}).superRefine((v,ctx)=>{
  if ((v.reason==='clean' && (v.rules.length || v.action!=='allow')) ||
      (v.reason==='sensitive_data' && (!v.rules.length || v.action==='allow')) ||
      (['uninspectable_request','policy_unavailable'].includes(v.reason) && (v.action!=='block' || v.rules.length)))
    ctx.addIssue({code:'custom',message:'Inconsistent decision'});
});
export type ManagedEvent = z.infer<typeof EventSchema>;
export const ReportSchema = z.strictObject({
  applied_revision:safeInteger, protection_scope:z.enum(['fixture','selected_process','configured_applications']),
  engine_state:z.enum(['starting','running','stopped','policy_unavailable']),
  protected_targets:z.array(Host).max(32), events:z.array(EventSchema).max(100),
});
export type DeviceReport = z.infer<typeof ReportSchema>;
export const RULE_LABELS: Record<typeof RULE_IDS[number],string> = {
  anthropic_key:'Anthropic API key', aws_access_key:'AWS access key', credit_card:'Credit card number',
  email:'Email address', github_token:'GitHub token', google_api_key:'Google API key',
  kr_phone:'Korean mobile number', kr_rrn:'Korean resident ID', openai_key:'OpenAI API key',
  private_key:'Private key', slack_token:'Slack token',
};
