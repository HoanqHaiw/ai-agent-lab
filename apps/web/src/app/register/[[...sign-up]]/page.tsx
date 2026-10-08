import Link from "next/link";
import { SignUp } from "@clerk/nextjs";

export default function RegisterPage() {
  const configured = Boolean(process.env.NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY && process.env.CLERK_SECRET_KEY);
  return <main className="auth-page">
    <section className="auth-card">
      <Link className="brand auth-brand" href="/">✳ agent<span>lab</span></Link>
      <p className="eyebrow">CREATE ACCOUNT</p>
      <h1>Join Agent Lab</h1>
      <p className="auth-copy">Create an account using Google or another provider enabled for this Clerk application.</p>
      {configured ? <SignUp routing="path" path="/register" signInUrl="/login" /> : <div className="auth-setup"><strong>Authentication needs setup</strong><p>Add `NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY` and `CLERK_SECRET_KEY` to `apps/web/.env.local`, then restart Next.js. Configure Google as a social connection in your Clerk development instance.</p></div>}
      <p className="auth-switch">Already registered? <Link href="/login">Sign in</Link></p>
    </section>
  </main>;
}
