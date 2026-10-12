# Deployment architecture

## Recommended first production setup

Deploy the Next.js web app to Vercel with the project root set to `apps/web`. Deploy the FastAPI API to a separate single-instance Linux host that provides a durable writable volume and a reachable Docker daemon. Keep the API's `WORKSPACE_ROOT` on that durable volume. MongoDB Atlas stores workspace metadata; it does not store repository files or implementation worktrees.

The current API cannot run as a normal Vercel Function without changing its storage and verification architecture. Vercel Functions have read-only filesystems apart from temporary `/tmp` storage, while Agent Lab writes imported repositories, Git worktrees, and implementation drafts to `WORKSPACE_ROOT`. Repository verification also invokes Docker. Keep those workloads on the persistent API host until workspace files move to shared object storage and verification runs in a separately managed sandbox service.

## Vercel web project

- Framework preset: Next.js.
- Root directory: `apps/web`.
- Install command: `pnpm install --frozen-lockfile`.
- Build command: `pnpm build`.
- Set these project environment variables for Production and Preview as appropriate:
  - `NEXT_PUBLIC_API_BASE_URL`: public HTTPS origin of the FastAPI service.
  - `NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY`: Clerk publishable key for the selected instance.
  - `CLERK_SECRET_KEY`: matching Clerk secret key; server-only.
- Add the Vercel production and preview origins to Clerk's allowed redirect/origin configuration.

## FastAPI host

Configure the API process with:

- `ENVIRONMENT=production`
- `AUTH_REQUIRED=true`
- `WORKSPACE_ROOT`: explicit durable mounted directory, writable by the API process.
- `MONGODB_URI` and `MONGODB_DATABASE=agent_lab`
- `GEMINI_API_KEY` and optionally `GEMINI_MODEL`
- `CLERK_ISSUER` and `CLERK_JWKS_URL`
- `WEB_ORIGIN` and `AUTHORIZED_PARTIES`: comma-separated exact Vercel production/preview origins that are allowed to call the API.
- `VERIFICATION_ALLOW_DEPENDENCY_NETWORK=false` unless the operator explicitly accepts Docker bridge egress for trusted repositories.

The API host needs Git for public repository import and a working Docker CLI/daemon for verification. Use one API instance: local workspace files are not shared across instances. `/health` checks the process; `/ready` reports dependency readiness without returning credentials.

Restrict MongoDB Atlas network access to the API host's egress IP when stable, use TLS, and keep all secret values in the hosting providers' secret managers. Do not put backend credentials in Vercel's `NEXT_PUBLIC_*` variables.

## Before enabling public traffic

The current product has no durable object-storage adapter, background job queue, request rate limiting, or multi-instance workspace coordination. Keep the API single-instance and access-controlled until those pieces are implemented. Review data retention and backups for the persistent workspace volume and MongoDB before inviting users to upload private repositories.
