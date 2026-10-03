using System.Net;
using System.Net.Security;
using System.Security.Cryptography.X509Certificates;
using System.Text;
using System.Text.Json;
using Microsoft.Extensions.Logging;
using Titanium.Web.Proxy;
using Titanium.Web.Proxy.EventArguments;
using Titanium.Web.Proxy.Models;

namespace AiDlp.Proxy;

internal sealed class DlpProxy : IDisposable
{
    public const int MaxBody = 1024 * 1024;
    private readonly ProxyServer server;
    private readonly ExplicitProxyEndPoint endpoint;
    private readonly HashSet<string> inspectHosts;
    private readonly InspectorWorker worker;
    private static readonly UTF8Encoding StrictUtf8 = new(false, true);
    public X509Certificate2 Root { get; }
    public int Port => endpoint.Port;
    public long Allowed;
    public long Blocked;
    public long Tunnels;

    public static string Normalize(string host) => host.TrimEnd('.').ToLowerInvariant();
    public bool Selects(string host) => inspectHosts.Contains(Normalize(host));

    public DlpProxy(InspectorWorker worker, IEnumerable<string> hosts, int port,
        X509Certificate2? testOrigin = null, int? testOriginPort = null, bool probeHttp2 = false)
    {
        this.worker = worker;
        inspectHosts = new HashSet<string>(hosts.Select(Normalize), StringComparer.Ordinal);
        if (inspectHosts.Count == 0 || inspectHosts.Any(h => Uri.CheckHostName(h) == UriHostNameType.Unknown))
            throw new ArgumentException("Provide exact DNS names or IP addresses, without wildcards.");
        Root = Certificates.Authority("AI DLP Ephemeral PoC " + Guid.NewGuid().ToString("N"));
        server = new ProxyServer(userTrustRootCertificate: false, machineTrustRootCertificate: false)
        {
            // 7.0.14's native H2 body path can forward before an async inspection finishes.
            // H2 is permitted ONLY in the isolated self-test reproduction, never normal mode.
            EnableHttp2 = probeHttp2 && testOrigin is not null, EnableHttpInterception = true,
            EnableDecryptFailureBypass = false, IgnoreServerCertificateErrors = false,
            ForwardToUpstreamGateway = false, MaxBufferedBodyBytes = MaxBody
        };
        server.CertificateManager.RootCertificate = Root;
        server.CertificateManager.SaveFakeCertificates = false;
        server.Logging.Enabled = testOrigin is not null &&
            Environment.GetEnvironmentVariable("AIDLP_TEST_DIAGNOSTICS") == "1";
        server.Logging.MinimumLevel = LogLevel.Debug;
        server.ApplyLoggingConfiguration();
        // Do not configure content/session loggers. No handler logs URLs, headers, or bodies.
        server.BeforeRequest += BeforeRequest;
        if (testOrigin is not null)
        {
            server.ServerCertificateValidationCallback += (_, e) =>
            {
                var uri = e.Session.HttpClient.Request.RequestUri;
                e.IsValid = e.SslPolicyErrors == SslPolicyErrors.None ||
                    (Normalize(uri.Host) == "localhost" && uri.Port == testOriginPort &&
                     e.Certificate?.GetCertHashString() == testOrigin.Thumbprint &&
                     (e.SslPolicyErrors & SslPolicyErrors.RemoteCertificateNameMismatch) == 0);
                return Task.CompletedTask;
            };
        }
        endpoint = new ExplicitProxyEndPoint(IPAddress.Loopback, port, decryptSsl: true);
        endpoint.BeforeTunnelConnectRequest += (_, e) =>
        {
            e.DecryptSsl = Selects(e.HttpClient.Request.RequestUri.Host);
            if (!e.DecryptSsl) Interlocked.Increment(ref Tunnels);
            return Task.CompletedTask;
        };
        server.AddEndPoint(endpoint);
    }

    public void Start() => server.Start();

    private async Task BeforeRequest(object sender, SessionEventArgs e)
    {
        try
        {
            var request = e.HttpClient.Request;
            var uri = request.RequestUri;
            if (!Selects(uri.Host))
            {
                // Opaque CONNECT tunnels never enter this handler. A different authority
                // inside an intercepted TLS connection must not bypass inspection.
                if (uri.Scheme == "https") Deny(e, "authority_not_selected");
                return;
            }
            if (uri.Scheme != "https") { Deny(e, "tls_required"); return; }
            if (request.UpgradeToWebSocket || request.Headers.HeaderExists("Upgrade"))
            { Deny(e, "unsupported_upgrade"); return; }
            if (request.Headers.HeaderExists("Content-Encoding"))
            { Deny(e, "unsupported_encoding"); return; }
            if (request.ContentLength > MaxBody)
            { Deny(e, "body_too_large", HttpStatusCode.RequestEntityTooLarge); return; }

            e.MaxBufferedBodyBytes = MaxBody;
            using var readTimeout = new CancellationTokenSource(TimeSpan.FromSeconds(10));
            var bytes = request.HasBody ? await e.GetRequestBody(readTimeout.Token) : [];
            if (bytes.Length > MaxBody)
            { Deny(e, "body_too_large", HttpStatusCode.RequestEntityTooLarge); return; }
            var body = StrictUtf8.GetString(bytes);
            var result = await worker.Inspect(Normalize(uri.Host), request.Method,
                uri.PathAndQuery, request.ContentType ?? "", body);
            if (result.Action != "allow") { Deny(e, result.Reason); return; }
            Interlocked.Increment(ref Allowed);
        }
        catch (Exception)
        {
            // Titanium catches callback exceptions; explicitly cancel forwarding instead.
            Deny(e, "inspection_error");
        }
    }

    private void Deny(SessionEventArgs e, string reason, HttpStatusCode status = HttpStatusCode.Forbidden)
    {
        Interlocked.Increment(ref Blocked);
        e.GenericResponse(JsonSerializer.Serialize(new { error = "aidlp_blocked", reason }), status);
    }

    public void Dispose()
    {
        server.Stop();
        server.Dispose();
        Root.Dispose();
    }
}
