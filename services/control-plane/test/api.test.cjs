const {test,before,after}=require('node:test');
const assert=require('node:assert/strict');
const {Pool}=require('pg');
const {randomUUID,generateKeyPairSync,verify,createPublicKey}=require('node:crypto');
const fs=require('node:fs/promises');
const os=require('node:os');
const path=require('node:path');
const {parseEnv}=require('node:util');
const {createApplication}=require('../dist/app');
const {defaultRules,EventSchema}=require('@aidlp/contracts');
let app,context,base,admin,device,enrollment,config,temp,root,schema;
async function call(route,body,credential){const res=await fetch(base+'/v1/'+route,{method:body===undefined?'GET':'POST',headers:{'content-type':'application/json',...(credential?{authorization:'Bearer '+credential}:{})},...(body===undefined?{}:{body:JSON.stringify(body)})});return {status:res.status,data:await res.json()};}
async function start(){({app,context}=await createApplication(config));await app.listen(0,'127.0.0.1');base=await app.getUrl();}
before(async()=>{
  const environment=process.env.DATABASE_URL?process.env:parseEnv(await fs.readFile(path.resolve('../../.local/managed.env'),'utf8'));
  if(!environment.DATABASE_URL)throw new Error('Dedicated test database is required');
  temp=await fs.mkdtemp(path.join(os.tmpdir(),'aidlp-api-'));schema='test_'+randomUUID().replaceAll('-','');
  root=new Pool({connectionString:environment.DATABASE_URL});await root.query(`CREATE SCHEMA ${schema}`);
  const keyPath=path.join(temp,'signing.pem');await fs.writeFile(keyPath,generateKeyPairSync('ed25519').privateKey.export({format:'pem',type:'pkcs8'}),{mode:0o600});
  config={databaseUrl:environment.DATABASE_URL,schema,tenantId:randomUUID(),signingKeyPath:keyPath,adminUser:'admin',adminPassword:'synthetic-admin-password-2026',publicUrl:'http://127.0.0.1:3901'};
  await start();
});
after(async()=>{await app?.close();await context?.db.close();if(root){await root.query(`DROP SCHEMA IF EXISTS ${schema} CASCADE`);await root.end();}if(temp)await fs.rm(temp,{recursive:true,force:true});});
test('public health does not expose management state',async()=>{const r=await call('health');assert.equal(r.status,200);assert.equal(r.data.ok,true);assert.equal((await call('admin/overview')).status,401);assert.equal((await call('device/policy')).status,401);});
test('strict login schema, invalid password and valid opaque session',async()=>{assert.equal((await call('auth/login',{username:'admin',password:'wrong',extra:'x'})).status,400);assert.equal((await call('auth/login',{username:'admin',password:'wrong'})).status,401);const r=await call('auth/login',{username:'admin',password:config.adminPassword});assert.equal(r.status,201);admin=r.data.session;assert.match(admin,/^adm_/);assert.equal((await call('device/policy',undefined,admin)).status,401);});
test('one-time enrollment is transactional and stores only hashes',async()=>{
  enrollment=(await call('admin/enrollments',{},admin)).data;
  const input={token:enrollment.token,name:'WINDOWS-TEST',platform:'windows'};
  const results=await Promise.all([call('enroll',input),call('enroll',input)]);
  assert.deepEqual(results.map(r=>r.status).sort(),[201,401]);device=results.find(r=>r.status===201).data;
  assert.equal((await call('admin/overview',undefined,device.device_token)).status,401);
  const stored=(await context.db.pool.query('SELECT token_hash FROM devices')).rows[0];assert.notEqual(stored.token_hash,device.device_token);assert.equal(stored.token_hash.length,64);
});
test('policy signature binds exact bytes to organization and device',async()=>{
  const r=await call('device/policy',undefined,device.device_token);assert.equal(r.status,200);
  const bytes=Buffer.from(r.data.payload_base64,'base64');const payload=JSON.parse(bytes);
  assert.equal(payload.device_id,device.device_id);assert.equal(payload.tenant_id,config.tenantId);assert.equal(payload.revision,1);
  const key=createPublicKey({key:{kty:'OKP',crv:'Ed25519',x:Buffer.from(enrollment.public_key_base64,'base64').toString('base64url')},format:'jwk'});
  assert(verify(null,bytes,key,Buffer.from(r.data.signature_base64,'base64')));bytes[5]^=1;assert(!verify(null,bytes,key,Buffer.from(r.data.signature_base64,'base64')));
});
test('publication conflict is enforced under concurrency',async()=>{
  const rules=defaultRules();rules.email='monitor';const input={expected_revision:1,rules};
  const results=await Promise.all([call('admin/policy',input,admin),call('admin/policy',input,admin)]);
  assert.deepEqual(results.map(r=>r.status).sort(),[201,409]);const p=(await call('admin/policy',undefined,admin)).data;assert.equal(p.revision,2);assert.equal(p.rules.email,'monitor');
  assert.equal((await call('admin/policy',{expected_revision:2,rules:{email:'block'}},admin)).status,400);
});
test('metadata-only reports deduplicate retries and reject changed duplicate IDs',async()=>{
  const event={event_id:randomUUID(),timestamp_ms:Date.now(),policy_revision:2,host:'aidlp.test',method:'POST',body_len:23,reason:'sensitive_data',rules:['email'],action:'monitor'};
  const report={applied_revision:2,protection_scope:'selected_process',engine_state:'running',protected_targets:['aidlp.test:18443'],events:[event]};
  const first=await call('device/report',report,device.device_token);assert.equal(first.status,201);assert.deepEqual(first.data.accepted_event_ids,[event.event_id]);
  assert.equal((await call('device/report',report,device.device_token)).status,201);
  const count=(await context.db.pool.query('SELECT count(*) FROM events')).rows[0];assert.equal(count.count,'1');
  assert.equal((await call('device/report',{...report,events:[{...event,body_len:24}]},device.device_token)).status,409);
  assert.equal((await call('device/report',{...report,events:[{...event,body:'secret'}]},device.device_token)).status,400);
  assert.equal((await call('device/report',{...report,device_id:randomUUID()},device.device_token)).status,400);
  assert.equal((await call('device/report',{...report,applied_revision:99},device.device_token)).status,409);
  assert.equal((await call('device/report',{...report,events:[{...event,host:'aidlp.test/?secret=x'}]},device.device_token)).status,400);
  assert.throws(()=>EventSchema.parse({...event,query:'secret'}));
});
test('applied state is a device report, independent from publication',async()=>{const devices=(await call('admin/devices',undefined,admin)).data;assert.equal(devices[0].applied_revision,2);const rules=defaultRules();const p=await call('admin/policy',{expected_revision:2,rules},admin);assert.equal(p.status,201);assert.equal((await call('admin/devices',undefined,admin)).data[0].applied_revision,2);});
test('another enrolled device receives its own bound payload',async()=>{const other=(await call('admin/enrollments',{},admin)).data;const second=(await call('enroll',{token:other.token,name:'SECOND',platform:'windows'})).data;const p=(await call('device/policy',undefined,second.device_token)).data;assert.equal(JSON.parse(Buffer.from(p.payload_base64,'base64')).device_id,second.device_id);assert.notEqual(second.device_id,device.device_id);});
test('restart preserves policies, sessions and device state',async()=>{await app.close();await context.db.close();await start();assert.equal((await call('admin/policy',undefined,admin)).data.revision,3);assert.equal((await call('admin/devices',undefined,admin)).data.length,2);assert.equal((await call('admin/events',undefined,admin)).data.length,1);assert.equal((await call('device/policy',undefined,device.device_token)).status,200);});
test('revocation rejects future device requests; logout invalidates session',async()=>{assert.equal((await call(`admin/devices/${device.device_id}/revoke`,{},admin)).status,201);assert.equal((await call('device/policy',undefined,device.device_token)).status,401);assert.equal((await call('device/report',{},device.device_token)).status,401);assert.equal((await call('auth/logout',{},admin)).status,201);assert.equal((await call('admin/overview',undefined,admin)).status,401);});
test('login attempts are bounded',async()=>{let throttled=false;for(let i=0;i<22;i++){if((await call('auth/login',{username:'missing',password:'invalid'})).status===429){throttled=true;break;}}assert(throttled);});
