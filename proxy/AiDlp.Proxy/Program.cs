using System.Text.Json;
using AiDlp.Proxy;

static string Option(string[] args, string name, string? fallback = null)
{
    var i = Array.IndexOf(args, name);
    if (i >= 0 && i + 1 < args.Length) return args[i + 1];
    return fallback ?? throw new ArgumentException("Missing " + name);
}

try
{
    var python = Path.GetFullPath(Option(args, "--python"));
    var agent = Path.GetFullPath(Option(args, "--agent-dir"));
    var state = Path.GetFullPath(Option(args, "--state-dir"));
    Directory.CreateDirectory(state);
    if (args.Contains("--self-test"))
        return await SelfTest.Run(python, agent, state, args.Contains("--probe-http2"));
    if (args.Contains("--probe-http2"))
        throw new ArgumentException("HTTP/2 probe is restricted to isolated self-test fixtures.");

    var hosts = Option(args, "--inspect-hosts", "localhost").Split(',', StringSplitOptions.RemoveEmptyEntries);
    var port = int.Parse(Option(args, "--port", "18080"));
    using var worker = new InspectorWorker(python, agent, Path.Combine(state, "events.db"));
    using var proxy = new DlpProxy(worker, hosts, port);
    proxy.Start();
    File.WriteAllText(Path.Combine(state, "ephemeral-ca.cer"), proxy.Root.ExportCertificatePem());
    Console.WriteLine(JsonSerializer.Serialize(new { status = "ready", port = proxy.Port,
        mode = "block", inspected_hosts = hosts, trust = "explicit-client-only",
        http2 = "disabled_pending_upstream_fix", scope = "PoC" }));
    var done = new TaskCompletionSource(TaskCreationOptions.RunContinuationsAsynchronously);
    Console.CancelKeyPress += (_, e) => { e.Cancel = true; done.TrySetResult(); };
    await done.Task;
    return 0;
}
catch (Exception ex)
{
    // Exception messages can include payloads/URLs. Emit type only.
    Console.Error.WriteLine("Startup failed: " + ex.GetType().Name);
    return 1;
}
