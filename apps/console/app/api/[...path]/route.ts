import {NextRequest,NextResponse} from 'next/server';
export const runtime='nodejs';
export const dynamic='force-dynamic';
const routes=new Map([
  ['auth/login',['POST']],['auth/logout',['POST']],['admin/overview',['GET']],
  ['admin/policy',['GET','POST']],['admin/devices',['GET']],['admin/enrollments',['POST']],
  ['admin/events',['GET']],['admin/audit',['GET']],['admin/settings',['GET']],
]);
async function handle(request:NextRequest,context:{params:Promise<{path:string[]}>}){
  const path=(await context.params).path.join('/');
  const allowed=routes.get(path)||( /^admin\/devices\/[0-9a-f-]{36}\/revoke$/.test(path)?['POST']:[]);
  if(!allowed.includes(request.method))return NextResponse.json({message:'Unsupported request.'},{status:404});
  const origin=process.env.AIDLP_CONSOLE_ORIGIN||'http://127.0.0.1:3100';
  if(request.method!=='GET'&&request.headers.get('origin')!==origin)return NextResponse.json({message:'Invalid request origin.'},{status:403});
  const token=request.cookies.get('aidlp_session')?.value;
  if(path!=='auth/login'&&!token)return NextResponse.json({message:'Sign in required.'},{status:401});
  let body:Uint8Array|undefined;
  if(request.method==='POST'){
    if(!request.headers.get('content-type')?.startsWith('application/json'))return NextResponse.json({message:'JSON request required.'},{status:415});
    const reader=request.body?.getReader();const chunks:Uint8Array[]=[];let size=0;
    if(reader){while(true){const {done,value}=await reader.read();if(done)break;size+=value.length;if(size>65536){await reader.cancel();return NextResponse.json({message:'Request too large.'},{status:413});}chunks.push(value);}}
    body=new Uint8Array(size);let offset=0;for(const chunk of chunks){body.set(chunk,offset);offset+=chunk.length;}
  }
  try {
    const upstream=await fetch(`${process.env.AIDLP_API_ORIGIN||'http://127.0.0.1:3901'}/v1/${path}`,{
      method:request.method,headers:{'content-type':'application/json',...(token?{authorization:`Bearer ${token}`}:{})},
      body:body as BodyInit|undefined,cache:'no-store',redirect:'error',signal:AbortSignal.timeout(12000),
    });
    const data=await upstream.json();
    if(path==='auth/login'&&upstream.ok){
      const result=NextResponse.json({username:data.username},{headers:{'Cache-Control':'no-store'}});
      result.cookies.set('aidlp_session',data.session,{httpOnly:true,secure:origin.startsWith('https:'),sameSite:'strict',path:'/',maxAge:8*3600});return result;
    }
    const result=NextResponse.json(data,{status:upstream.status,headers:{'Cache-Control':'no-store'}});
    if((path==='auth/logout'&&upstream.ok)||upstream.status===401)result.cookies.delete('aidlp_session');
    return result;
  }catch{return NextResponse.json({message:'Management server unavailable. Try again later.'},{status:503});}
}
export const GET=handle;
export const POST=handle;
