import type {ManagedEvent,Rules} from '@aidlp/contracts';
export type Policy={revision:number;rules:Rules;issued_at:number;expires_at:number};
export type Device={id:string;name:string;platform:string;enrolled_at:string;revoked_at:string|null;last_seen:string|null;applied_revision:number;protection_scope:string|null;engine_state:string|null;protected_targets:string[]};
export type EventRow={device_id:string;device_name:string;received_at:string;payload:ManagedEvent};
export type Overview={username:string;tenant_id:string;policy:Policy;devices:Device[];events:EventRow[];counts:{events:number;blocked:number;monitored:number}};
export type AuditRow={id:number;created_at:string;actor:string;action:string;detail:Record<string,unknown>};
export async function api<T>(path:string,body?:unknown):Promise<T>{
  const response=await fetch('/api/'+path,{method:body===undefined?'GET':'POST',headers:body===undefined?{}:{'content-type':'application/json'},...(body===undefined?{}:{body:JSON.stringify(body)}),cache:'no-store'});
  if(response.status===401)throw new Error('AUTH');
  if(!response.ok){if(response.status===409)throw new Error('Another policy was published first. Refresh and try again.');const value=await response.json().catch(()=>({}));throw new Error(response.status===503?value.message:'Request failed. Check your input and connection.');}
  return response.json();
}
export const date=(value:string|number|null)=>value?new Date(typeof value==='number'?value*1000:value).toLocaleString('en-US',{month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit',second:'2-digit',hour12:false}):'—';
export const online=(device:Device)=>!device.revoked_at&&!!device.last_seen&&Date.now()-new Date(device.last_seen).getTime()<30000;
