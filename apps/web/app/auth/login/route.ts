import { NextResponse } from 'next/server';
import { agentJson } from '@/lib/agent';

/**
 * Kick off the OIDC sign-in.
 *
 * The agent builds the authorize URL (with PKCE + state + nonce) and stashes
 * the verifier server-side. We just bounce the browser through.
 */
export async function GET(request: Request) {
  const url = new URL(request.url);
  const returnTo = url.searchParams.get('return_to') ?? '/';
  try {
    const { authorize_url } = await agentJson<{ authorize_url: string }>(
      `/auth/signin-url?return_to=${encodeURIComponent(returnTo)}`,
    );
    return NextResponse.redirect(authorize_url, 302);
  } catch (err) {
    const detail = err instanceof Error ? err.message : 'unknown';
    return NextResponse.redirect(
      new URL(`/?error=signin_start_failed&detail=${encodeURIComponent(detail)}`, url),
      302,
    );
  }
}
