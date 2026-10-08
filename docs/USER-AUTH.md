# User accounts and workspace access

## Sign-in flow

The web app uses Clerk for account registration and sign-in. Google is the preferred social connection. In a Clerk development instance, Google can use Clerk's shared development credentials; production requires a Google OAuth client configured in Clerk and the matching authorized redirect URI.

Clerk sign-in and registration pages are `/login` and `/register`. The dashboard is protected by Clerk middleware when Clerk keys are configured. API requests include the active Clerk session token. FastAPI verifies its signature against Clerk's JWKS, checks issuer/expiry/authorized party, and associates imported workspaces with the token's `sub` user ID.

## Local setup

1. Create a Clerk development application and enable Google under social connections.
2. Copy `apps/web/.env.example` to `apps/web/.env.local` and set `NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY` and `CLERK_SECRET_KEY` from Clerk.
3. Copy `apps/api/.env.example` to `apps/api/.env` and set `AUTH_REQUIRED=true`, `CLERK_ISSUER`, and `CLERK_JWKS_URL` from the Clerk instance. `CLERK_JWKS_URL` is the instance Frontend API URL followed by `/.well-known/jwks.json`.
4. Set `AUTHORIZED_PARTIES=http://localhost:3000` and keep `WEB_ORIGIN=http://localhost:3000` for local development.
5. Restart Next.js and FastAPI, then register at `http://localhost:3000/register`.

Never commit `.env.local` or `.env` files. In production, set `ENVIRONMENT=production` or explicitly set `AUTH_REQUIRED=true`; use the production Clerk instance, a production Google OAuth client, and the deployed web origin in `AUTHORIZED_PARTIES`/`WEB_ORIGIN`.

Existing guest workspaces have no owner. After enabling required authentication, sign in and import those repositories again to create user-owned workspaces.

## Provider scope

Google sign-in is the first supported account provider. Phone OTP is not enabled in the UI. Clerk's current free plan includes social connections, while SMS codes are a paid feature with per-message pricing; evaluate it separately before enabling phone registration. This account sign-in is independent from GitHub authorization for private repository access.
