using System.Diagnostics;
using System.Net;
using System.Net.Security;
using System.Security.Cryptography.X509Certificates;
using System.Text;
using System.Text.Json;
using AiDlp.Proxy;

return await Run();
async Task<int> Run()
{
    var root = args[0];
    var state = args[1];
    Directory.CreateDirectory(state);
    var checks = new List<object>();
    var failures = 0;
    void Check(string name, bool passed, string detail)
    {
        if (!passed) failures++;
        checks.Add(new { name, passed, detail });
        Console.WriteLine($"{(passed ? "PASS" : "FAIL")} {name}: {detail}");
    }
    using var ca = Certificates.Authority("Hudsucker Synthetic Origin");
    using var certificate = Certificates.Localhost(ca);
    await using var origin = new OriginFixture(certificate);
    await origin.Start();
    var originCa = Path.Combine(state, "origin-ca.pem");
    File.WriteAllText(originCa, ca.ExportCertificatePem());
    var processes = new List<Process>();
    async Task<(Process Process, int Port, X509Certificate2 Ca)> Start(string label, int delay, string? agent = null)
    {
        var directory = Path.Combine(state, label); Directory.CreateDirectory(directory);
        var info = new ProcessStartInfo(Path.Combine(root, "source", "target", "x86_64-pc-windows-gnullvm", "debug", "aidlp-hudsucker-spike.exe"))
        { UseShellExecute = false, RedirectStandardInput = true, RedirectStandardOutput = true, RedirectStandardError = true };
        foreach (var value in new[] { "--state", directory, "--origin-ca", originCa, "--python", @"C:\ai-dlp\venv\Scripts\python.exe",
            "--agent", agent ?? @"C:\ai-dlp\titanium-poc\source\agent", "--delay-ms", delay.ToString() }) info.ArgumentList.Add(value);
        var p = Process.Start(info)!; processes.Add(p);
        var diagnostics = p.StandardError.ReadToEndAsync(); // Synthetic fixture only.
        var line = await p.StandardOutput.ReadLineAsync().WaitAsync(TimeSpan.FromSeconds(15));
        if (line is null) throw new InvalidOperationException($"Synthetic proxy startup failed: exit={p.ExitCode}; {await diagnostics}");
        using var ready = JsonDocument.Parse(line);
        return (p, ready.RootElement.GetProperty("port").GetInt32(), X509Certificate2.CreateFromPem(File.ReadAllText(Path.Combine(directory,"proxy-ca.pem"))));
    }
    HttpClient Client(int port, X509Certificate2 proxyCa, Action<string>? thumb = null)
    {
        var handler = new SocketsHttpHandler { Proxy = new WebProxy($"http://127.0.0.1:{port}") { BypassProxyOnLocal = false },
            UseProxy = true, AllowAutoRedirect = false, MaxConnectionsPerServer = 1, EnableMultipleHttp2Connections = false };
        handler.SslOptions.RemoteCertificateValidationCallback = (_, cert, _, errors) =>
        {
            if (cert is null || (errors & SslPolicyErrors.RemoteCertificateNameMismatch) != 0) return false;
            using var leaf = new X509Certificate2(cert); using var chain = new X509Chain();
            chain.ChainPolicy.TrustMode = X509ChainTrustMode.CustomRootTrust;
            chain.ChainPolicy.CustomTrustStore.Add(proxyCa); chain.ChainPolicy.CustomTrustStore.Add(ca);
            chain.ChainPolicy.RevocationMode = X509RevocationMode.NoCheck; chain.ChainPolicy.DisableCertificateDownloads = true;
            if (!chain.Build(leaf)) return false; thumb?.Invoke(leaf.Thumbprint); return true;
        };
        return new HttpClient(handler) { Timeout = TimeSpan.FromSeconds(25) };
    }
    HttpRequestMessage Request(string body, int major=2, string type="text/plain", string path="/echo", string host="localhost", int? port=null)
    {
        var req = new HttpRequestMessage(HttpMethod.Post,$"https://{host}:{port ?? origin.Port}{path}")
        { Version = new Version(major,major==1?1:0), VersionPolicy=HttpVersionPolicy.RequestVersionExact, Content=new ByteArrayContent(Encoding.UTF8.GetBytes(body)) };
        req.Content.Headers.ContentType = new(type); return req;
    }
    try
    {
        var proxy = await Start("main", 100); using var proxyCa=proxy.Ca;
        using var client = Client(proxy.Port,proxyCa);
        async Task Block(string name, HttpRequestMessage req)
        {
            using(req) { var before=origin.Requests; using var response=await client.SendAsync(req);
                Check(name,response.StatusCode==HttpStatusCode.Forbidden && origin.Requests==before,
                    $"status={(int)response.StatusCode}, origin_delta={origin.Requests-before}, HTTP/{response.Version}"); }
        }
        foreach(var major in new[]{1,2})
        {
            using var req=Request("safe synthetic hello",major); using var response=await client.SendAsync(req);
            using var data=JsonDocument.Parse(await response.Content.ReadAsStringAsync());
            // A H1 client may be upgraded to H2 on the origin leg; verify H2 end-to-end explicitly.
            Check("safe_http_"+major,response.IsSuccessStatusCode && response.Version.Major==major &&
                data.RootElement.GetProperty("body").GetString()=="safe synthetic hello" &&
                (major!=2 || data.RootElement.GetProperty("protocol").GetString()=="HTTP/2"),
                $"client=HTTP/{response.Version}, origin={data.RootElement.GetProperty("protocol").GetString()}");
            await Block("pii_http_"+major,Request("900101-1234567",major));
            await Block("escaped_json_http_"+major,Request("{\"prompt\":\"alice\\u0040example.com\"}",major,"application/json"));
        }
        string? selectedThumb=null;
        using(var tls=Client(proxy.Port,proxyCa,t=>selectedThumb=t))
        using(var req=Request("safe"))
        using(var res=await tls.SendAsync(req))
            Check("selected_mitm",res.IsSuccessStatusCode && selectedThumb!=certificate.Thumbprint,"proxy certificate validated");
        string? excludedThumb=null;
        using(var tls=Client(proxy.Port,proxyCa,t=>excludedThumb=t))
        using(var req=Request("safe",host:"127.0.0.1"))
        using(var res=await tls.SendAsync(req))
            Check("excluded_opaque",res.IsSuccessStatusCode && excludedThumb==certificate.Thumbprint,"original origin certificate preserved");
        await Block("pem",Request("-----BEGIN PRIVATE KEY-----\nSYNTHETIC_BODY\n-----END PRIVATE KEY-----"));
        await Block("query",Request("safe",path:"/echo?email=alice%40example.com"));
        await Block("malformed_json",Request("{",type:"application/json"));
        await Block("duplicate_json",Request("{\"p\":\"alice@example.com\",\"p\":\"safe\"}",type:"application/json"));
        await Block("multipart",Request("synthetic",type:"multipart/form-data"));
        await Block("oversize",Request(new string('x',1048577)));
        var invalid=Request("safe");invalid.Content=new ByteArrayContent(new byte[]{255,254,65});invalid.Content.Headers.ContentType=new("text/plain");
        await Block("invalid_utf8",invalid);
        var compressed=Request("synthetic");compressed.Content!.Headers.ContentEncoding.Add("gzip");await Block("encoded",compressed);
        var chunked=Request("safe",1);chunked.Content=new StreamContent(new MemoryStream(Encoding.UTF8.GetBytes("alice@example.com")));chunked.Content.Headers.ContentType=new("text/plain");chunked.Headers.TransferEncodingChunked=true;
        await Block("chunked_pii",chunked);
        var upgrade=Request("",1);upgrade.Headers.TryAddWithoutValidation("Connection","Upgrade");upgrade.Headers.TryAddWithoutValidation("Upgrade","websocket");await Block("upgrade",upgrade);

        using(var large=Request(new string('x',200000)))
        using(var res=await client.SendAsync(large))
            Check("http2_large_body",res.IsSuccessStatusCode && res.Version.Major==2,"200 KB buffered across HTTP/2 flow-control windows");
        var before=origin.Requests;
        var mixed=Enumerable.Range(0,12).Select(async i=>{
            using var req=Request(i%2==0?"safe concurrent":"alice@example.com");
            using var response=await client.SendAsync(req);
            return response.Version.Major==2 && (i%2==0?response.IsSuccessStatusCode:response.StatusCode==HttpStatusCode.Forbidden);
        }).ToArray();
        var results=await Task.WhenAll(mixed);
        Check("http2_mixed_concurrency",results.All(x=>x) && origin.Requests-before==6,
            $"requests=12, allowed=6, blocked=6, origin_delta={origin.Requests-before}; client connections capped at 1");

        before=origin.Requests;
        using(var slowContent=new SlowContent())
        using(var slowRequest=Request("safe"))
        {
            slowRequest.Content=slowContent;
            var slowPending=client.SendAsync(slowRequest);
            await slowContent.Started.Task.WaitAsync(TimeSpan.FromSeconds(3));
            await Block("parallel_block_during_partial_body",Request("alice@example.com"));
            var stayedLocal=origin.Requests==before;
            slowContent.Release.TrySetResult();
            using var slowResponse=await slowPending;
            Check("http2_partial_body_isolation",stayedLocal && slowResponse.IsSuccessStatusCode && origin.Requests==before+1,
                "second stream blocked while first stream waited for its final body bytes");
        }

        using(var req=new HttpRequestMessage(HttpMethod.Get,$"https://localhost:{origin.Port}/sse") { Version=HttpVersion.Version20,VersionPolicy=HttpVersionPolicy.RequestVersionExact })
        using(var res=await client.SendAsync(req,HttpCompletionOption.ResponseHeadersRead))
        using(var stream=new StreamReader(await res.Content.ReadAsStreamAsync()))
        {
            var first=await stream.ReadLineAsync().WaitAsync(TimeSpan.FromSeconds(3));
            var early=first=="data: first" && !origin.ReleaseSecondEvent.Task.IsCompleted;
            origin.ReleaseSecondEvent.TrySetResult();var rest=await stream.ReadToEndAsync().WaitAsync(TimeSpan.FromSeconds(3));
            Check("http2_sse",early && rest.Contains("data: second") && res.Version.Major==2,"first event arrived before second was released");
        }
        var delayed=await Start("delayed",1200);using var delayedCa=delayed.Ca;using var delayedClient=Client(delayed.Port,delayedCa);
        using(var warm=Request("safe warmup"))
        using(var warmResponse=await delayedClient.SendAsync(warm))
            warmResponse.EnsureSuccessStatusCode();
        foreach(var sensitive in new[]{false,true})
        {
            before=origin.Requests;using var req=Request(sensitive?"alice@example.com":"safe delayed");
            var pending=delayedClient.SendAsync(req);await Task.Delay(400);
            var gated=!pending.IsCompleted && origin.Requests==before;using var res=await pending;
            Check(sensitive?"delayed_block_no_arrival":"delayed_allow_gate",gated &&
                (sensitive ? res.StatusCode==HttpStatusCode.Forbidden && origin.Requests==before : res.IsSuccessStatusCode && origin.Requests==before+1),
                $"pending_at_400ms={gated}, final_status={(int)res.StatusCode}, origin_delta={origin.Requests-before}");
        }
        using(var badCa=Certificates.Authority("Untrusted Synthetic Origin"))
        using(var badCert=Certificates.Localhost(badCa))
        {
            await using var badOrigin=new OriginFixture(badCert);await badOrigin.Start();
            using var req=Request("safe",port:badOrigin.Port);using var res=await client.SendAsync(req);
            Check("untrusted_origin",!res.IsSuccessStatusCode && badOrigin.Requests==0,$"status={(int)res.StatusCode}, origin_delta={badOrigin.Requests}");
        }
        foreach(var mode in new[]{"failed","hung"})
        {
            var stub=Path.Combine(state,mode+"-agent");Directory.CreateDirectory(Path.Combine(stub,"aidlp"));
            File.WriteAllText(Path.Combine(stub,"aidlp","__init__.py"),"");
            File.WriteAllText(Path.Combine(stub,"aidlp","worker.py"),mode=="hung"?"import time\ntime.sleep(30)\n":"raise SystemExit(2)\n");
            var stopped=await Start(mode,0,stub);using var stoppedCa=stopped.Ca;using var stoppedClient=Client(stopped.Port,stoppedCa);
            before=origin.Requests;var watch=Stopwatch.StartNew();using var req=Request("safe synthetic");using var res=await stoppedClient.SendAsync(req);
            Check("worker_"+mode,res.StatusCode==HttpStatusCode.Forbidden && origin.Requests==before && watch.Elapsed.TotalSeconds<9,
                $"status={(int)res.StatusCode}, origin_delta={origin.Requests-before}, elapsed_ms={watch.ElapsedMilliseconds}");
        }
        using(var excludedOrigin=new System.Net.Sockets.TcpListener(IPAddress.Loopback,0))
        using(var excludedClient=new System.Net.Sockets.TcpClient())
        {
            excludedOrigin.Start();var excludedPort=((IPEndPoint)excludedOrigin.LocalEndpoint).Port;
            var exchange=Task.Run(async ()=>{
                using var peer=await excludedOrigin.AcceptTcpClientAsync();using var stream=peer.GetStream();var b=new byte[1];
                var count=await stream.ReadAsync(b);await stream.WriteAsync(new byte[]{(byte)'Y'});return count==1 && b[0]=='X';
            });
            await excludedClient.ConnectAsync(IPAddress.Loopback,proxy.Port);using var stream=excludedClient.GetStream();
            await stream.WriteAsync(Encoding.ASCII.GetBytes($"CONNECT 127.0.0.1:{excludedPort} HTTP/1.1\r\nHost: 127.0.0.1:{excludedPort}\r\n\r\n"));
            var header=new List<byte>();var one=new byte[1];
            while(header.Count<4096 && !Encoding.ASCII.GetString(header.ToArray()).EndsWith("\r\n\r\n"))
            {if(await stream.ReadAsync(one)==0)throw new IOException("CONNECT closed");header.Add(one[0]);}
            await stream.WriteAsync(new byte[]{(byte)'X'});var received=await stream.ReadAsync(one).AsTask().WaitAsync(TimeSpan.FromSeconds(3));
            Check("excluded_one_byte_protocol",received==1 && one[0]=='Y' && await exchange.WaitAsync(TimeSpan.FromSeconds(3)),
                "excluded CONNECT preserved a protocol that sends one byte before waiting for its reply");
            excludedOrigin.Stop();
        }
        using(var rawOrigin=new System.Net.Sockets.TcpListener(IPAddress.Loopback,0))
        using(var rawClient=new System.Net.Sockets.TcpClient())
        {
            rawOrigin.Start();var rawPort=((IPEndPoint)rawOrigin.LocalEndpoint).Port;
            await rawClient.ConnectAsync(IPAddress.Loopback,proxy.Port);using var rawStream=rawClient.GetStream();
            await rawStream.WriteAsync(Encoding.ASCII.GetBytes($"CONNECT localhost:{rawPort} HTTP/1.1\r\nHost: localhost:{rawPort}\r\n\r\n"));
            var header=new List<byte>();var one=new byte[1];
            while(header.Count<4096 && !Encoding.ASCII.GetString(header.ToArray()).EndsWith("\r\n\r\n"))
            {if(await rawStream.ReadAsync(one)==0)throw new IOException("CONNECT closed");header.Add(one[0]);}
            await rawStream.WriteAsync(Encoding.ASCII.GetBytes("TEST-synthetic-unsupported-protocol"));
            var closed=false;
            try{closed=await rawStream.ReadAsync(one).AsTask().WaitAsync(TimeSpan.FromSeconds(1))==0;}
            catch(TimeoutException){}
            catch(IOException e) when(e.InnerException is System.Net.Sockets.SocketException socket && socket.SocketErrorCode==System.Net.Sockets.SocketError.ConnectionReset){closed=true;}
            var connected=rawOrigin.Pending();
            if(connected){using var leaked=await rawOrigin.AcceptTcpClientAsync();}
            Check("selected_unknown_protocol_no_tunnel",closed && !connected,$"client_closed={closed}, origin_tcp_connection={connected}");
            rawOrigin.Stop();
        }
        // A selected CONNECT must remain intercepted even when ClientHello arrives in tiny reads.
        before=origin.Requests;
        using(var tcp=new System.Net.Sockets.TcpClient())
        {
            await tcp.ConnectAsync(IPAddress.Loopback,proxy.Port);
            using var network=tcp.GetStream();
            await network.WriteAsync(Encoding.ASCII.GetBytes($"CONNECT localhost:{origin.Port} HTTP/1.1\r\nHost: localhost:{origin.Port}\r\n\r\n"));
            var header=new List<byte>();var one=new byte[1];
            while(header.Count<4096 && !Encoding.ASCII.GetString(header.ToArray()).EndsWith("\r\n\r\n"))
            { if(await network.ReadAsync(one)==0)throw new IOException("CONNECT closed");header.Add(one[0]); }
            using var fragmented=new FragmentFirstWriteStream(network);
            string? peer=null;
            using var ssl=new SslStream(fragmented,false,(_,cert,_,errors)=>{
                if(cert is null || (errors&SslPolicyErrors.RemoteCertificateNameMismatch)!=0)return false;
                using var leaf=new X509Certificate2(cert);using var chain=new X509Chain();
                chain.ChainPolicy.TrustMode=X509ChainTrustMode.CustomRootTrust;chain.ChainPolicy.CustomTrustStore.Add(ca);chain.ChainPolicy.CustomTrustStore.Add(proxyCa);
                chain.ChainPolicy.RevocationMode=X509RevocationMode.NoCheck;chain.ChainPolicy.DisableCertificateDownloads=true;
                peer=leaf.Thumbprint;return chain.Build(leaf);
            });
            await ssl.AuthenticateAsClientAsync(new SslClientAuthenticationOptions{TargetHost="localhost",ApplicationProtocols=[SslApplicationProtocol.Http11]}).WaitAsync(TimeSpan.FromSeconds(8));
            var body="alice@example.com";
            await ssl.WriteAsync(Encoding.ASCII.GetBytes($"POST /echo HTTP/1.1\r\nHost: localhost:{origin.Port}\r\nContent-Type: text/plain\r\nContent-Length: {body.Length}\r\nConnection: close\r\n\r\n{body}"));
            using var reader=new StreamReader(ssl);var status=await reader.ReadLineAsync().WaitAsync(TimeSpan.FromSeconds(8));
            Check("fragmented_clienthello_stays_inspected",status?.Contains("403")==true && peer!=certificate.Thumbprint && origin.Requests==before,
                $"status={status}, original_certificate={peer==certificate.Thumbprint}, origin_delta={origin.Requests-before}");
        }

    }
    catch(Exception e) { Check("test_exception",false,e.GetType().Name); Console.Error.WriteLine(e); }
    finally
    {
        foreach(var p in processes)
        {
            if(!p.HasExited){await p.StandardInput.WriteLineAsync("stop");await p.StandardInput.FlushAsync();
                try{await p.WaitForExitAsync().WaitAsync(TimeSpan.FromSeconds(5));}catch(TimeoutException){p.Kill(true);await p.WaitForExitAsync();}}
            p.Dispose();
        }
    }
    try
    {
        var forbidden=new[]{"alice@example.com","900101-1234567","SYNTHETIC_BODY","safe synthetic hello"};
        Check("metadata_only",Directory.EnumerateFiles(state,"events.db*",SearchOption.AllDirectories).All(p=>{
            var text=Encoding.UTF8.GetString(File.ReadAllBytes(p));return forbidden.All(v=>!text.Contains(v));}),"synthetic payloads absent from SQLite files");
    }
    catch(Exception e){Check("metadata_read_error",false,e.GetType().Name);}
    var report=new { timestamp_utc=DateTimeOffset.UtcNow,success=failures==0,failures,checks,
        engine="hudsucker 0.25.0; local selected-CONNECT fail-closed patch; manifest selects ring", scope="throwaway localhost-only Windows native spike; no OS proxy or trust changes" };
    File.WriteAllText(Path.Combine(state,"self-test.json"),JsonSerializer.Serialize(report,new JsonSerializerOptions{WriteIndented=true}));
    Console.WriteLine($"RESULT: {checks.Count-failures}/{checks.Count}");return failures==0?0:1;
}

sealed class SlowContent : HttpContent
{
    public readonly TaskCompletionSource Started=new(TaskCreationOptions.RunContinuationsAsynchronously);
    public readonly TaskCompletionSource Release=new(TaskCreationOptions.RunContinuationsAsynchronously);
    public SlowContent(){Headers.ContentType=new("text/plain");}
    protected override bool TryComputeLength(out long length){length=0;return false;}
    protected override async Task SerializeToStreamAsync(Stream stream, TransportContext? context)
    {
        await stream.WriteAsync(Encoding.UTF8.GetBytes("safe partial "));await stream.FlushAsync();Started.TrySetResult();
        await Release.Task.WaitAsync(TimeSpan.FromSeconds(8));await stream.WriteAsync(Encoding.UTF8.GetBytes("body"));
    }
}

sealed class FragmentFirstWriteStream(Stream inner) : Stream
{
    private bool first=true;
    public override bool CanRead=>true;public override bool CanSeek=>false;public override bool CanWrite=>true;
    public override long Length=>throw new NotSupportedException();public override long Position{get=>throw new NotSupportedException();set=>throw new NotSupportedException();}
    public override void Flush()=>inner.Flush();public override Task FlushAsync(CancellationToken c)=>inner.FlushAsync(c);
    public override int Read(byte[] b,int o,int n)=>inner.Read(b,o,n);
    public override Task<int> ReadAsync(byte[] b,int o,int n,CancellationToken c)=>inner.ReadAsync(b,o,n,c);
    public override ValueTask<int> ReadAsync(Memory<byte> b,CancellationToken c=default)=>inner.ReadAsync(b,c);
    public override void Write(byte[] b,int o,int n)=>WriteAsync(b.AsMemory(o,n)).AsTask().GetAwaiter().GetResult();
    public override Task WriteAsync(byte[] b,int o,int n,CancellationToken c)=>WriteAsync(b.AsMemory(o,n),c).AsTask();
    public override async ValueTask WriteAsync(ReadOnlyMemory<byte> b,CancellationToken c=default)
    {
        if(first && b.Length>1){first=false;await inner.WriteAsync(b[..1],c);await inner.FlushAsync(c);await Task.Delay(350,c);await inner.WriteAsync(b[1..],c);}
        else await inner.WriteAsync(b,c);
    }
    public override long Seek(long o,SeekOrigin s)=>throw new NotSupportedException();public override void SetLength(long n)=>throw new NotSupportedException();
}
