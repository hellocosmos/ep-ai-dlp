using System.Net;
using System.Security.Cryptography;
using System.Security.Cryptography.X509Certificates;

namespace AiDlp.Proxy;

internal static class Certificates
{
    public static X509Certificate2 Authority(string name)
    {
        using var key = RSA.Create(2048);
        var request = new CertificateRequest($"CN={name}", key, HashAlgorithmName.SHA256,
            RSASignaturePadding.Pkcs1);
        request.CertificateExtensions.Add(new X509BasicConstraintsExtension(true, false, 0, true));
        request.CertificateExtensions.Add(new X509KeyUsageExtension(
            X509KeyUsageFlags.KeyCertSign | X509KeyUsageFlags.CrlSign, true));
        return request.CreateSelfSigned(DateTimeOffset.UtcNow.AddMinutes(-5), DateTimeOffset.UtcNow.AddDays(2));
    }

    public static X509Certificate2 Localhost(X509Certificate2 authority)
    {
        using var key = RSA.Create(2048);
        var request = new CertificateRequest("CN=localhost", key, HashAlgorithmName.SHA256,
            RSASignaturePadding.Pkcs1);
        request.CertificateExtensions.Add(new X509BasicConstraintsExtension(false, false, 0, true));
        request.CertificateExtensions.Add(new X509KeyUsageExtension(X509KeyUsageFlags.DigitalSignature, true));
        request.CertificateExtensions.Add(new X509EnhancedKeyUsageExtension(
            new OidCollection { new("1.3.6.1.5.5.7.3.1") }, true));
        var san = new SubjectAlternativeNameBuilder();
        san.AddDnsName("localhost");
        san.AddIpAddress(IPAddress.Loopback);
        request.CertificateExtensions.Add(san.Build());
        using var cert = request.Create(authority, DateTimeOffset.UtcNow.AddMinutes(-2),
            DateTimeOffset.UtcNow.AddDays(1), RandomNumberGenerator.GetBytes(16));
        using var withKey = cert.CopyWithPrivateKey(key);
        // Windows Schannel cannot serve TLS with ephemeral private-key handles.
        // Import into a temporary user key container, deleted on certificate disposal.
        // Do not set PersistKeySet or add the certificate to any certificate store.
        return X509CertificateLoader.LoadPkcs12(withKey.Export(X509ContentType.Pkcs12), null,
            X509KeyStorageFlags.UserKeySet);
    }
}
