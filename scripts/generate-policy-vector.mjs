// Synthetic fixed seed only. This is never a deployment signing key.
import {createPrivateKey,createPublicKey,sign,createHash} from 'node:crypto';
import {mkdir,writeFile} from 'node:fs/promises';
import {createRequire} from 'node:module';
const require=createRequire(import.meta.url);const {defaultRules}=require('@aidlp/contracts');
const key=createPrivateKey({key:Buffer.concat([Buffer.from('302e020100300506032b657004220420','hex'),Buffer.alloc(32,7)]),format:'der',type:'pkcs8'});
const publicKey=Buffer.from(createPublicKey(key).export({format:'jwk'}).x,'base64url');
const payload={schema_version:1,tenant_id:'11111111-1111-4111-8111-111111111111',device_id:'22222222-2222-4222-8222-222222222222',revision:4,issued_at:100,expires_at:200,rules:defaultRules()};
const bytes=Buffer.from(JSON.stringify(payload));
const value={public_key_base64:publicKey.toString('base64'),clock:150,payload,envelope:{payload_base64:bytes.toString('base64'),signature_base64:sign(null,bytes,key).toString('base64'),key_id:createHash('sha256').update(publicKey).digest('hex').slice(0,16)}};
await mkdir('packages/contracts/fixtures',{recursive:true});await writeFile('packages/contracts/fixtures/node-signed-policy.json',JSON.stringify(value,null,2)+'\n');
console.log('Synthetic cross-language policy vector generated.');
