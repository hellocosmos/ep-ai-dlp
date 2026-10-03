import type {EventRow} from './types';
import {date} from './types';
import {RULE_LABELS,type RULE_IDS} from '@aidlp/contracts';
import Icon from './Icon';
export default function EventTable({events}:{events:EventRow[]}){
  if(!events.length)return <div className="empty"><Icon name="events" size={30}/><h3>No inspection events yet</h3><p>Results appear when a connected agent inspects requests.</p></div>;
  return <div className="table-scroll"><table><thead><tr><th>Time</th><th>Device / destination</th><th>Detection</th><th>Action</th><th>Policy</th></tr></thead><tbody>{events.map(({payload:e,device_name,device_id})=><tr key={device_id+e.event_id}><td className="nowrap muted">{date(e.timestamp_ms/1000)}</td><td><strong>{device_name}</strong><small>{e.host} · {e.method}</small></td><td>{e.rules.length?e.rules.map(rule=>RULE_LABELS[rule]).join(', '):e.reason==='clean'?'No sensitive data':e.reason==='policy_unavailable'?'No valid policy':'Unsupported request'}<small>{e.body_len.toLocaleString()} bytes · Metadata only</small></td><td><span className={`badge ${e.action==='block'?'danger':e.action==='monitor'?'warning':'success'}`}>{e.action==='block'?'Block':e.action==='monitor'?'Monitor and allow':'Allow'}</span></td><td className="mono">{e.policy_revision?`v${e.policy_revision}`:'—'}</td></tr>)}</tbody></table></div>;
}
