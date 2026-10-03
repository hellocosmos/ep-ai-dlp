export interface ServerConfig {
  databaseUrl:string; tenantId:string; signingKeyPath:string; adminUser:string;
  adminPassword?:string; publicUrl:string; enrollmentCaPem?:string; schema?:string;
}
export function configFromEnv(): ServerConfig {
  const required=(key:string)=>{const v=process.env[key];if(!v)throw new Error(`Missing ${key}`);return v;};
  return {databaseUrl:required('DATABASE_URL'),tenantId:required('AIDLP_TENANT_ID'),
    signingKeyPath:required('AIDLP_SIGNING_KEY'), adminUser:process.env.AIDLP_ADMIN_USER||'admin',
    adminPassword:process.env.AIDLP_ADMIN_PASSWORD,publicUrl:required('AIDLP_PUBLIC_URL'),
    enrollmentCaPem:process.env.AIDLP_ENROLLMENT_CA_PEM};
}
