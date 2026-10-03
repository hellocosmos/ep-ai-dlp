import { randomUUID } from 'node:crypto';
import { ConflictException, NotFoundException, UnauthorizedException } from '@nestjs/common';
import { DeviceReport } from '@aidlp/contracts';
import { Database } from './db';
import { hash,token } from './auth';

export class Devices {
  constructor(private db:Database){}
  async issue(actor:string){
    const value=token('enr_');const expiry=new Date(Date.now()+15*60000);
    await this.db.transaction(async tx=>{
      await tx.query('DELETE FROM enrollment_tokens WHERE expires_at<now()');
      await tx.query('INSERT INTO enrollment_tokens(token_hash,expires_at) VALUES($1,$2)',[hash(value),expiry]);
      await this.db.audit(actor,'enrollment.issued',{expires_at:expiry.toISOString()},tx);
    });return {token:value,expires_at:expiry.toISOString()};
  }
  async enroll(value:string,name:string,platform:string){
    return this.db.transaction(async tx=>{
      const used=await tx.query('UPDATE enrollment_tokens SET used_at=now() WHERE token_hash=$1 AND expires_at>now() AND used_at IS NULL RETURNING token_hash',[hash(value)]);
      if(!used.rowCount)throw new UnauthorizedException('Enrollment is invalid or expired');
      const id=randomUUID(),credential=token('dev_');
      await tx.query('INSERT INTO devices(id,name,platform,token_hash) VALUES($1,$2,$3,$4)',[id,name,platform,hash(credential)]);
      await this.db.audit('device:'+id,'device.enrolled',{platform},tx);
      return {device_id:id,device_token:credential};
    });
  }
  async list(){const rows=(await this.db.pool.query(`SELECT id,name,platform,enrolled_at,revoked_at,last_seen,applied_revision,protection_scope,engine_state,protected_targets FROM devices ORDER BY enrolled_at DESC LIMIT 500`)).rows;
    return rows.map(row=>({...row,applied_revision:Number(row.applied_revision)}));}
  async revoke(id:string,actor:string){return this.db.transaction(async tx=>{
    const row=await tx.query('UPDATE devices SET revoked_at=COALESCE(revoked_at,now()) WHERE id=$1 RETURNING id',[id]);
    if(!row.rowCount)throw new NotFoundException();
    await this.db.audit(actor,'device.revoked',{device_id:id},tx);return {ok:true};
  });}
  async report(id:string,report:DeviceReport){return this.db.transaction(async tx=>{
    // Lock against concurrent revocation and reject a token revoked after the guard ran.
    const device=(await tx.query('SELECT revoked_at FROM devices WHERE id=$1 FOR UPDATE',[id])).rows[0];
    if(!device||device.revoked_at)throw new UnauthorizedException();
    const revisions=[...new Set([report.applied_revision,...report.events.map(event=>event.policy_revision)].filter(v=>v>0))];
    if(revisions.length){const known=await tx.query('SELECT revision FROM policies WHERE revision=ANY($1::bigint[])',[revisions]);
      if(known.rowCount!==revisions.length)throw new ConflictException('Unknown policy revision');}
    const accepted:string[]=[];
    for(const event of report.events){
      const inserted=await tx.query('INSERT INTO events(device_id,event_id,payload) VALUES($1,$2,$3) ON CONFLICT DO NOTHING RETURNING event_id',[id,event.event_id,JSON.stringify(event)]);
      if(!inserted.rowCount){const existing=(await tx.query('SELECT payload=$3::jsonb AS same FROM events WHERE device_id=$1 AND event_id=$2',[id,event.event_id,JSON.stringify(event)])).rows[0];
        if(!existing?.same)throw new ConflictException('Event ID already has different content');}
      accepted.push(event.event_id);
    }
    await tx.query('UPDATE devices SET last_seen=now(),applied_revision=$2,protection_scope=$3,engine_state=$4,protected_targets=$5 WHERE id=$1',[id,report.applied_revision,report.protection_scope,report.engine_state,JSON.stringify(report.protected_targets)]);
    return {accepted_event_ids:accepted};
  });}
  async events(){return (await this.db.pool.query('SELECT e.device_id,d.name AS device_name,e.received_at,e.payload FROM events e JOIN devices d ON d.id=e.device_id ORDER BY e.received_at DESC LIMIT 200')).rows;}
}
