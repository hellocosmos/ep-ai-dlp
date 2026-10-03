# Local management console

Requirements: Node.js 22.19+, npm, Docker Engine/Desktop with Compose. Rust
runtime checks additionally require Rust 1.95+, a C/C++ toolchain and `protoc`.

```bash
git clone https://github.com/hellocosmos/ep-ai-dlp.git
cd ep-ai-dlp
npm ci
npm run bootstrap
docker compose --env-file .local/managed.env -f deploy/compose.yaml up -d
npm run build
```

Bootstrap creates random administrator/database credentials and a policy signing
key under `.local/`. Existing configuration is preserved; no shared default
password is used. Retrieve your administrator credentials locally from the
protected `.local/managed.env` file. Do not paste or publish that file.

In separate terminals, from the repository root:

```bash
npm run dev:server
```

```bash
npm run start:console
```

Open [the local console](http://127.0.0.1:3100). A new installation has no enrolled
devices and no inspection events. README screenshots use a synthetic fixture;
these examples are not automatically inserted into your database.

The API defaults to loopback port 3901; PostgreSQL is bound to loopback port
55438. Review `deploy/compose.yaml` before starting it if another installation
uses these ports. The Compose file starts PostgreSQL only, not the API, console,
or a Windows agent. Remote device enrollment requires a reachable, validated
HTTPS management endpoint; loopback demo URLs are not a remote deployment.

## Server tests

After bootstrap and PostgreSQL startup, run `npm run test:server`. Tests create
and remove a uniquely named test schema. Use a dedicated development database.
Never point test commands at a customer database.

## Windows

Read [Windows lab setup](windows-lab.md) and [security boundaries](../SECURITY.md)
before enrolling a device. Starting this console does not protect any endpoint.
