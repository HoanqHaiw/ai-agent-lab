import { clerkMiddleware } from "@clerk/nextjs/server";
import { NextResponse, type NextFetchEvent, type NextRequest } from "next/server";

const clerkEnabled = Boolean(process.env.NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY && process.env.CLERK_SECRET_KEY);
const clerkHandler = clerkEnabled
  ? clerkMiddleware(async (auth, request) => {
      const path = request.nextUrl.pathname;
      const publicPath = path === "/login" || path.startsWith("/login/") || path === "/register" || path.startsWith("/register/");
      if (!publicPath) await auth.protect();
    }, { signInUrl: "/login" })
  : null;

export default function middleware(request: NextRequest, event: NextFetchEvent) {
  return clerkHandler ? clerkHandler(request, event) : NextResponse.next();
}

export const config = {
  matcher: ["/((?!_next|[^?]*\\.(?:html?|css|js(?!on)|jpe?g|webp|png|gif|svg|ttf|woff2?|ico|csv|docx?|xlsx?|zip|webmanifest)).*)", "/(api|trpc)(.*)"],
};
