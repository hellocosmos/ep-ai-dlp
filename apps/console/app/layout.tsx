import type {Metadata} from 'next';
import './globals.css';
export const metadata:Metadata={title:'EP AI DLP · Endpoint AI data protection',description:'Review policy, endpoint status, and data protection decisions.'};
export default function Layout({children}:{children:React.ReactNode}){return <html lang="en" suppressHydrationWarning><body>{children}</body></html>;}
