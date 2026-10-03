import 'reflect-metadata';
import { Body,Controller,Get,Post,Param,Headers,Module,Inject,Injectable,BadRequestException,INestApplication,HttpException,Catch,ArgumentsHost,ExceptionFilter } from '@nestjs/common';
import { NestFactory } from '@nestjs/core';
import { json } from 'express';
import { z } from 'zod';
import { EnrollmentInput,LoginInput,PolicyInput,ReportSchema } from '@aidlp/contracts';
import { ServerConfig } from './config';
import { Database } from './db';
import { Auth } from './auth';
import { Policies } from './policies';
import { Devices } from './devices';
import { PolicySigner } from './signing';

const CONTEXT=Symbol('context');
export class Context {
  db:Database;auth:Auth;policies:Policies;devices:Devices;
  constructor(readonly config:ServerConfig){
    z.uuid().parse(config.tenantId);
    const publicUrl=new URL(config.publicUrl);
    if(publicUrl.username||publicUrl.password||publicUrl.search||publicUrl.hash||publicUrl.pathname!=='/'||
      (publicUrl.protocol!=='https:'&&!(publicUrl.protocol==='http:'&&['localhost','127.0.0.1','[::1]'].includes(publicUrl.hostname))))throw new Error('Public URL requires HTTPS or loopback HTTP');
    this.db=new Database(config);this.auth=new Auth(this.db,config);this.policies=new Policies(this.db,new PolicySigner(config.signingKeyPath),config.tenantId);this.devices=new Devices(this.db);
  }
  async initialize(){
    await this.db.migrate();
    const org=(await this.db.pool.query('SELECT id FROM organization')).rows;
    if(org.length && (org.length!==1||org[0].id!==this.config.tenantId))throw new Error('Organization configuration does not match database');
    await this.db.pool.query('INSERT INTO organization VALUES($1) ON CONFLICT DO NOTHING',[this.config.tenantId]);
    await this.auth.bootstrap();await this.policies.bootstrap();
  }
}
function parse<T>(schema:z.ZodType<T>,value:unknown):T{const result=schema.safeParse(value);if(!result.success)throw new BadRequestException('Invalid request schema');return result.data;}
@Catch()
class SafeErrors implements ExceptionFilter{
  catch(exception:unknown,host:ArgumentsHost){const response=host.switchToHttp().getResponse();
    const status=exception instanceof HttpException?exception.getStatus():500;
    const code=status===500?'Internal server error':exception instanceof HttpException?exception.message:'Request failed';
    response.status(status).json({statusCode:status,message:code});}
}
@Controller('v1')
class Api {
  constructor(@Inject(CONTEXT) private c:Context){}
  @Get('health') async health(){await this.c.db.pool.query('SELECT 1');return {ok:true,service:'aidlp-control-plane'};}
  @Post('auth/login') login(@Body() body:unknown){const value=parse(LoginInput,body);return this.c.auth.login(value.username,value.password);}
  @Post('auth/logout') logout(@Headers('authorization') auth?:string){return this.c.auth.logout(auth);}
  @Get('admin/overview') async overview(@Headers('authorization') auth?:string){
    const username=await this.c.auth.administrator(auth);const policy=await this.c.policies.current();
    const devices=await this.c.devices.list();const events=await this.c.devices.events();
    const count=(await this.c.db.pool.query("SELECT count(*) AS total,count(*) FILTER (WHERE payload->>'action'='block') AS blocked,count(*) FILTER (WHERE payload->>'action'='monitor') AS monitored FROM events")).rows[0];
    return {username,tenant_id:this.c.config.tenantId,policy,devices,events:events.slice(0,8),counts:{events:Number(count.total),blocked:Number(count.blocked),monitored:Number(count.monitored)},scope:'single_organization_pilot'};
  }
  @Get('admin/policy') async policy(@Headers('authorization') auth?:string){await this.c.auth.administrator(auth);return this.c.policies.current();}
  @Post('admin/policy') async publish(@Body() body:unknown,@Headers('authorization') auth?:string){const user=await this.c.auth.administrator(auth);const value=parse(PolicyInput,body);return this.c.policies.publish(value.expected_revision,value.rules,user);}
  @Get('admin/devices') async devices(@Headers('authorization') auth?:string){await this.c.auth.administrator(auth);return this.c.devices.list();}
  @Post('admin/devices/:id/revoke') async revoke(@Param('id') id:string,@Headers('authorization') auth?:string){const user=await this.c.auth.administrator(auth);parse(z.uuid(),id);return this.c.devices.revoke(id,user);}
  @Post('admin/enrollments') async enrollments(@Body() body:unknown,@Headers('authorization') auth?:string){
    const user=await this.c.auth.administrator(auth);parse(z.strictObject({}),body);
    return {...await this.c.devices.issue(user),server_url:this.c.config.publicUrl,tenant_id:this.c.config.tenantId,
      public_key_base64:this.c.policies.signer.publicKeyBase64,key_id:this.c.policies.signer.keyId,
      ca_pem:this.c.config.enrollmentCaPem||null};
  }
  @Get('admin/events') async events(@Headers('authorization') auth?:string){await this.c.auth.administrator(auth);return this.c.devices.events();}
  @Get('admin/audit') async audit(@Headers('authorization') auth?:string){await this.c.auth.administrator(auth);return (await this.c.db.pool.query('SELECT * FROM audit ORDER BY id DESC LIMIT 200')).rows;}
  @Get('admin/settings') async settings(@Headers('authorization') auth?:string){await this.c.auth.administrator(auth);return {tenant_id:this.c.config.tenantId,server_url:this.c.config.publicUrl,key_id:this.c.policies.signer.keyId,policy_validity_days:7,raw_content_collection:false,scope:'single_organization_pilot'};}
  @Post('enroll') enroll(@Body() body:unknown){const value=parse(EnrollmentInput,body);return this.c.devices.enroll(value.token,value.name,value.platform);}
  @Get('device/policy') async devicePolicy(@Headers('authorization') auth?:string){const id=await this.c.auth.device(auth);return this.c.policies.envelope(id);}
  @Post('device/report') async report(@Body() body:unknown,@Headers('authorization') auth?:string){const id=await this.c.auth.device(auth);return this.c.devices.report(id,parse(ReportSchema,body));}
}
export async function createApplication(config:ServerConfig):Promise<{app:INestApplication;context:Context}>{
  const context=new Context(config);try{await context.initialize();}catch(error){await context.db.close();throw error;}
  @Module({controllers:[Api],providers:[{provide:CONTEXT,useValue:context}]}) class AppModule{}
  const app=await NestFactory.create(AppModule,{logger:false,bodyParser:false});
  app.use(json({limit:'64kb',strict:true}));
  app.use((_req:any,res:any,next:()=>void)=>{res.setHeader('Cache-Control','no-store');res.setHeader('X-Content-Type-Options','nosniff');next();});
  app.useGlobalFilters(new SafeErrors());
  return {app,context};
}
