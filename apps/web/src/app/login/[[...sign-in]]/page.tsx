import Link from "next/link";
import { SignIn } from "@clerk/nextjs";

export default function LoginPage() {
  const configured = Boolean(process.env.NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY && process.env.CLERK_SECRET_KEY);
  return <main className="auth-page">
    <section className="auth-card">
      <Link className="brand auth-brand" href="/">✳ agent<span>lab</span></Link>
      <p className="eyebrow">WELCOME BACK</p>
      <h1>Sign in to Agent Lab</h1>
      <p className="auth-copy">Sign in with Google or another provider enabled for this Clerk application.</p>
      {configured ? <SignIn routing="path" path="/login" signUpUrl="/register" /> : <div className="auth-setup"><strong>Authentication needs setup</strong><p>Add `NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY` and `CLERK_SECRET_KEY` to `apps/web/.env.local`, then restart Next.js. Configure Google as a social connection in your Clerk development instance.</p></div>}
      <p className="auth-switch">New here? <Link href="/register">Create an account</Link></p>
    </section>
  </main>;
}
