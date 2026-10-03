import {generateKeyPairSync,randomBytes,randomUUID} from 'node:crypto';
import {mkdir,writeFile,access} from 'node:fs/promises';
import path from 'node:path';
const directory=path.resolve('.local');await mkdir(directory,{recursive:true,mode:0o700});
try{await access(path.join(directory,'managed.env'));console.log('Existing configuration preserved.');process.exit(0);}catch{}
const password=randomBytes(24).toString('base64url'),admin=randomBytes(24).toString('base64url');
const key=generateKeyPairSync('ed25519').privateKey.export({type:'pkcs8',format:'pem'});
const keyPath=path.join(directory,'policy-signing.pem');
await writeFile(keyPath,key,{mode:0o600,flag:'wx'});
const content=[`AIDLP_DB_PASSWORD=${password}`,`DATABASE_URL=postgresql://aidlp:${password}@127.0.0.1:55438/aidlp`,
  `AIDLP_TENANT_ID=${randomUUID()}`,`AIDLP_SIGNING_KEY=${keyPath}`,'AIDLP_ADMIN_USER=admin',`AIDLP_ADMIN_PASSWORD=${admin}`,
  'AIDLP_PUBLIC_URL=http://127.0.0.1:3901','AIDLP_API_ORIGIN=http://127.0.0.1:3901','AIDLP_CONSOLE_ORIGIN=http://127.0.0.1:3100'].join('\n')+'\n';
await writeFile(path.join(directory,'managed.env'),content,{mode:0o600,flag:'wx'});
console.log('Created protected .local/managed.env and policy signing key. No credentials printed.');
