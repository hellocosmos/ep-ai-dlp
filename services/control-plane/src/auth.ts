import { createHash, randomBytes, scrypt, timingSafeEqual } from 'node:crypto';
import { promisify } from 'node:util';
import { HttpException, UnauthorizedException } from '@nestjs/common';
import { Database } from './db';
import { ServerConfig } from './config';

const derive=promisify(scrypt);
export const token=(prefix:string)=>prefix+randomBytes(32).toString('base64url');
export const hash=(value:string)=>createHash('sha256').update(value).digest('hex');
export async function passwordHash(password:string){const salt=randomBytes(16).toString('hex');const value=await derive(password,salt,64) as Buffer;return salt+':'+value.toString('hex');}
async function checkPassword(password:string,stored:string){const [salt,hex]=stored.split(':');const value=await derive(password,salt,64) as Buffer;const expected=Buffer.from(hex,'hex');return expected.length===value.length&&timingSafeEqual(expected,value);}
export class Auth {
  private attempts:number[]=[];private checking=0;
  constructor(private db:Database,private config:ServerConfig){}
  async bootstrap(){
    const rows=await this.db.pool.query('SELECT username FROM administrators LIMIT 1');
    if(rows.rowCount)return;
    if(!this.config.adminPassword || this.config.adminPassword.length<16)throw new Error('Bootstrap admin password must have at least 16 characters');
    await this.db.pool.query('INSERT INTO administrators VALUES($1,$2) ON CONFLICT DO NOTHING',[this.config.adminUser,await passwordHash(this.config.adminPassword)]);
  }
  async login(username:string,password:string){
    const now=Date.now();this.attempts=this.attempts.filter(t=>now-t<60000);
    if(this.attempts.length>=20||this.checking>=4)throw new HttpException('Try again later',429);
    this.attempts.push(now);this.checking++;
    try{
      const row=(await this.db.pool.query('SELECT password_hash FROM administrators WHERE username=$1',[username])).rows[0];
      // Perform a password KDF even for an unknown account.
      const valid=await checkPassword(password,row?.password_hash||('0'.repeat(32)+':'+ '0'.repeat(128)));
      if(!row||!valid)throw new UnauthorizedException('Invalid credentials');
      const session=token('adm_');
      await this.db.transaction(async tx=>{
        await tx.query("DELETE FROM sessions WHERE expires_at < now()");
        await tx.query("INSERT INTO sessions VALUES($1,$2,now()+interval '8 hours')",[hash(session),username]);
        await this.db.audit(username,'session.created',{},tx);
      });
      return {session,username};
    }finally{this.checking--;}
  }
  bearer(header:string|undefined,prefix:string){
    if(!header || !new RegExp(`^Bearer ${prefix}[A-Za-z0-9_-]{43}$`).test(header))throw new UnauthorizedException();
    return header.slice(7);
  }
  async administrator(header?:string){
    const value=this.bearer(header,'adm_');
    const row=(await this.db.pool.query('SELECT username FROM sessions WHERE token_hash=$1 AND expires_at>now()',[hash(value)])).rows[0];
    if(!row)throw new UnauthorizedException();return row.username as string;
  }
  async device(header?:string){
    const value=this.bearer(header,'dev_');
    const row=(await this.db.pool.query('SELECT id FROM devices WHERE token_hash=$1 AND revoked_at IS NULL',[hash(value)])).rows[0];
    if(!row)throw new UnauthorizedException();return row.id as string;
  }
  async logout(header?:string){const username=await this.administrator(header);await this.db.transaction(async tx=>{
    await tx.query('DELETE FROM sessions WHERE token_hash=$1',[hash(this.bearer(header,'adm_'))]);
    await this.db.audit(username,'session.ended',{},tx);
  });return {ok:true};}
}
