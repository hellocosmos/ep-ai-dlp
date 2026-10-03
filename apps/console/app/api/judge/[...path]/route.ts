import {NextRequest,NextResponse} from 'next/server';
import {readFile} from 'node:fs/promises';
import {resolve} from 'node:path';
export const runtime='nodejs';
export const dynamic='force-dynamic';
const routes:Record<string,string[]>={status:['GET'],policy:['GET','POST'],run:['POST'],events:['GET'],receipts:['GET'],approvals:['GET','POST'],benchmark:['GET']};
async function handle(request:NextRequest,context:{params:Promise<{path:string[]}>}){
  const path=(await context.params).path.join('/');
  if(!routes[path]?.includes(request.method))return NextResponse.json({message:'지원하지 않는 요청입니다.'},{status:404});
  const origin=process.env.AIDLP_CONSOLE_ORIGIN||'http://127.0.0.1:3100';
  if(request.method!=='GET'&&request.headers.get('origin')!==origin)return NextResponse.json({message:'요청 출처가 올바르지 않습니다.'},{status:403});
  const session=request.cookies.get('aidlp_session')?.value;
  if(!session)return NextResponse.json({message:'로그인이 필요합니다.'},{status:401});
  try{
    const auth=await fetch(`${process.env.AIDLP_API_ORIGIN||'http://127.0.0.1:3901'}/v1/admin/settings`,{headers:{authorization:`Bearer ${session}`},cache:'no-store',redirect:'error',signal:AbortSignal.timeout(5000)});
    if(!auth.ok)return NextResponse.json({message:'관리자 인증을 확인해 주세요.'},{status:auth.status===401?401:503});
    let body:Uint8Array|undefined;
    if(request.method==='POST'){
      if(!request.headers.get('content-type')?.startsWith('application/json'))return NextResponse.json({message:'JSON 요청이 필요합니다.'},{status:415});
      const reader=request.body?.getReader();const chunks:Uint8Array[]=[];let size=0;
      if(reader)while(true){const {done,value}=await reader.read();if(done)break;size+=value.length;if(size>6*1024*1024){await reader.cancel();return NextResponse.json({message:'파일은 최대 4MB까지 가능합니다.'},{status:413});}chunks.push(value);}
      body=new Uint8Array(size);let offset=0;for(const value of chunks){body.set(value,offset);offset+=value.length;}
    }
    // Support root launch and npm workspace launch; never include this file in build traces.
    const token=(await readFile(/* turbopackIgnore: true */ process.env.AIDLP_JUDGE_TOKEN_FILE||resolve(/* turbopackIgnore: true */ process.cwd(),process.cwd().endsWith('/apps/console')?'../../.local/judge-state/admin.token':'.local/judge-state/admin.token'),'utf8')).trim();
    const upstream=await fetch(`http://127.0.0.1:8310/v1/${path}`,{method:request.method,headers:{'content-type':'application/json',authorization:`Bearer ${token}`},body:body as BodyInit|undefined,cache:'no-store',redirect:'error',signal:AbortSignal.timeout(125000)});
    const data=await upstream.json();
    return NextResponse.json(upstream.ok?data:{message:typeof data.message==='string'?data.message:'요청 형식 또는 정책 값을 확인해 주세요.'},{status:upstream.status,headers:{'Cache-Control':'no-store'}});
  }catch{return NextResponse.json({message:'로컬 Judge 연결 또는 응답 확인에 실패했습니다. 실행 결과가 불명확하므로 자동 재전송하지 않습니다.'},{status:503});}
}
export const GET=handle;
export const POST=handle;
