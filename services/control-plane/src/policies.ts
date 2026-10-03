import { ConflictException, ServiceUnavailableException } from '@nestjs/common';
import { defaultRules, Rules } from '@aidlp/contracts';
import { Database } from './db';
import { PolicySigner } from './signing';

export class Policies {
  constructor(private db:Database,readonly signer:PolicySigner,private tenantId:string){}
  async bootstrap(){
    const now=Math.floor(Date.now()/1000);
    await this.db.pool.query('INSERT INTO policies VALUES(1,$1,$2,$3) ON CONFLICT DO NOTHING',[JSON.stringify(defaultRules()),now,now+7*86400]);
  }
  async current(){
    const row=(await this.db.pool.query('SELECT * FROM policies ORDER BY revision DESC LIMIT 1')).rows[0];
    if(!row)throw new ServiceUnavailableException('Policy unavailable');
    return {revision:Number(row.revision),rules:row.rules as Rules,issued_at:Number(row.issued_at),expires_at:Number(row.expires_at)};
  }
  async publish(expected:number,rules:Rules,actor:string){
    return this.db.transaction(async tx=>{
      // One organization lock serializes concurrent policy revisions, even with multiple API processes.
      await tx.query('SELECT id FROM organization FOR UPDATE');
      const row=(await tx.query('SELECT revision FROM policies ORDER BY revision DESC LIMIT 1')).rows[0];
      const previous=Number(row.revision);
      if(previous!==expected)throw new ConflictException('Policy changed; refresh before publishing');
      const revision=previous+1,issued_at=Math.floor(Date.now()/1000),expires_at=issued_at+7*86400;
      await tx.query('INSERT INTO policies VALUES($1,$2,$3,$4)',[revision,JSON.stringify(rules),issued_at,expires_at]);
      await this.db.audit(actor,'policy.published',{revision,previous_revision:previous},tx);
      return {revision,rules,issued_at,expires_at};
    });
  }
  async envelope(deviceId:string){const policy=await this.current();return this.signer.sign({schema_version:1,tenant_id:this.tenantId,device_id:deviceId,...policy});}
}
