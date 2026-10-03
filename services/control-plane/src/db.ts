import { Pool, PoolClient } from 'pg';
import { ServerConfig } from './config';

export class Database {
  readonly pool:Pool;
  constructor(config:ServerConfig) {
    if(config.schema && !/^[a-z][a-z0-9_]{0,60}$/.test(config.schema))throw new Error('Invalid schema');
    this.pool=new Pool({connectionString:config.databaseUrl,max:8,
      options:config.schema?`-c search_path=${config.schema}`:undefined,
      statement_timeout:10000,connectionTimeoutMillis:5000});
  }
  async transaction<T>(fn:(db:PoolClient)=>Promise<T>):Promise<T>{
    const client=await this.pool.connect();
    try {await client.query('BEGIN'); const result=await fn(client); await client.query('COMMIT');return result;}
    catch(error){await client.query('ROLLBACK');throw error;}finally{client.release();}
  }
  async migrate(){await this.pool.query(`
    CREATE TABLE IF NOT EXISTS organization(id uuid PRIMARY KEY);
    CREATE TABLE IF NOT EXISTS administrators(username text PRIMARY KEY,password_hash text NOT NULL);
    CREATE TABLE IF NOT EXISTS sessions(token_hash text PRIMARY KEY,username text REFERENCES administrators(username),expires_at timestamptz NOT NULL);
    CREATE TABLE IF NOT EXISTS policies(revision bigint PRIMARY KEY,rules jsonb NOT NULL,issued_at bigint NOT NULL,expires_at bigint NOT NULL);
    CREATE TABLE IF NOT EXISTS enrollment_tokens(token_hash text PRIMARY KEY,expires_at timestamptz NOT NULL,used_at timestamptz);
    CREATE TABLE IF NOT EXISTS devices(id uuid PRIMARY KEY,name text NOT NULL,platform text NOT NULL,token_hash text UNIQUE NOT NULL,
      enrolled_at timestamptz NOT NULL DEFAULT now(),revoked_at timestamptz,last_seen timestamptz,
      applied_revision bigint NOT NULL DEFAULT 0,protection_scope text,engine_state text,protected_targets jsonb NOT NULL DEFAULT '[]');
    CREATE TABLE IF NOT EXISTS events(device_id uuid REFERENCES devices(id),event_id uuid NOT NULL,
      received_at timestamptz NOT NULL DEFAULT now(),payload jsonb NOT NULL,PRIMARY KEY(device_id,event_id));
    CREATE INDEX IF NOT EXISTS events_received ON events(received_at DESC);
    CREATE TABLE IF NOT EXISTS audit(id bigserial PRIMARY KEY,created_at timestamptz NOT NULL DEFAULT now(),actor text NOT NULL,action text NOT NULL,detail jsonb NOT NULL);
  `);}
  async audit(actor:string,action:string,detail:Record<string,unknown>,db:PoolClient|Pool=this.pool){
    await db.query('INSERT INTO audit(actor,action,detail) VALUES($1,$2,$3)',[actor,action,JSON.stringify(detail)]);
  }
  async close(){await this.pool.end();}
}
