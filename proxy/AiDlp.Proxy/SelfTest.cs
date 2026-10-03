using System.Diagnostics;
using System.Net;
using System.Net.Http.Headers;
using System.Net.Security;
using System.Security.Cryptography.X509Certificates;
using System.Text;
using System.Text.Json;

namespace AiDlp.Proxy;

internal static class SelfTest
{
    private sealed record Check(string Name, bool Passed, string Detail);

    public static async Task<int> Run(string python, string agent, string state, bool probeHttp2 = false)
    {
        var checks = new List<Check>();
        var clock = Stopwatch.StartNew();
        using var authority = Certificates.Authority("AI DLP Synthetic Origin");
        using var originCertificate = Certificates.Localhost(authority);
        await using var origin = new OriginFixture(originCertificate);
        await origin.Start();
        using var worker = new InspectorWorker(python, agent, Path.Combine(state, "events.db"));
        using var proxy = new DlpProxy(worker, ["localhost"], 0, originCertificate, origin.Port, probeHttp2);
        proxy.Start();
        var versions = probeHttp2 ? new[] { HttpVersion.Version11, HttpVersion.Version20 }
            : new[] { HttpVersion.Version11 };
        var inspectedVersion = probeHttp2 ? HttpVersion.Version20 : HttpVersion.Version11;

        void Record(string name, bool pass, string detail)
        {
            checks.Add(new(name, pass, detail));
            Console.WriteLine($"{(pass ? "PASS" : "FAIL")} {name}: {detail}");
        }

        HttpClient Client(Action<string>? thumbprint = null, bool useProxy = true, DlpProxy? selectedProxy = null)
        {
            selectedProxy ??= proxy;
            var handler = new HttpClientHandler
            {
                Proxy = new WebProxy($"http://127.0.0.1:{selectedProxy.Port}") { BypassProxyOnLocal = false },
                UseProxy = useProxy, AllowAutoRedirect = false
            };
            handler.ServerCertificateCustomValidationCallback = (_, certificate, _, errors) =>
            {
                if (certificate is null || (errors & SslPolicyErrors.RemoteCertificateNameMismatch) != 0)
                    return false;
                using var chain = new X509Chain();
                chain.ChainPolicy.TrustMode = X509ChainTrustMode.CustomRootTrust;
                chain.ChainPolicy.CustomTrustStore.Add(selectedProxy.Root);
                chain.ChainPolicy.CustomTrustStore.Add(authority);
                chain.ChainPolicy.RevocationMode = X509RevocationMode.NoCheck;
                chain.ChainPolicy.DisableCertificateDownloads = true;
                if (!chain.Build(certificate)) return false;
                thumbprint?.Invoke(certificate.Thumbprint);
                return true;
            };
            return new HttpClient(handler) { Timeout = TimeSpan.FromSeconds(20) };
        }

        HttpRequestMessage Request(string body, Version version, string path = "/echo",
            string contentType = "text/plain", string host = "localhost")
        {
            var req = new HttpRequestMessage(HttpMethod.Post, $"https://{host}:{origin.Port}{path}")
            {
                Version = version, VersionPolicy = HttpVersionPolicy.RequestVersionExact,
                Content = new ByteArrayContent(Encoding.UTF8.GetBytes(body))
            };
            req.Content.Headers.ContentType = MediaTypeHeaderValue.Parse(contentType);
            return req;
        }

        using var client = Client();
        async Task Block(string name, HttpRequestMessage request)
        {
            using (request)
            {
                var before = origin.Requests;
                using var response = await client.SendAsync(request);
                Record(name, (response.StatusCode is HttpStatusCode.Forbidden or HttpStatusCode.RequestEntityTooLarge)
                       && origin.Requests == before,
                    $"status={(int)response.StatusCode}, origin_delta={origin.Requests - before}, HTTP/{response.Version}");
            }
        }

        try
        {
            using (var direct = Client(useProxy: false))
            using (var response = await direct.GetAsync($"https://localhost:{origin.Port}/echo"))
                Record("direct_fixture_tls", response.IsSuccessStatusCode, "fixture certificate validated without proxy");
            Record("exact_host_policy", proxy.Selects("LOCALHOST.") && !proxy.Selects("localhost.evil.test")
                && !proxy.Selects("notlocalhost") && !proxy.Selects("127.0.0.1"), "exact canonical host matching");

            foreach (var version in versions)
            {
                using var request = Request("safe synthetic hello", version);
                using var response = await client.SendAsync(request);
                using var content = JsonDocument.Parse(await response.Content.ReadAsStringAsync());
                var expected = version.Major == 2 ? "HTTP/2" : "HTTP/1.1";
                Record("safe_http_" + version, response.IsSuccessStatusCode && response.Version == version &&
                    content.RootElement.GetProperty("body").GetString() == "safe synthetic hello" &&
                    content.RootElement.GetProperty("protocol").GetString() == expected,
                    $"client=HTTP/{response.Version}, origin={content.RootElement.GetProperty("protocol").GetString()}");
            }

            string? selectedThumb = null;
            using (var tlsClient = Client(t => selectedThumb = t))
            using (var req = Request("safe", HttpVersion.Version11))
            using (var response = await tlsClient.SendAsync(req))
                Record("selected_host_mitm", response.IsSuccessStatusCode && selectedThumb is not null &&
                    selectedThumb != originCertificate.Thumbprint, "client verified proxy-issued certificate");

            string? excludedThumb = null;
            var tunnelsBefore = proxy.Tunnels;
            using (var tlsClient = Client(t => excludedThumb = t))
            using (var req = Request("safe", HttpVersion.Version11, host: "127.0.0.1"))
            using (var response = await tlsClient.SendAsync(req))
                Record("excluded_host_opaque", response.IsSuccessStatusCode &&
                    excludedThumb == originCertificate.Thumbprint && proxy.Tunnels > tunnelsBefore,
                    "original origin certificate preserved through CONNECT");

            if (!probeHttp2)
            {
                var before = origin.Requests;
                var refused = false;
                try
                {
                    using var strictH2 = Client();
                    using var req = Request("must not arrive", HttpVersion.Version20);
                    using var response = await strictH2.SendAsync(req);
                }
                catch (HttpRequestException) { refused = true; }
                Record("unsafe_http2_disabled", refused && origin.Requests == before,
                    $"strict h2 refused, origin_delta={origin.Requests - before}");
            }

            foreach (var version in versions)
            {
                await Block("pii_block_" + version, Request("900101-1234567", version));
                await Block("json_escape_block_" + version,
                    Request("{\"prompt\":\"alice\\u0040example.com\"}", version, contentType: "application/json"));
            }
            await Block("pem_body_block", Request("-----BEGIN PRIVATE KEY-----\nSYNTHETIC_BODY\n-----END PRIVATE KEY-----",
                inspectedVersion));
            await Block("query_block", Request("safe", inspectedVersion, "/echo?email=alice%40example.com"));
            await Block("malformed_json_block", Request("{", inspectedVersion, contentType: "application/json"));
            await Block("duplicate_json_key_block", Request("{\"p\":\"alice@example.com\",\"p\":\"safe\"}",
                inspectedVersion, contentType: "application/json"));
            await Block("multipart_block", Request("synthetic file", inspectedVersion,
                contentType: "multipart/form-data; boundary=synthetic"));
            await Block("oversize_block", Request(new string('x', DlpProxy.MaxBody + 1), inspectedVersion));
            var chunked = Request("safe", inspectedVersion);
            chunked.Content = new StreamContent(new MemoryStream(Encoding.UTF8.GetBytes("alice@example.com")));
            chunked.Content.Headers.ContentType = new("text/plain");
            if (inspectedVersion == HttpVersion.Version11) chunked.Headers.TransferEncodingChunked = true;
            await Block("chunked_pii_block", chunked);
            var compressed = Request("not forwarded", inspectedVersion);
            compressed.Content!.Headers.ContentEncoding.Add("gzip");
            await Block("encoded_body_block", compressed);
            var invalidUtf8 = Request("safe", inspectedVersion);
            invalidUtf8.Content = new ByteArrayContent([0xff, 0xfe, 0x41]);
            invalidUtf8.Content.Headers.ContentType = new("text/plain");
            await Block("invalid_utf8_block", invalidUtf8);
            var websocket = Request("", HttpVersion.Version11);
            websocket.Headers.TryAddWithoutValidation("Connection", "Upgrade");
            websocket.Headers.TryAddWithoutValidation("Upgrade", "websocket");
            await Block("websocket_unsupported_block", websocket);

            using (var request = new HttpRequestMessage(HttpMethod.Get, $"https://localhost:{origin.Port}/sse")
            { Version = inspectedVersion, VersionPolicy = HttpVersionPolicy.RequestVersionExact })
            using (var response = await client.SendAsync(request, HttpCompletionOption.ResponseHeadersRead))
            using (var stream = new StreamReader(await response.Content.ReadAsStreamAsync()))
            {
                var first = await stream.ReadLineAsync().WaitAsync(TimeSpan.FromSeconds(3));
                var streamed = first == "data: first" && !origin.ReleaseSecondEvent.Task.IsCompleted;
                origin.ReleaseSecondEvent.TrySetResult();
                var rest = await stream.ReadToEndAsync().WaitAsync(TimeSpan.FromSeconds(3));
                Record("sse_streaming", streamed && rest.Contains("data: second") &&
                    response.Version == inspectedVersion,
                    $"HTTP/{response.Version}; first event received before origin released second event");
            }

            using (var badAuthority = Certificates.Authority("AI DLP Untrusted Origin"))
            using (var badCertificate = Certificates.Localhost(badAuthority))
            {
                await using var badOrigin = new OriginFixture(badCertificate);
                await badOrigin.Start();
                var rejected = 0;
                for (var i = 0; i < 3; i++)
                {
                    try
                    {
                        using var badClient = Client();
                        using var response = await badClient.GetAsync($"https://localhost:{badOrigin.Port}/echo");
                        if (!response.IsSuccessStatusCode) rejected++;
                    }
                    catch (HttpRequestException) { rejected++; }
                }
                Record("untrusted_origin_no_fallback", rejected == 3 && badOrigin.Requests == 0,
                    $"rejected={rejected}/3, origin_requests={badOrigin.Requests}");
            }

            worker.Kill();
            await Block("worker_failure_block", Request("safe after worker failure", inspectedVersion));
            Record("metadata_only_storage", MetadataClean(state), "database contains no synthetic payload or prompt text");

            if (!probeHttp2)
            {
                var stub = Path.Combine(state, "hung-worker", "aidlp");
                Directory.CreateDirectory(stub);
                File.WriteAllText(Path.Combine(stub, "__init__.py"), "");
                File.WriteAllText(Path.Combine(stub, "worker.py"), "import time\ntime.sleep(30)\n");
                using var hungWorker = new InspectorWorker(python, Path.GetDirectoryName(stub)!,
                    Path.Combine(state, "unused.db"));
                using var timeoutProxy = new DlpProxy(hungWorker, ["localhost"], 0, originCertificate, origin.Port);
                timeoutProxy.Start();
                using var timeoutClient = Client(selectedProxy: timeoutProxy);
                var before = origin.Requests;
                var timer = Stopwatch.StartNew();
                using var request = Request("safe but inspection stalls", HttpVersion.Version11);
                using var response = await timeoutClient.SendAsync(request);
                Record("worker_timeout_block", response.StatusCode == HttpStatusCode.Forbidden &&
                    origin.Requests == before && timer.Elapsed < TimeSpan.FromSeconds(9),
                    $"status={(int)response.StatusCode}, origin_delta={origin.Requests - before}, elapsed_ms={timer.ElapsedMilliseconds}");
            }
        }
        catch (Exception ex)
        {
            Record("unexpected_test_exception", false, ex.GetType().Name);
            if (Environment.GetEnvironmentVariable("AIDLP_TEST_DIAGNOSTICS") == "1")
                Console.Error.WriteLine(ex); // Synthetic fixtures only; never used by normal proxy mode.
        }
        var success = checks.All(c => c.Passed);
        var report = new
        {
            timestamp_utc = DateTimeOffset.UtcNow, success,
            runtime = System.Runtime.InteropServices.RuntimeInformation.FrameworkDescription,
            os = System.Runtime.InteropServices.RuntimeInformation.OSDescription,
            titanium = typeof(Titanium.Web.Proxy.ProxyServer).Assembly.GetName().Version?.ToString(),
            http2 = probeHttp2 ? "UNSAFE_REPRODUCTION" : "disabled_pending_upstream_fix",
            duration_ms = clock.ElapsedMilliseconds, allowed = proxy.Allowed, blocked = proxy.Blocked,
            opaque_tunnels = proxy.Tunnels, origin_requests = origin.Requests, checks,
            scope = "loopback synthetic fixtures; no OS proxy or trust changes; no real AI service test"
        };
        File.WriteAllText(Path.Combine(state, "self-test.json"),
            JsonSerializer.Serialize(report, new JsonSerializerOptions { WriteIndented = true }));
        Console.WriteLine($"RESULT {(success ? "PASS" : "FAIL")}: {checks.Count(c => c.Passed)}/{checks.Count}");
        return success ? 0 : 1;
    }

    private static bool MetadataClean(string state)
    {
        var forbidden = new[] { "alice@example.com", "900101-1234567", "SYNTHETIC_BODY", "safe synthetic hello" };
        return Directory.EnumerateFiles(state, "events.db*").All(path =>
        {
            var bytes = File.ReadAllBytes(path);
            var text = Encoding.UTF8.GetString(bytes);
            return forbidden.All(value => !text.Contains(value, StringComparison.Ordinal));
        });
    }
}
