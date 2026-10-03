// Ported policy-signing boundary from TrapDefense b1867b7; see THIRD_PARTY.md.
import { createHash, createPrivateKey, createPublicKey, sign } from 'node:crypto';
import { readFileSync, statSync } from 'node:fs';
import { Envelope, PolicyPayload, PolicyPayloadSchema } from '@aidlp/contracts';

export class PolicySigner {
  private readonly key; readonly publicKeyBase64:string; readonly keyId:string;
  constructor(path:string){
    if(process.platform!=='win32' && (statSync(path).mode & 0o077))throw new Error('Signing key must have mode 0600');
    this.key=createPrivateKey(readFileSync(path));
    if(this.key.asymmetricKeyType!=='ed25519')throw new Error('Expected Ed25519 signing key');
    const jwk=createPublicKey(this.key).export({format:'jwk'});
    const raw=Buffer.from(jwk.x!,'base64url');
    this.publicKeyBase64=raw.toString('base64');
    this.keyId=createHash('sha256').update(raw).digest('hex').slice(0,16);
  }
  sign(payload:PolicyPayload):Envelope{
    const bytes=Buffer.from(JSON.stringify(PolicyPayloadSchema.parse(payload)),'utf8');
    return {payload_base64:bytes.toString('base64'),signature_base64:sign(null,bytes,this.key).toString('base64'),key_id:this.keyId};
  }
}
