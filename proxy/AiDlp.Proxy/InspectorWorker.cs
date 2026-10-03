using System.Diagnostics;
using System.Text;
using System.Text.Json;

namespace AiDlp.Proxy;

internal sealed record Decision(string Action, string Reason);

internal sealed class InspectorWorker : IDisposable
{
    private readonly Process process;
    private readonly SemaphoreSlim gate = new(1, 1);
    private readonly Task stderrDrain;
    public int ProcessId => process.Id;

    public InspectorWorker(string python, string agentDirectory, string database)
    {
        var start = new ProcessStartInfo(python)
        {
            UseShellExecute = false, RedirectStandardInput = true,
            RedirectStandardOutput = true, RedirectStandardError = true,
            CreateNoWindow = true, StandardInputEncoding = new UTF8Encoding(false),
            StandardOutputEncoding = Encoding.UTF8,
            WorkingDirectory = agentDirectory
        };
        foreach (var arg in new[] { "-u", "-m", "aidlp.worker", "--db", database })
            start.ArgumentList.Add(arg);
        start.Environment["PYTHONPATH"] = agentDirectory;
        start.Environment["PYTHONIOENCODING"] = "utf-8";
        start.Environment["PYTHONDONTWRITEBYTECODE"] = "1";
        process = Process.Start(start) ?? throw new InvalidOperationException("Worker start failed");
        // Drain without persisting stack traces or accidental request contents.
        stderrDrain = Task.Run(async () =>
        {
            var buffer = new char[1024];
            while (await process.StandardError.ReadAsync(buffer) != 0) { }
        });
    }

    public async Task<Decision> Inspect(string host, string method, string path, string contentType, string body)
    {
        using var timeout = new CancellationTokenSource(TimeSpan.FromSeconds(5));
        var entered = false;
        try
        {
            await gate.WaitAsync(timeout.Token);
            entered = true;
            if (process.HasExited) return new("block", "inspector_unavailable");
            var id = Guid.NewGuid().ToString("N");
            var line = JsonSerializer.Serialize(new { id, host, method, path, content_type = contentType, body });
            await process.StandardInput.WriteLineAsync(line.AsMemory(), timeout.Token);
            await process.StandardInput.FlushAsync(timeout.Token);
            var answer = await process.StandardOutput.ReadLineAsync(timeout.Token);
            if (answer is null || answer.Length > 8192) throw new InvalidDataException();
            using var result = JsonDocument.Parse(answer);
            var root = result.RootElement;
            if (root.GetProperty("id").GetString() != id) throw new InvalidDataException();
            var action = root.GetProperty("action").GetString();
            var reason = root.GetProperty("reason").GetString();
            // Only an explicit clean decision with a committed event permits forwarding.
            if (action == "allow" && reason == "clean" &&
                root.GetProperty("event_id").TryGetInt64(out var eventId) && eventId > 0)
                return new("allow", "clean");
            return new("block", reason == "sensitive_data" ? reason : "uninspectable_request");
        }
        catch (Exception)
        {
            // Kill a desynchronized/hung worker; never consume a late reply for another request.
            if (entered) Kill();
            return new("block", "inspection_error");
        }
        finally
        {
            if (entered) gate.Release();
        }
    }

    public void Kill()
    {
        try { if (!process.HasExited) process.Kill(entireProcessTree: true); }
        catch (InvalidOperationException) { }
    }

    public void Dispose()
    {
        Kill();
        process.WaitForExit(3000);
        try { stderrDrain.Wait(TimeSpan.FromSeconds(2)); } catch (AggregateException) { }
        process.Dispose();
        gate.Dispose();
    }
}
