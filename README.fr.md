# EP AI DLP — Protection des données IA sur les terminaux

**Explorer comment inspecter les données sensibles avant leur envoi par une application Windows vers un service d’IA.**

[English](README.md) · [한국어](README.ko.md) · [简体中文](README.zh-CN.md) · [日本語](README.ja.md) · [Español](README.es.md) · [Français](README.fr.md)

> Aperçu de recherche expérimental. Il ne remplace pas un DLP de production et ne garantit pas une prévention complète des fuites. Le README anglais fait référence ; la console est en anglais.

Console réelle avec des données synthétiques. Les noms, compteurs et états illustrent l’interface ; ils ne constituent ni des usages clients ni des preuves de blocage en conditions réelles.

![Console overview](docs/assets/console-overview.png)

<table><tr><td width="50%"><img src="docs/assets/console-policy.png" alt="Policy"></td><td width="50%"><img src="docs/assets/console-devices.png" alt="Devices"></td></tr></table>

![Inspection events](docs/assets/console-events-dark.png)

## Fonctions implémentées

- Moteur Rust : capture TCP sélective, inspection TLS, analyse bornée des requêtes et détection déterministe.
- Service Windows capturant les nouveaux processus correspondant aux chemins configurés, sans profil de navigateur dédié ni option de proxy dans ce mode.
- Onze règles : motifs d’identifiants, marqueurs de clés privées, e-mails, cartes bancaires, numéros mobiles et identifiants de résident coréens.
- Inscription à usage unique, politiques Ed25519 liées au terminal, blocage ou surveillance par règle et remontée de la version appliquée.
- Console Next.js, NestJS et PostgreSQL ; événements de métadonnées sans le texte protégé.

Un seul tenant et une politique commune. Domaines et exécutables restent configurés sur l’agent. L’inspection des réponses, l’extraction de fichiers, la classification contextuelle et Agent IAM ne sont pas inclus.

```text
Windows application -> TCP capture -> Rust TLS / DLP -> AI destination
                           ^
                  signed policy / metadata
                           |
              Next.js -> NestJS -> PostgreSQL
```

## Démarrer la console locale

Node.js 22.19+, npm et Docker Compose sont nécessaires. Démarrer la console ne protège pas automatiquement un terminal.

```bash
git clone https://github.com/hellocosmos/ep-ai-dlp.git
cd ep-ai-dlp
npm ci
npm run bootstrap
docker compose --env-file .local/managed.env -f deploy/compose.yaml up -d
npm run build
```

Lancer `npm run dev:server` et `npm run start:console` dans deux terminaux, puis ouvrir `http://127.0.0.1:3100`. Les identifiants aléatoires sont générés dans `.local/managed.env`, exclu de Git.

## Preuves et limites

Sur une destination HTTPS contrôlée, les requêtes normales sont arrivées et les requêtes sensibles synthétiques bloquées n’ont pas augmenté le compteur de réception. Chrome standard a été testé après redémarrage du service, ainsi qu’une conversation bénigne avec ChatGPT. Cela ne démontre pas une compatibilité générale.

**Un possible faux positif sur une requête auxiliaire ChatGPT reste inexpliqué. Fichiers réels, autres services et navigateurs, notification de blocage, performances, redémarrage et reprise après panne, et résistance à l’altération nécessitent des validations. Une panne peut libérer la capture : le blocage continu en cas de défaillance n’est pas établi. Pas de capture macOS ni de journal d’audit immuable.**

## Pourquoi publier

Conçu et validé par Jaemyung Kim, architecte de produits de cybersécurité, avec une implémentation assistée par des agents de programmation IA. Le projet partage architecture, modèle de menace, critères d’acceptation et limites.

Le code original est sous MIT ; Windows redirector, WinDivert et les autres composants conservent leurs licences. Les anciens essais Python et .NET restent des chemins distincts. Identifiants opérationnels, preuves brutes et documents commerciaux internes sont exclus.

[English reference](README.md) · [Setup](docs/getting-started.md) · [Windows lab](docs/windows-lab.md) · [Architecture](docs/architecture.md) · [Validation](docs/validation.md) · [Screenshots](docs/screenshot-provenance.md) · [Security](SECURITY.md) · [Licenses](THIRD_PARTY.md)
