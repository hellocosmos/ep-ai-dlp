using System.Net;
using System.Security.Cryptography.X509Certificates;
using Microsoft.AspNetCore.Server.Kestrel.Core;

namespace AiDlp.Proxy;

internal sealed class OriginFixture : IAsyncDisposable
{
    private readonly WebApplication app;
    public int Port { get; private set; }
    public int Requests;
    public readonly TaskCompletionSource ReleaseSecondEvent =
        new(TaskCreationOptions.RunContinuationsAsynchronously);

    public OriginFixture(X509Certificate2 certificate)
    {
        var builder = WebApplication.CreateSlimBuilder();
        builder.Logging.ClearProviders();
        if (Environment.GetEnvironmentVariable("AIDLP_TEST_DIAGNOSTICS") == "1")
            builder.Logging.AddConsole().SetMinimumLevel(LogLevel.Debug);
        builder.WebHost.ConfigureKestrel(options =>
            options.Listen(IPAddress.Loopback, 0, listen =>
            {
                listen.Protocols = HttpProtocols.Http1AndHttp2;
                listen.UseHttps(certificate);
            }));
        app = builder.Build();
        app.Run(async context =>
        {
            Interlocked.Increment(ref Requests);
            if (context.Request.Path == "/sse")
            {
                context.Response.ContentType = "text/event-stream";
                await context.Response.WriteAsync("data: first\n\n");
                await context.Response.Body.FlushAsync();
                await ReleaseSecondEvent.Task.WaitAsync(TimeSpan.FromSeconds(10));
                await context.Response.WriteAsync("data: second\n\n");
                await context.Response.Body.FlushAsync();
                return;
            }
            using var reader = new StreamReader(context.Request.Body);
            var body = await reader.ReadToEndAsync();
            await context.Response.WriteAsJsonAsync(new { body, protocol = context.Request.Protocol });
        });
    }

    public async Task Start()
    {
        await app.StartAsync();
        Port = new Uri(app.Urls.Single()).Port;
    }

    public async ValueTask DisposeAsync()
    {
        ReleaseSecondEvent.TrySetResult();
        await app.StopAsync();
        await app.DisposeAsync();
    }
}
