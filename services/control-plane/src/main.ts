import { readFileSync } from 'node:fs';
import { createServer } from 'node:https';
import { createApplication } from './app';
import { configFromEnv } from './config';

async function main(){
  const config=configFromEnv();
  if(process.env.AIDLP_ENROLLMENT_CA_FILE)config.enrollmentCaPem=readFileSync(process.env.AIDLP_ENROLLMENT_CA_FILE,'utf8');
  const {app,context}=await createApplication(config);
  await app.listen(Number(process.env.AIDLP_API_PORT||3901),'127.0.0.1');
  // Optional dedicated lab TLS listener. No global certificate-verification override.
  const tls=process.env.AIDLP_TLS_CERT&&process.env.AIDLP_TLS_KEY?createServer({cert:readFileSync(process.env.AIDLP_TLS_CERT),key:readFileSync(process.env.AIDLP_TLS_KEY)},app.getHttpAdapter().getInstance()):null;
  if(tls)await new Promise<void>((resolve,reject)=>{tls.once('error',reject);tls.listen(Number(process.env.AIDLP_TLS_PORT||3943),process.env.AIDLP_TLS_HOST||'127.0.0.1',resolve);});
  console.log(JSON.stringify({ready:true,http:'127.0.0.1',port:Number(process.env.AIDLP_API_PORT||3901),tls:!!tls}));
  let stopping=false;const stop=async()=>{if(stopping)return;stopping=true;tls?.close();await app.close();await context.db.close();};
  process.once('SIGINT',()=>{void stop();});process.once('SIGTERM',()=>{void stop();});
}
main().catch(()=>{console.error('Control plane startup failed; verify database, key permissions and configuration.');process.exitCode=1;});
