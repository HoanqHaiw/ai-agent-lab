import Link from "next/link";
import { SignIn } from "@clerk/nextjs";

export default function LoginPage() {
  const configured = Boolean(process.env.NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY && process.env.CLERK_SECRET_KEY);
  const setupMessage = process.env.VERCEL
    ? "Add NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY and CLERK_SECRET_KEY in Vercel Project Settings → Environment Variables for Production and Preview, then redeploy. Also allow the Vercel domain in Clerk."
    : "Add NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY and CLERK_SECRET_KEY to apps/web/.env.local, then restart Next.js. Configure Google as a social connection in Clerk.";
  return <main className="auth-page">
    <section className="auth-card">
      <Link className="brand auth-brand" href="/">✳ agent<span>lab</span></Link>
      <p className="eyebrow">WELCOME BACK</p>
      <h1>Sign in to Agent Lab</h1>
      <p className="auth-copy">Sign in with Google or another provider enabled for this Clerk application. Guest workspaces saved in this browser will be linked to your account after sign-in.</p>
      {configured ? <SignIn routing="path" path="/login" signUpUrl="/register" /> : <div className="auth-setup"><strong>Authentication needs setup</strong><p>{setupMessage}</p></div>}
      <p className="auth-switch">New here? <Link href="/register">Create an account</Link></p>
    </section>
  </main>;
}
